#!/usr/bin/env python3
"""Optional PySide6 layout manager; importable without Linux runtime modules."""
from __future__ import annotations

import argparse
import configparser
from copy import deepcopy
from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QTabWidget, QVBoxLayout, QWidget, QDoubleSpinBox,
)

from dialpad_layout import (
    discover_layouts, dumps_layout, normalize_document, parse_json, parse_python,
    resolve_layout, revision_bytes, save_layout, user_layout_path, validate_identifier,
)
from dialpad_layout_editor import (
    ActionDialog, DIRECTIONS, DocumentDraft, FunctionDialog, GEOMETRY_LABELS,
    GeometryCanvas, RingPreview, action_items, button, command_fields,
    function_names, move_ordered, put_actions, rename_ordered,
)


def read_manager_config(config_dir):
    """Read-only portable config access; all writes belong to the Linux adapter."""
    parser = configparser.ConfigParser(interpolation=None)
    path = Path(config_dir) / "dialpad_dev"
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    if not parser.has_section("main"):
        parser.add_section("main")
    return parser


def parse_source(path, data):
    return parse_json(data) if Path(path).suffix.lower() == ".json" else parse_python(data, str(path))


@dataclass(frozen=True)
class PendingRemoval:
    operation: str
    original_path: Path
    original_identifier: str
    original_revision: str
    replacement_identifier: str
    replacement_revision: str
    replacement_path: Path
    instance_id: str
    generation: int


def replacement_acknowledged(status, pending):
    if not status or status.get("instance_id") != pending.instance_id or status.get("state") != "applied":
        return False
    if status.get("generation", -1) < pending.generation:
        return False
    for field in ("requested", "applied"):
        identity = status.get(field) or {}
        if (identity.get("identifier") != pending.replacement_identifier
                or identity.get("revision") != pending.replacement_revision):
            return False
    return True


class LayoutManagerWindow(QMainWindow):
    """A saved revision is never presented as applied without runtime evidence."""

    def __init__(self, config_dir, install_dir=None, *, offline=False, runtime_adapter=None, poll_interval=1000):
        super().__init__()
        self.setObjectName("layoutManager")
        self.setWindowTitle("DialPad Layout Manager")
        self.resize(1200, 820)
        self.config_dir = Path(config_dir).expanduser().resolve()
        self.install_dir = Path(install_dir or Path(__file__).parent).expanduser().resolve()
        self.offline = offline or not sys.platform.startswith("linux")
        self._adapter = runtime_adapter
        self.runtime_status = None
        self.runtime_error = "Offline editing — activation is unavailable"
        self.configuration = configparser.ConfigParser(interpolation=None)
        self.config_error = None
        self.sources = []
        self.source = None
        self.source_revision = None
        self.draft = None
        self.pending_removal = None
        self.activation_request = None
        self._changing = False
        self._loading = False
        self._suggested_identifier = "my_layout"
        self._edit_widgets = []
        self._build_ui()
        self._read_configuration()
        self.refresh_layouts()
        configured = self.configuration.get("main", "layout", fallback="").strip()
        initial = next((source for source in self.sources if source.identifier == configured), None)
        if initial is None and self.sources:
            initial = self.sources[0]
        if initial:
            self.open_source(initial)
        else:
            self._update_state()
        self.timer = QTimer(self)
        self.timer.setInterval(poll_interval)
        self.timer.timeout.connect(self.poll_runtime)
        if not self.offline:
            self.timer.start()
            QTimer.singleShot(0, self.poll_runtime)

    def _build_ui(self):
        root = QWidget(self)
        outer = QVBoxLayout(root)
        self.config_label = QLabel(f"Configuration: {self.config_dir}", self)
        self.config_label.setObjectName("configDirectory")
        self.config_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.config_label.setWordWrap(True)
        outer.addWidget(self.config_label)
        self.runtime_label = QLabel(self)
        self.runtime_label.setObjectName("runtimeStatus")
        self.runtime_label.setWordWrap(True)
        self.runtime_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.runtime_label)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        outer.addWidget(splitter, 1)
        library = QWidget(splitter)
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(0, 0, 8, 0)
        library_layout.addWidget(QLabel("Shortcut presets"))
        self.layout_filter = QLineEdit(self)
        self.layout_filter.setObjectName("layoutSearch")
        self.layout_filter.setPlaceholderText("Filter identifiers / provenance")
        self.layout_filter.textChanged.connect(self._filter_layouts)
        library_layout.addWidget(self.layout_filter)
        self.layout_list = QListWidget(self)
        self.layout_list.setObjectName("layoutList")
        self.layout_list.currentItemChanged.connect(self._select_layout)
        library_layout.addWidget(self.layout_list, 1)
        row = QHBoxLayout()
        self.new_button = button("New", "newLayout", self.new_layout)
        self.copy_button = button("Copy…", "copyLayout", self.copy_layout)
        row.addWidget(self.new_button)
        row.addWidget(self.copy_button)
        library_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("Import…", "importLayout", self.import_layout))
        self.export_button = button("Export…", "exportLayout", self.export_layout)
        row.addWidget(self.export_button)
        library_layout.addLayout(row)
        row = QHBoxLayout()
        self.rename_button = button("Rename…", "renameLayout", self.rename_layout)
        self.delete_button = button("Delete…", "deleteLayout", self.delete_layout)
        row.addWidget(self.rename_button)
        row.addWidget(self.delete_button)
        library_layout.addLayout(row)
        library_layout.addWidget(button("Refresh library", "refreshLayouts", self.refresh_layouts))
        editor = QWidget(splitter)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        self.source_label = QLabel("Select a layout or create a new preset.", self)
        self.source_label.setObjectName("sourceProvenance")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(self.source_label)
        title_row = QFormLayout()
        self.display_name = QLineEdit(self)
        self.display_name.setObjectName("displayName")
        self.display_name.setPlaceholderText("Optional display name; never a file path")
        self.display_name.textEdited.connect(self._name_edited)
        title_row.addRow("Display name", self.display_name)
        editor_layout.addLayout(title_row)
        self.revision_label = QLabel(self)
        self.revision_label.setObjectName("revisionState")
        self.revision_label.setWordWrap(True)
        editor_layout.addWidget(self.revision_label)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("editorTabs")
        editor_layout.addWidget(self.tabs, 1)
        self._build_rules_tab()
        self._build_geometry_tab()
        self._build_raw_tab()
        self.validation_label = QLabel(self)
        self.validation_label.setObjectName("validationIssues")
        self.validation_label.setWordWrap(True)
        self.validation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(self.validation_label)
        self.pending_label = QLabel(self)
        self.pending_label.setObjectName("pendingOperation")
        self.pending_label.setWordWrap(True)
        editor_layout.addWidget(self.pending_label)
        self.cancel_pending_button = button("Cancel pending rename / deletion", "cancelPendingOperation", self.cancel_pending)
        editor_layout.addWidget(self.cancel_pending_button)
        row = QHBoxLayout()
        self.trust_button = button("Trust Python and convert…", "trustedConversion", self.trusted_conversion)
        row.addWidget(self.trust_button)
        row.addStretch()
        self.reload_button = button("Reload file", "reloadLayout", self.reload_layout)
        row.addWidget(self.reload_button)
        row.addWidget(button("Validate", "validateLayout", self.validate_current))
        self.save_button = button("Save", "saveLayout", self.save_current)
        row.addWidget(self.save_button)
        self.activate_button = button("Activate saved revision", "activateLayout", self.activate_current)
        row.addWidget(self.activate_button)
        editor_layout.addLayout(row)
        splitter.setSizes([265, 935])
        self.setCentralWidget(root)
        self.statusBar().showMessage("Static editing and previews never execute layout commands")
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_current)
        self.addAction(save_action)
        self.setStyleSheet("QGroupBox { margin-top: 0.7em; } QGroupBox::title { subcontrol-origin: margin; left: 8px; } "
                           "QPushButton { padding: 5px 9px; } QPlainTextEdit { font-family: monospace; }")

    def _edit_button(self, text, name, callback):
        widget = button(text, name, callback)
        self._edit_widgets.append(widget)
        return widget

    def _build_rules_tab(self):
        tab = QWidget(self)
        root = QVBoxLayout(tab)
        note = QLabel("Rules are tested in their stored order. ‘none’ is the fallback; an empty rule intentionally has no bindings.")
        note.setWordWrap(True)
        root.addWidget(note)
        split = QSplitter(Qt.Orientation.Horizontal, tab)
        root.addWidget(split, 1)
        rules = QWidget(split)
        rule_box = QVBoxLayout(rules)
        rule_box.setContentsMargins(0, 0, 6, 0)
        self.app_list = QListWidget(self)
        self.app_list.setObjectName("applicationRules")
        self.app_list.currentItemChanged.connect(self._rule_selected)
        rule_box.addWidget(self.app_list)
        row = QHBoxLayout()
        row.addWidget(self._edit_button("Add", "addApplication", self.add_rule))
        row.addWidget(self._edit_button("Rename", "renameApplication", self.rename_rule))
        row.addWidget(self._edit_button("Remove", "removeApplication", self.remove_rule))
        rule_box.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._edit_button("Move up", "applicationUp", lambda: self.move_rule(-1)))
        row.addWidget(self._edit_button("Move down", "applicationDown", lambda: self.move_rule(1)))
        rule_box.addLayout(row)
        bindings = QWidget(split)
        right = QVBoxLayout(bindings)
        right.setContentsMargins(6, 0, 0, 0)
        self.mode = QComboBox(self)
        self.mode.setObjectName("functionMode")
        self.mode.addItem("Single function / shared controls", "direct")
        self.mode.addItem("Multifunction / named functions", "functions")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        right.addWidget(self.mode)
        self.mode_label = QLabel(self)
        self.mode_label.setWordWrap(True)
        self.mode_label.setObjectName("mappingModeDescription")
        right.addWidget(self.mode_label)
        self.function_panel = QWidget(self)
        function_box = QVBoxLayout(self.function_panel)
        function_box.setContentsMargins(0, 0, 0, 0)
        self.function_label = QLabel("Named functions · tool-ring entries · stored order runs clockwise from the top", self)
        self.function_label.setObjectName("functionListLabel")
        self.function_label.setWordWrap(True)
        function_box.addWidget(self.function_label)
        self.function_list = QListWidget(self)
        self.function_list.setObjectName("functionList")
        self.function_list.setMaximumHeight(130)
        self.function_list.currentItemChanged.connect(self._function_selected)
        function_box.addWidget(self.function_list)
        row = QHBoxLayout()
        row.addWidget(self._edit_button("Add function", "addFunction", self.add_function))
        row.addWidget(self._edit_button("Rename", "renameFunction", self.rename_function))
        row.addWidget(self._edit_button("Remove", "removeFunction", self.remove_function))
        row.addWidget(self._edit_button("Up", "functionUp", lambda: self.move_function(-1)))
        row.addWidget(self._edit_button("Down", "functionDown", lambda: self.move_function(1)))
        function_box.addLayout(row)
        self.function_metadata_button = self._edit_button("Command / display metadata…", "editFunctionMetadata", self.edit_function_metadata)
        row = QHBoxLayout()
        row.addWidget(self.function_metadata_button)
        self.preview_ring_button = button("Preview tool ring", "previewToolRing", self.show_ring_preview)
        self.preview_ring_button.setToolTip("Show the static tool-ring appearance in the Geometry & static preview tab. Commands and value queries are not run.")
        row.addWidget(self.preview_ring_button)
        row.addStretch()
        function_box.addLayout(row)
        right.addWidget(self.function_panel)
        self.direction = QComboBox(self)
        self.direction.setObjectName("actionDirection")
        for text, key in [("Center button", "center"), ("Clockwise rotation", "clockwise"), ("Counterclockwise rotation", "counterclockwise")]:
            self.direction.addItem(text, key)
        self.direction.currentIndexChanged.connect(self._refresh_actions)
        right.addWidget(self.direction)
        self.action_list = QListWidget(self)
        self.action_list.setObjectName("actionList")
        self.action_list.setMinimumHeight(110)
        self.action_list.itemDoubleClicked.connect(lambda _item: self.edit_action())
        right.addWidget(self.action_list, 1)
        row = QHBoxLayout()
        row.addWidget(self._edit_button("Add action…", "addAction", self.add_action))
        row.addWidget(self._edit_button("Edit…", "editAction", self.edit_action))
        row.addWidget(self._edit_button("Remove", "removeAction", self.remove_action))
        row.addWidget(self._edit_button("Up", "actionUp", lambda: self.move_action(-1)))
        row.addWidget(self._edit_button("Down", "actionDown", lambda: self.move_action(1)))
        right.addLayout(row)
        precedence = QLabel("Modifier-specific alternatives take precedence; order is retained within each precedence group.")
        precedence.setWordWrap(True)
        right.addWidget(precedence)
        split.setSizes([230, 530])
        self.tabs.addTab(tab, "Application rules & shortcuts")

    def _build_geometry_tab(self):
        tab = QWidget(self)
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(tab)
        scroll.setObjectName("geometryScroll")
        scroll.setWidgetResizable(True)
        self.geometry_scroll = scroll
        content = QWidget(scroll)
        layout = QHBoxLayout(content)
        left = QVBoxLayout()
        geometry_box = QGroupBox("Hardware template · absolute touchpad coordinates", self)
        form = QFormLayout(geometry_box)
        self.geometry_spins = {}
        for name, label in GEOMETRY_LABELS.items():
            spin = QDoubleSpinBox(self)
            spin.setObjectName(name)
            spin.setDecimals(6)
            spin.setRange(-1e12 if name in ("circle_center_x", "circle_center_y") else 0, 1e12)
            spin.valueChanged.connect(lambda value, key=name: self._geometry_edited(key, value))
            self.geometry_spins[name] = spin
            form.addRow(label, spin)
            self._edit_widgets.append(spin)
        left.addWidget(geometry_box)
        instructions = QLabel("Move the circle by dragging its interior. Square handles resize the outer circle, center button, and activation region. The activation region stays anchored at the top right.\n\nDevice information, when available, validates the template; the schematic is not calibration. Overlay appearance is independent of touchpad geometry.")
        instructions.setWordWrap(True)
        left.addWidget(instructions)
        self.ring = RingPreview(self)
        left.addWidget(self.ring, 1)
        layout.addLayout(left, 1)
        self.geometry_canvas = GeometryCanvas(self)
        self.geometry_canvas.geometryChanged.connect(self._canvas_edited)
        self._edit_widgets.append(self.geometry_canvas)
        layout.addWidget(self.geometry_canvas, 2)
        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        self.geometry_tab = tab
        self.tabs.addTab(tab, "Geometry & static preview")

    def _build_raw_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.raw_note = QLabel("Raw changes remain a separate draft until Apply succeeds. Invalid JSON never replaces the form or saved file.")
        self.raw_note.setWordWrap(True)
        layout.addWidget(self.raw_note)
        self.raw_editor = QPlainTextEdit(self)
        self.raw_editor.setObjectName("rawJsonEditor")
        self.raw_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_editor.textChanged.connect(self._raw_edited)
        layout.addWidget(self.raw_editor)
        row = QHBoxLayout()
        self.apply_raw_button = button("Apply valid JSON to forms", "applyRawJson", self.apply_raw)
        self.discard_raw_button = button("Discard raw changes", "discardRawJson", self.discard_raw)
        row.addWidget(self.apply_raw_button)
        row.addWidget(self.discard_raw_button)
        row.addStretch()
        layout.addLayout(row)
        self.tabs.addTab(tab, "Raw JSON")

    def _error(self, title, error):
        QMessageBox.warning(self, title, str(error))

    def _read_configuration(self):
        try:
            self.configuration = read_manager_config(self.config_dir)
            self.config_error = None
        except (OSError, configparser.Error) as exc:
            self.config_error = str(exc)

    def refresh_layouts(self, checked=False):
        self._read_configuration()
        try:
            self.sources = discover_layouts(self.config_dir, self.install_dir)
        except (OSError, ValueError) as exc:
            self._error("Cannot discover layouts", exc)
            return
        self._changing = True
        self.layout_list.clear()
        for source in self.sources:
            tag = "bundled · read-only" if source.builtin else "user"
            text = f"{source.identifier}\n{tag} · {source.format}"
            if source.overrides:
                text += f" · overrides {len(source.overrides)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, source)
            item.setToolTip(str(source.path) + ("\nOverrides:\n" + "\n".join(map(str, source.overrides)) if source.overrides else ""))
            self.layout_list.addItem(item)
            if self.source and source.identifier == self.source.identifier:
                self.layout_list.setCurrentItem(item)
        self._changing = False
        self._filter_layouts()
        self._update_state()

    def _filter_layouts(self):
        text = self.layout_filter.text().casefold()
        for index in range(self.layout_list.count()):
            item = self.layout_list.item(index)
            item.setHidden(text not in item.text().casefold())

    def _select_layout(self, item, previous):
        if self._changing or item is None:
            return
        # Saving a new/imported draft refreshes the list and destroys its items.
        # Keep the data identity, not a Qt item, across the navigation dialog.
        source = item.data(Qt.ItemDataRole.UserRole)
        if not self._guard_navigation():
            self._highlight_source()
            return
        self.open_source(source)

    def _highlight_source(self):
        self._changing = True
        self.layout_list.clearSelection()
        self.layout_list.setCurrentRow(-1)
        if self.source:
            for index in range(self.layout_list.count()):
                item = self.layout_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole).identifier == self.source.identifier:
                    self.layout_list.setCurrentItem(item)
                    break
        self._changing = False

    def open_source(self, source):
        self.source = source
        self.source_revision = None
        self.draft = None
        self._suggested_identifier = source.identifier + "_copy"
        try:
            data = source.path.read_bytes()
            self.source_revision = revision_bytes(data)
            layout = parse_source(source.path, data)
            self.draft = DocumentDraft.opened(layout, self.source_revision)
            self.validation_label.setText("")
        except (OSError, ValueError, SyntaxError) as exc:
            self.validation_label.setText(f"Cannot open this selected source. No lower-priority file was substituted.\n{exc}\nPython code has not been executed.")
        self._populate_form()
        self._highlight_source()
        self._update_state()

    def _populate_form(self):
        self._loading = True
        self.display_name.setText(self.draft.document.get("name", "") if self.draft else "")
        self.app_list.clear()
        if self.draft:
            self.app_list.addItems(list(self.draft.document["app_shortcuts"]))
            for name, spin in self.geometry_spins.items():
                spin.setValue(self.draft.document["geometry"][name])
            self.geometry_canvas.set_geometry(self.draft.document["geometry"])
            self.raw_editor.setPlainText(self.draft.raw_text)
        else:
            self.geometry_canvas.set_geometry({})
            self.raw_editor.clear()
        self._loading = False
        if self.app_list.count():
            self.app_list.setCurrentRow(0)
        else:
            self._rule_selected()
        self._update_state()

    def _editable(self):
        return bool(self.draft and not (self.source and self.source.builtin)
                    and not self.draft.raw_pending and self.pending_removal is None)

    def _update_state(self):
        editable = self._editable()
        for widget in self._edit_widgets:
            widget.setEnabled(editable)
        self.display_name.setEnabled(editable)
        readonly = bool(self.source and self.source.builtin)
        self.raw_editor.setReadOnly(not self.draft or readonly or self.pending_removal is not None)
        raw_pending = bool(self.draft and self.draft.raw_pending)
        self.apply_raw_button.setEnabled(raw_pending and not readonly)
        self.discard_raw_button.setEnabled(raw_pending and not readonly)
        self.copy_button.setEnabled(bool(self.draft) and self.pending_removal is None)
        self.export_button.setEnabled(bool(self.draft))
        self.save_button.setEnabled(bool(self.draft) and not readonly and self.pending_removal is None)
        self.new_button.setEnabled(self.pending_removal is None)
        self.reload_button.setEnabled(self.source is not None and self.pending_removal is None)
        removable = bool(self.source and not self.source.builtin and self.source.provenance == "user" and self.pending_removal is None)
        self.rename_button.setEnabled(removable and bool(self.draft))
        self.delete_button.setEnabled(removable)
        self.trust_button.setVisible(bool(self.source and self.source.format == "python" and not self.draft))
        self.trust_button.setEnabled(not self.offline and self.pending_removal is None)
        saved = bool(self.source and self.draft and not self.draft.dirty)
        self.activate_button.setEnabled(saved and self.runtime_status is not None and not self.config_error and self.pending_removal is None)
        active = self._source_is_referenced(self.source.identifier) if self.source else False
        self.save_button.setText("Save & request reload" if active and self.source and self.source.format == "json" else "Save")
        self.save_button.setToolTip("Saving an active file requests a runtime reload. Save never changes the selected layout identifier.")
        if self.source:
            provenance = "Bundled preset · read-only — Copy to edit" if self.source.builtin else "User preset"
            self.source_label.setText(f"{self.source.identifier} · {provenance} · {self.source.format}\n{self.source.path}"
                                     + ("\nOverrides: " + "; ".join(map(str, self.source.overrides)) if self.source.overrides else ""))
        elif self.draft:
            self.source_label.setText("New user draft · no file saved · identifier is chosen when saving")
        dirty = bool(self.draft and self.draft.dirty)
        revision = self.draft.revision if self.draft else self.source_revision
        state = "Unapplied raw draft" if raw_pending else "Unsaved draft" if dirty else "Saved, unchanged" if self.draft else "No editable document"
        applied = self.runtime_status.get("applied") or {} if self.runtime_status else {}
        exact = bool(self.source and revision and applied.get("identifier") == self.source.identifier and applied.get("revision") == revision)
        running = "Saved revision is running" if exact else "Saved revision is not confirmed running"
        if exact and dirty:
            running += "; draft is not running"
        self.revision_label.setText(f"{state} · saved revision: {revision[:12] if revision else 'none'}\n{running}")
        self.revision_label.setToolTip(f"Saved source revision: {revision or 'none'}\nApplied revision: {applied.get('revision') or 'none'}")
        self.setWindowTitle(("* " if dirty else "") + "DialPad Layout Manager")
        self.cancel_pending_button.setVisible(self.pending_removal is not None)
        if self.pending_removal:
            pending = self.pending_removal
            self.pending_label.setText(f"Waiting for {pending.replacement_identifier} @ {pending.replacement_revision[:12]} to be applied before {pending.operation}. Original file is retained until exact acknowledgment.")
        elif self.activation_request:
            identifier, requested_revision, instance = self.activation_request
            acknowledged = bool(self.runtime_status and self.runtime_status.get("instance_id") == instance
                                and self.runtime_status.get("state") == "applied"
                                and applied.get("identifier") == identifier
                                and applied.get("revision") == requested_revision)
            if acknowledged:
                self.activation_request = None
                self.pending_label.setText(f"Driver acknowledged {identifier} @ {requested_revision[:12]}.")
                self.statusBar().showMessage(f"Driver applied {identifier} @ {requested_revision[:12]}.", 7000)
            else:
                self.pending_label.setText(f"Manager requested {identifier} @ {requested_revision[:12]}; not yet acknowledged by the same driver instance.")
        else:
            self.pending_label.clear()
        self._render_runtime_status()

    def _render_runtime_status(self):
        if self.config_error:
            config = "Configuration error: " + self.config_error
        else:
            value = self.configuration.get("main", "layout", fallback="").strip()
            config = "Configured request: " + (value or "no override (driver argv default)")
        status = self.runtime_status
        if status:
            request, applied = status.get("requested") or {}, status.get("applied") or {}
            requested = f"{request.get('identifier') or '?'} @ {(request.get('revision') or '?')[:12]}"
            running = f"{applied.get('identifier') or 'none'} @ {(applied.get('revision') or '?')[:12]}"
            text = f"{config} · Driver: {status.get('state', 'unknown')}\nRequested: {requested} · Applied: {running} · generation {status.get('generation', '?')}"
            if not status.get("recovery_current", False):
                text += "\nRecovery is not current: " + (status.get("recovery_error") or "durability not confirmed")
            if status.get("error"):
                text += "\n" + str(status["error"])
            self.runtime_label.setToolTip(f"Instance: {status.get('instance_id')}\nRequested revision: {request.get('revision')}\nApplied revision: {applied.get('revision')}")
        else:
            text = f"{config} · {self.runtime_error}"
        self.runtime_label.setText(text)

    def _form_changed(self):
        if self._loading or not self.draft:
            return
        self.draft.discard_raw()
        self._loading = True
        self.raw_editor.setPlainText(self.draft.raw_text)
        self._loading = False
        self._refresh_ring()
        self._validate_inline()
        self._update_state()

    def _name_edited(self, value):
        if not self._editable():
            return
        if value:
            self.draft.document["name"] = value
        else:
            self.draft.document.pop("name", None)
        self._form_changed()

    def _validate_inline(self):
        if not self.draft:
            return
        try:
            layout = normalize_document(self.draft.document)
            bounds = self.runtime_status.get("device_geometry") if self.runtime_status else None
            if bounds and self._adapter:
                self._adapter.validate_device_geometry(layout, bounds)
            self.validation_label.setText("Valid layout · commands and value queries have not been executed")
            self.validation_label.setStyleSheet("")
        except (ValueError, TypeError) as exc:
            self.validation_label.setText(str(exc))
            self.validation_label.setStyleSheet("color: #c34735")

    def _rule_name(self):
        item = self.app_list.currentItem()
        return item.text() if item else None

    def _profile(self):
        name = self._rule_name()
        if self.draft and name is not None:
            return self.draft.document["app_shortcuts"][name]
        return None

    def _rule_selected(self, *unused):
        if self._loading:
            return
        profile = self._profile()
        self._loading = True
        self.function_list.clear()
        if profile is not None:
            self.function_list.addItems(function_names(profile))
            self.mode.setCurrentIndex(1 if function_names(profile) else 0)
        self._loading = False
        if self.function_list.count():
            self.function_list.setCurrentRow(0)
        self._mode_changed()

    def _mode_changed(self, *unused):
        if self._loading:
            return
        multi = self.mode.currentData() == "functions"
        self.function_panel.setVisible(multi)
        profile = self._profile()
        if profile is None:
            self.mode_label.setText("Select an application rule.")
        elif not profile:
            self.mode_label.setText("Empty mapping: no bindings and no tool-ring entries. Add direct actions or a named function.")
        else:
            names = function_names(profile)
            direct = any(key in profile for key in DIRECTIONS)
            if names and direct:
                actual = "Combined direct controls and named functions (the tool-ring entries)"
            elif names:
                actual = "Multifunction mapping: the named functions are the tool-ring entries, in this stored order"
            else:
                actual = "Single-function mapping: no named tool-ring entries, so the ring carries no labeled slices"
            self.mode_label.setText(actual + ". Switching editor views never deletes either set of bindings.")
        self._refresh_actions()
        self._refresh_ring()

    def _function_selected(self, *unused):
        if not self._loading:
            self._refresh_actions()

    def _target(self):
        profile = self._profile()
        if profile is None:
            return None
        if self.mode.currentData() == "functions":
            item = self.function_list.currentItem()
            return profile[item.text()] if item else None
        return profile

    def _refresh_actions(self, *unused):
        if self._loading:
            return
        self.action_list.clear()
        target = self._target()
        if target is None:
            return
        for index, action in enumerate(action_items(target, self.direction.currentData())):
            keys = action.get("key", [])
            keys = [keys] if isinstance(keys, str) else keys
            description = " + ".join(keys) or ("Command: " + action["command"] if action.get("command") else "Control / selection")
            if keys and action.get("command"):
                description += " + shell command"
            if isinstance(action.get("value"), list):
                description += " = " + ", ".join(map(str, action["value"]))
            if action.get("modifier"):
                description = f"[{action['modifier']}] " + description
            title = (action.get("title") + " · ") if action.get("title") else ""
            text = f"{index + 1}. {title}{description}\n{action.get('trigger', 'release')} · hold {action.get('duration', 0)} s"
            self.action_list.addItem(text)
        if self.action_list.count():
            self.action_list.setCurrentRow(0)

    def _refresh_ring(self):
        try:
            minimum = self.configuration.getint("main", "slices_minimum_count", fallback=4)
        except (ValueError, configparser.Error):
            minimum = 4
        self.ring.set_profile(self._profile() or {}, minimum)

    def show_ring_preview(self):
        self.tabs.setCurrentWidget(self.geometry_tab)
        QTimer.singleShot(0, lambda: self.geometry_scroll.ensureWidgetVisible(self.ring.canvas))
        self.statusBar().showMessage("Tool-ring preview uses the same canvas as the live overlay; commands and value queries are not run.", 7000)

    def _ask_name(self, title, label, current="", *, identifier=False):
        value, accepted = QInputDialog.getText(self, title, label, text=current)
        if not accepted:
            return None
        value = value.strip()
        if not value:
            self._error(title, "A nonempty name is required")
            return None
        if identifier:
            try:
                validate_identifier(value)
            except ValueError as exc:
                self._error(title, exc)
                return None
        return value

    def add_rule(self):
        if not self._editable():
            return
        name = self._ask_name("Add application rule", "Application executable / matching rule:")
        if name is None:
            return
        profiles = self.draft.document["app_shortcuts"]
        if name in profiles:
            self._error("Duplicate rule", "That application rule already exists")
            return
        profiles[name] = {}
        self.app_list.addItem(name)
        self.app_list.setCurrentRow(self.app_list.count() - 1)
        self._form_changed()

    def rename_rule(self):
        name = self._rule_name()
        if not self._editable() or not name:
            return
        if name == "none":
            self._error("Fallback rule", "The required ‘none’ fallback cannot be renamed")
            return
        new = self._ask_name("Rename rule", "Application executable / matching rule:", name)
        if new is None:
            return
        try:
            self.draft.document["app_shortcuts"] = rename_ordered(self.draft.document["app_shortcuts"], name, new)
        except ValueError as exc:
            self._error("Duplicate rule", exc)
            return
        self.app_list.currentItem().setText(new)
        self._form_changed()

    def remove_rule(self):
        name = self._rule_name()
        if not self._editable() or not name:
            return
        if name == "none":
            self._error("Fallback rule", "The required ‘none’ fallback cannot be removed; its bindings may be empty")
            return
        if QMessageBox.question(self, "Remove application rule", f"Remove {name!r} and all of its bindings?") != QMessageBox.StandardButton.Yes:
            return
        del self.draft.document["app_shortcuts"][name]
        self.app_list.takeItem(self.app_list.currentRow())
        self._form_changed()

    def move_rule(self, offset):
        name = self._rule_name()
        if not self._editable() or not name:
            return
        self.draft.document["app_shortcuts"] = move_ordered(self.draft.document["app_shortcuts"], name, offset)
        self._rebuild_rule_list(name)
        self._form_changed()

    def _rebuild_rule_list(self, selected):
        self._loading = True
        self.app_list.clear()
        self.app_list.addItems(list(self.draft.document["app_shortcuts"]))
        self._loading = False
        names = list(self.draft.document["app_shortcuts"])
        self.app_list.setCurrentRow(names.index(selected))

    def _rebuild_functions(self, selected=None):
        self._loading = True
        self.function_list.clear()
        names = function_names(self._profile() or {})
        self.function_list.addItems(names)
        self._loading = False
        if names:
            self.function_list.setCurrentRow(names.index(selected) if selected in names else 0)
        self._mode_changed()

    def add_function(self):
        profile = self._profile()
        if not self._editable() or profile is None:
            return
        name = self._ask_name("Add named function", "Function identity (order determines its ring slice):")
        if name is None:
            return
        if name in profile or name in DIRECTIONS:
            self._error("Invalid function name", "The name already exists or is reserved for a direct binding")
            return
        profile[name] = {}
        self.mode.setCurrentIndex(1)
        self._rebuild_functions(name)
        self._form_changed()

    def rename_function(self):
        item = self.function_list.currentItem()
        profile = self._profile()
        if not self._editable() or item is None or profile is None:
            return
        old = item.text()
        new = self._ask_name("Rename function", "Function identity:", old)
        if new is None:
            return
        if new in DIRECTIONS:
            self._error("Reserved name", "Direction names cannot be used as function identities")
            return
        try:
            replacement = rename_ordered(profile, old, new)
        except ValueError as exc:
            self._error("Duplicate function", exc)
            return
        self.draft.document["app_shortcuts"][self._rule_name()] = replacement
        self._rebuild_functions(new)
        self._form_changed()

    def remove_function(self):
        item = self.function_list.currentItem()
        profile = self._profile()
        if not self._editable() or item is None or profile is None:
            return
        if QMessageBox.question(self, "Remove named function", f"Remove {item.text()!r} and its actions?") != QMessageBox.StandardButton.Yes:
            return
        del profile[item.text()]
        self._rebuild_functions()
        self._form_changed()

    def move_function(self, offset):
        item = self.function_list.currentItem()
        profile = self._profile()
        if not self._editable() or item is None or profile is None:
            return
        name = item.text()
        self.draft.document["app_shortcuts"][self._rule_name()] = move_ordered(profile, name, offset, eligible=lambda key: key not in DIRECTIONS)
        self._rebuild_functions(name)
        self._form_changed()

    def edit_function_metadata(self):
        item = self.function_list.currentItem()
        target = self._target()
        if not self._editable() or item is None or target is None:
            return
        dialog = FunctionDialog(item.text(), target, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = deepcopy(self.draft.document)
        candidate["app_shortcuts"][self._rule_name()][item.text()] = dialog.result_value
        try:
            normalize_document(candidate)
        except ValueError as exc:
            self._error("Invalid function metadata", exc)
            return
        self.draft.document = candidate
        self._form_changed()

    def add_action(self):
        target = self._target()
        if not self._editable() or target is None:
            return
        dialog = ActionDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            direction = self.direction.currentData()
            values = list(action_items(target, direction))
            values.append(dialog.result_value)
            put_actions(target, direction, values)
            self._refresh_actions()
            self.action_list.setCurrentRow(len(values) - 1)
            self._form_changed()

    def edit_action(self):
        target = self._target()
        index = self.action_list.currentRow()
        if not self._editable() or target is None or index < 0:
            return
        direction = self.direction.currentData()
        values = list(action_items(target, direction))
        dialog = ActionDialog(values[index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values[index] = dialog.result_value
            put_actions(target, direction, values)
            self._refresh_actions()
            self.action_list.setCurrentRow(index)
            self._form_changed()

    def remove_action(self):
        target = self._target()
        index = self.action_list.currentRow()
        if not self._editable() or target is None or index < 0:
            return
        direction = self.direction.currentData()
        values = list(action_items(target, direction))
        del values[index]
        put_actions(target, direction, values)
        self._refresh_actions()
        self._form_changed()

    def move_action(self, offset):
        target = self._target()
        index = self.action_list.currentRow()
        if not self._editable() or target is None or index < 0:
            return
        direction = self.direction.currentData()
        values = list(action_items(target, direction))
        if not 0 <= index + offset < len(values):
            return
        values[index], values[index + offset] = values[index + offset], values[index]
        put_actions(target, direction, values)
        self._refresh_actions()
        self.action_list.setCurrentRow(index + offset)
        self._form_changed()

    def _geometry_edited(self, name, value):
        if self._loading or not self._editable():
            return
        self.draft.document["geometry"][name] = value
        self.geometry_canvas.set_geometry(self.draft.document["geometry"])
        self._form_changed()

    def _canvas_edited(self, geometry):
        if not self._editable():
            return
        self.draft.document["geometry"] = geometry
        self._loading = True
        for name, spin in self.geometry_spins.items():
            spin.setValue(geometry[name])
        self._loading = False
        self._form_changed()

    def _raw_edited(self):
        if self._loading or not self.draft or (self.source and self.source.builtin):
            return
        self.draft.edit_raw(self.raw_editor.toPlainText())
        self.validation_label.setText("Unapplied raw draft. Apply valid JSON or discard raw changes before structured editing / saving.")
        self._update_state()

    def apply_raw(self):
        if not self.draft or not self.draft.raw_pending:
            return True
        try:
            self.draft.apply_raw()
        except ValueError as exc:
            self.validation_label.setText(f"Raw draft is invalid and has been preserved:\n{exc}")
            self.validation_label.setStyleSheet("color: #c34735")
            self.tabs.setCurrentIndex(2)
            return False
        self._populate_form()
        self._validate_inline()
        return True

    def discard_raw(self):
        if not self.draft or not self.draft.raw_pending:
            return
        if QMessageBox.question(self, "Discard raw JSON draft", "Discard unapplied raw text and restore the last valid form?") != QMessageBox.StandardButton.Yes:
            return
        self.draft.discard_raw()
        self._loading = True
        self.raw_editor.setPlainText(self.draft.raw_text)
        self._loading = False
        self._validate_inline()
        self._update_state()

    def _resolve_raw(self):
        if not self.draft or not self.draft.raw_pending:
            return True
        choice = QMessageBox.question(self, "Unapplied raw JSON", "Apply the raw JSON draft before continuing? Invalid text will remain untouched.",
                                      QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        return choice == QMessageBox.StandardButton.Apply and self.apply_raw()

    def validate_current(self):
        if not self.draft:
            return
        try:
            layout = parse_json(self.draft.raw_text) if self.draft.raw_pending else normalize_document(self.draft.document)
            if self.runtime_status and self._adapter:
                self._adapter.validate_device_geometry(layout, self.runtime_status.get("device_geometry"))
        except ValueError as exc:
            self.validation_label.setText(str(exc))
            self.validation_label.setStyleSheet("color: #c34735")
            return
        self.validation_label.setText("Valid raw draft (not applied to forms)" if self.draft.raw_pending else "Valid layout · no commands executed")
        self.validation_label.setStyleSheet("")

    def _guard_navigation(self):
        if self.pending_removal:
            self._error("File operation pending", "Wait for acknowledgment, or cancel the pending rename/deletion first. The original is still retained.")
            return False
        if not self.draft or not self.draft.dirty:
            return True
        choice = QMessageBox.question(self, "Unsaved layout draft", "Save your changes before leaving this draft? Discard also discards unapplied raw text.",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if choice == QMessageBox.StandardButton.Save:
            return self.save_current()
        return choice == QMessageBox.StandardButton.Discard

    def new_layout(self):
        if not self._guard_navigation():
            return
        geometry = deepcopy(self.draft.document["geometry"]) if self.draft else {
            "circle_center_x": 500, "circle_center_y": 500, "circle_diameter": 800,
            "center_button_diameter": 300, "top_right_icon_width": 150, "top_right_icon_height": 150,
        }
        document = {"schema_version": 1, "name": "New preset", "geometry": geometry, "app_shortcuts": {"none": {}}}
        self.source = None
        self.source_revision = None
        self.draft = DocumentDraft.opened(normalize_document(document))
        self.draft.baseline = None
        self._suggested_identifier = "my_layout"
        self._populate_form()
        self._highlight_source()
        self._validate_inline()

    def copy_layout(self):
        if self.draft and self._resolve_raw():
            self._save_copy()

    def _new_identifier(self, suggestion=None):
        value = self._ask_name("Save user copy", "New identifier (letters, digits, underscores, hyphens):", suggestion or self._suggested_identifier, identifier=True)
        if value is None:
            return None
        try:
            sources = discover_layouts(self.config_dir, self.install_dir)
            if any(source.identifier == value for source in sources):
                raise ValueError("This identifier is already installed. Choose a distinct identifier; existing presets are not overwritten by Copy.")
            path = user_layout_path(self.config_dir, value)
            if path.exists():
                raise ValueError("The destination already exists")
        except (OSError, ValueError) as exc:
            self._error("Cannot create copy", exc)
            return None
        return value, path

    def _save_copy(self):
        if not self.draft:
            return False
        try:
            layout = normalize_document(self.draft.document)
        except ValueError as exc:
            self._error("Invalid layout", exc)
            return False
        destination = self._new_identifier()
        if destination is None:
            return False
        identifier, path = destination
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            revision = save_layout(path, layout)
            self.source = resolve_layout(identifier, self.config_dir, self.install_dir)
            self.source_revision = revision
            self.draft.mark_saved(layout, revision)
        except (OSError, ValueError) as exc:
            self._error("Copy failed", exc)
            return False
        self._suggested_identifier = identifier + "_copy"
        self.refresh_layouts()
        self._populate_form()
        self.statusBar().showMessage("User copy saved. It has not been activated.", 7000)
        return True

    def _check_source_revision(self):
        if not self.source or not self.source_revision:
            return
        if revision_bytes(self.source.path.read_bytes()) != self.source_revision:
            raise ValueError("The selected file changed externally. Reload it or save your draft as a new copy.")

    def _conflict_dialog(self, error):
        box = QMessageBox(QMessageBox.Icon.Warning, "External revision conflict", str(error), parent=self)
        reload_button = box.addButton("Reload external file", QMessageBox.ButtonRole.DestructiveRole)
        copy_button = box.addButton("Save a new copy…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() == copy_button:
            return self._save_copy()
        if box.clickedButton() == reload_button:
            if QMessageBox.question(self, "Discard local changes", "Reloading discards this local draft. Continue?") == QMessageBox.StandardButton.Yes:
                try:
                    source = resolve_layout(self.source.identifier, self.config_dir, self.install_dir)
                except (OSError, ValueError) as exc:
                    self._error("Cannot reload external file", exc)
                else:
                    self.open_source(source)
        return False

    def save_current(self):
        if not self.draft or self.pending_removal:
            return False
        if self.source and self.source.builtin:
            self._error("Bundled preset is read-only", "Use Copy to create a distinct user preset")
            return False
        if not self._resolve_raw():
            return False
        if not self.source or self.source.format != "json":
            return self._save_copy()
        try:
            self._assert_user_source()
            self._check_source_revision()
        except (OSError, ValueError) as exc:
            return self._conflict_dialog(exc)
        self._read_configuration()
        may_reload = self._source_is_referenced(self.source.identifier) or self.runtime_status is None
        if may_reload and self.draft.dirty:
            if QMessageBox.question(self, "Saving may request a live reload", "Saving changes the watched preset file. If the driver is using it, this requests a reload; the running revision changes only after acknowledgment. Without live status, the current runtime selection cannot be confirmed. Continue?",
                                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Save:
                return False
        try:
            layout = normalize_document(self.draft.document)
            revision = save_layout(self.source.path, layout, expected_revision=self.source_revision, overwrite=True)
        except (OSError, ValueError) as exc:
            self._error("Save failed — draft retained", exc)
            return False
        self.source_revision = revision
        self.draft.mark_saved(layout, revision)
        self._populate_form()
        self._validate_inline()
        self.statusBar().showMessage("Saved atomically. See runtime status for the actually applied revision.", 7000)
        return True

    def reload_layout(self):
        if not self.source or not self._guard_navigation():
            return
        try:
            source = resolve_layout(self.source.identifier, self.config_dir, self.install_dir)
        except (OSError, ValueError) as exc:
            self._error("Cannot reload", exc)
            return
        self.open_source(source)

    def import_layout(self):
        if not self._guard_navigation():
            return
        path, _filter = QFileDialog.getOpenFileName(self, "Import layout without executing code", "", "DialPad layouts (*.json *.py)")
        if not path:
            return
        try:
            layout = parse_source(Path(path), Path(path).read_bytes())
        except (OSError, ValueError, SyntaxError) as exc:
            self._error("Static import failed — original preserved", f"{exc}\n\nDynamic Python is not executed by Import. Retain it unchanged, or select an installed Python layout and explicitly choose Trust Python and convert.")
            return
        self.source = None
        self.source_revision = None
        self.draft = DocumentDraft.opened(layout)
        self.draft.baseline = None
        try:
            self._suggested_identifier = validate_identifier(Path(path).stem + "_imported")
        except ValueError:
            self._suggested_identifier = "imported_layout"
        self._populate_form()
        self._highlight_source()
        fields = command_fields(layout.document)
        self.validation_label.setText(f"Imported statically from {path}. Original unchanged. {len(fields)} command/query fields were not executed; review their trust before activation.")

    def export_layout(self):
        if not self.draft or not self._resolve_raw():
            return
        try:
            layout = normalize_document(self.draft.document)
        except ValueError as exc:
            self._error("Invalid export", exc)
            return
        suggestion = (self.source.identifier if self.source else self._suggested_identifier) + ".json"
        filename, _filter = QFileDialog.getSaveFileName(self, "Export normalized JSON", suggestion, "JSON layout (*.json)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            protected = {source.path.resolve() for source in self.sources}
            protected.update(shadow.resolve() for source in self.sources for shadow in source.overrides)
            if path.resolve() in protected:
                raise ValueError("Export cannot overwrite an installed preset. Use Save or select a different export path.")
            expected = revision_bytes(path.read_bytes()) if path.exists() else None
            if expected and QMessageBox.question(self, "Replace export file", f"Replace {path}?") != QMessageBox.StandardButton.Yes:
                return
            save_layout(path, layout, expected_revision=expected, overwrite=expected is not None)
        except (OSError, ValueError) as exc:
            self._error("Export failed", exc)
            return
        self.statusBar().showMessage(f"Exported to {path}; editor save/activation state unchanged", 7000)

    def _linux_adapter(self):
        if self.offline:
            raise RuntimeError("Linux runtime integration is disabled; offline editing remains available")
        if self._adapter is None:
            self._adapter = importlib.import_module("dialpad_layout_linux")
        return self._adapter

    def poll_runtime(self):
        self._read_configuration()
        if self.offline:
            self.runtime_status = None
        else:
            try:
                self.runtime_status = self._linux_adapter().get_status(self.config_dir, timeout=0.12)
                self.runtime_error = ""
            except (OSError, RuntimeError, ImportError, ValueError) as exc:
                self.runtime_status = None
                self.runtime_error = f"Driver unavailable — offline editing only ({exc})"
        bounds = self.runtime_status.get("device_geometry") if self.runtime_status else None
        self.geometry_canvas.set_device_bounds(bounds)
        self._refresh_ring()
        if self.pending_removal and self.runtime_status:
            if self.runtime_status.get("instance_id") != self.pending_removal.instance_id:
                self.pending_removal = None
                self._error("Runtime instance changed", "The pending file operation was canceled. The original file has been retained. Review the new instance before trying again.")
            elif replacement_acknowledged(self.runtime_status, self.pending_removal):
                self._finish_pending_removal()
            elif self.runtime_status.get("state") == "rejected":
                self.pending_removal = None
                self._error("Replacement was rejected", "No file was removed. The running layout is retained; choose a valid replacement before renaming/deleting.")
        self._update_state()

    def _source_is_referenced(self, identifier):
        if not identifier:
            return False
        if self.configuration.get("main", "layout", fallback="").strip() == identifier:
            return True
        return bool(self.runtime_status and any((self.runtime_status.get(key) or {}).get("identifier") == identifier for key in ("requested", "applied")))

    def _confirm_commands(self, layout):
        fields = command_fields(layout.document)
        if not fields:
            return True
        box = QMessageBox(QMessageBox.Icon.Warning, "Trust commands in this preset?",
                          "Activating this preset allows the driver to run its shell actions and display-value queries as your user. Validation and previews did not execute them. Activate only if you trust the source and these commands.",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.setDetailedText("\n\n".join(f"{path}\n{command}" for path, command in fields))
        return box.exec() == QMessageBox.StandardButton.Yes

    def activate_current(self):
        if not self.source or not self.draft or self.draft.dirty or self.pending_removal:
            return
        self.poll_runtime()
        if not self.runtime_status or self.config_error:
            self._error("Activation unavailable", self.config_error or self.runtime_error)
            return
        try:
            self._check_source_revision()
            layout = normalize_document(self.draft.document)
        except (OSError, ValueError) as exc:
            self._conflict_dialog(exc)
            return
        if not self._confirm_commands(layout):
            return
        try:
            loaded = self._linux_adapter().activate_layout(self.config_dir, self.source.identifier, self.install_dir,
                                                           expected_revision=self.source_revision)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self._error("Activation request failed", exc)
            return
        self.activation_request = (self.source.identifier, loaded.revision, self.runtime_status.get("instance_id"))
        self.statusBar().showMessage(f"Requested {self.source.identifier} @ {loaded.revision[:12]}; waiting for runtime acknowledgment", 10000)
        self.poll_runtime()

    def trusted_conversion(self):
        if not self.source or self.source.format != "python" or not self._guard_navigation():
            return
        choice = QMessageBox.warning(self, "Execute trusted Python once for conversion?",
                                     f"{self.source.path}\n\nThis will execute arbitrary Python as your user, including imports and side effects. Failure cannot undo those effects. Only proceed if you trust this file and its imports. The result becomes an unsaved JSON copy; the original remains unchanged. This does not enable dynamic Python in the driver.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            loaded = self._linux_adapter().load_layout(self.source.identifier, self.config_dir, self.install_dir, trusted_python=True)
        except Exception as exc:
            self._error("Trusted conversion failed", exc)
            return
        self._suggested_identifier = self.source.identifier + "_converted"
        self.source = None
        self.source_revision = None
        self.draft = DocumentDraft.opened(loaded.layout)
        self.draft.baseline = None
        self._populate_form()
        self._highlight_source()
        self.validation_label.setText("Trusted Python executed for conversion. Save a distinct JSON copy before activation; original source unchanged.")

    def _assert_user_source(self):
        if not self.source or self.source.builtin or self.source.provenance != "user":
            raise ValueError("Only user-owned layout files can be modified")
        contained = user_layout_path(self.config_dir, self.source.identifier)
        if self.source.path.is_symlink() or self.source.path.parent.resolve() != contained.parent.resolve():
            raise ValueError("Refusing to modify a symlink or a source outside the user layout directory; save a new copy instead")

    def _prepare_removal(self):
        if not self.source or not self._guard_navigation():
            return False
        self.poll_runtime()
        if not self.runtime_status or not self.runtime_status.get("instance_id") or self.config_error:
            self._error("Safe offline refusal", "Cannot prove which layout this driver instance is using. Rename/deletion is disabled until live status is available. Copy and export remain available; no files were removed.")
            return False
        if self.draft and self.draft.dirty:
            # A Discard choice permits the operation, but must not accidentally
            # include discarded local changes in the renamed file.
            self.open_source(self.source)
        try:
            self._assert_user_source()
            self._check_source_revision()
        except (OSError, ValueError) as exc:
            self._error("File operation refused", exc)
            return False
        return True

    def _require_still_inactive(self, identifier):
        self.poll_runtime()
        if not self.runtime_status or self.config_error or self._source_is_referenced(identifier):
            raise ValueError("The active selection changed or became unavailable. The original is retained; retry with an acknowledged replacement.")

    def rename_layout(self):
        if not self._prepare_removal() or not self.draft:
            return
        original = self.source
        original_revision = self.source_revision
        destination = self._new_identifier(original.identifier + "_renamed")
        if destination is None:
            return
        identifier, path = destination
        layout = normalize_document(self.draft.document)
        active = self._source_is_referenced(original.identifier)
        if active and not self._confirm_commands(layout):
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            revision = save_layout(path, layout)
            if active:
                loaded = self._linux_adapter().activate_layout(self.config_dir, identifier, self.install_dir, expected_revision=revision)
                self._begin_pending("rename", original, original_revision, loaded)
            else:
                self._require_still_inactive(original.identifier)
                self._linux_adapter().remove_user_layout(
                    self.config_dir, original, original_revision, self.install_dir,
                    instance_id=self.runtime_status["instance_id"])
                self.open_source(resolve_layout(identifier, self.config_dir, self.install_dir))
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self._error("Rename not completed", f"{exc}\nThe original is retained on failure. Any newly saved copy is kept to avoid data loss.")
        self.refresh_layouts()

    def delete_layout(self):
        if not self._prepare_removal():
            return
        original = self.source
        revision = self.source_revision
        active = self._source_is_referenced(original.identifier)
        if active:
            choices = [source.identifier for source in self.sources if source.identifier != original.identifier]
            if not choices:
                self._error("Replacement required", "Create another preset first. The last active preset cannot be deleted.")
                return
            identifier, accepted = QInputDialog.getItem(self, "Replacement required before deletion", "Activate this replacement and wait for acknowledgment:", choices, editable=False)
            if not accepted:
                return
            try:
                loaded = self._linux_adapter().load_layout(identifier, self.config_dir, self.install_dir)
                if not self._confirm_commands(loaded.layout):
                    return
                if QMessageBox.question(self, "Delete after replacement is applied", f"Delete {original.identifier!r} only after {identifier!r} at revision {loaded.revision[:12]} is confirmed applied?") != QMessageBox.StandardButton.Yes:
                    return
                replacement = self._linux_adapter().activate_layout(self.config_dir, identifier, self.install_dir, expected_revision=loaded.revision)
                self._begin_pending("deletion", original, revision, replacement)
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._error("Replacement request failed — original retained", exc)
                return
        else:
            if QMessageBox.question(self, "Delete user preset", f"Permanently delete {original.path}? This cannot be undone.") != QMessageBox.StandardButton.Yes:
                return
            try:
                self._require_still_inactive(original.identifier)
                self._linux_adapter().remove_user_layout(
                    self.config_dir, original, revision, self.install_dir,
                    instance_id=self.runtime_status["instance_id"])
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._error("Delete failed", exc)
                return
            self.source = None
            self.source_revision = None
            self.draft = None
            self._populate_form()
            self.refresh_layouts()
            self.source_label.setText("Preset deleted. Select another layout or create a new preset.")
        self._update_state()

    def _begin_pending(self, operation, original, original_revision, replacement):
        status = self.runtime_status
        self.pending_removal = PendingRemoval(operation, original.path, original.identifier, original_revision,
                                               replacement.source.identifier, replacement.revision, replacement.source.path,
                                               status["instance_id"], status["generation"])
        self._update_state()
        QTimer.singleShot(0, self.poll_runtime)

    def _finish_pending_removal(self):
        pending = self.pending_removal
        try:
            original = resolve_layout(pending.original_identifier, self.config_dir, self.install_dir)
            if original.path != pending.original_path:
                raise ValueError("The original layout source changed before removal")
            self._linux_adapter().remove_user_layout(
                self.config_dir, original, pending.original_revision, self.install_dir,
                instance_id=pending.instance_id,
                replacement=(pending.replacement_identifier, pending.replacement_revision))
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self.pending_removal = None
            self._error("Original retained", f"{exc}\nNo original file was removed.")
            return
        self.pending_removal = None
        self.refresh_layouts()
        try:
            self.open_source(resolve_layout(pending.replacement_identifier, self.config_dir, self.install_dir))
        except (OSError, ValueError) as exc:
            self._error("Cannot display replacement", exc)
        self.statusBar().showMessage(f"Completed {pending.operation} after exact-revision acknowledgment", 8000)

    def cancel_pending(self):
        if self.pending_removal:
            self.pending_removal = None
            self._update_state()
            self.statusBar().showMessage("File removal canceled; original retained. The already-persisted replacement activation request is not rolled back.", 12000)

    def closeEvent(self, event):
        if self.pending_removal:
            if QMessageBox.question(self, "Cancel pending file operation?", "Closing cancels file removal and retains the original. The replacement activation request remains in configuration. Close?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_pending()
        if not self._guard_navigation():
            event.ignore()
            return
        self.timer.stop()
        event.accept()


def create_application(config_dir, install_dir=None, *, argv=None, offline=False, runtime_adapter=None):
    """Return (QApplication, window) without showing it or starting an event loop."""
    app = QApplication.instance() or QApplication(argv or ["dialpad-layout-manager"])
    app.setApplicationName("DialPad Layout Manager")
    window = LayoutManagerWindow(config_dir, install_dir, offline=offline, runtime_adapter=runtime_adapter)
    return app, window


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default=os.environ.get("DIALPAD_CONFIG_DIR"), help="Same explicit configuration directory as the driver")
    parser.add_argument("--install-dir", default=str(Path(__file__).resolve().parent), help="Driver installation containing layouts and the bundle manifest")
    parser.add_argument("--offline", action="store_true", help="Never import or contact the Linux runtime adapter")
    args = parser.parse_args(argv)
    if not args.config_dir:
        parser.error("--config-dir or DIALPAD_CONFIG_DIR is required; no configuration-directory guessing is performed")
    app, window = create_application(args.config_dir, args.install_dir, offline=args.offline)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
