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
from dialpad_help import add_help_toolbar, attach_help, help_button, with_help
from dialpad_i18n import initialize_i18n, tr


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


# Canonical runtime states and stored triggers map to explicit locale keys; any other
# raw value is opaque backend data and is displayed unchanged.
DRIVER_STATE_KEYS = {
    "pending": "manager.state.driver.pending",
    "applied": "manager.state.driver.applied",
    "rejected": "manager.state.driver.rejected",
    "recovered": "manager.state.driver.recovered",
    "requested": "manager.state.driver.requested",
    "unknown": "manager.state.driver.unknown",
}

ACTION_TRIGGER_KEYS = {
    "release": "manager.actions.triggers.release",
    "immediate": "manager.actions.triggers.immediate",
}


def translated_state(value):
    key = DRIVER_STATE_KEYS.get(value)
    return tr(key) if key else str(value)


def translated_trigger(value):
    key = ACTION_TRIGGER_KEYS.get(value)
    return tr(key) if key else str(value)


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
        self.setWindowTitle(tr("manager.window.title"))
        self.resize(1200, 820)
        self.config_dir = Path(config_dir).expanduser().resolve()
        self.install_dir = Path(install_dir or Path(__file__).parent).expanduser().resolve()
        self.offline = offline or not sys.platform.startswith("linux")
        self._adapter = runtime_adapter
        self.runtime_status = None
        self.runtime_error = tr("manager.runtime.offline")
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
        self.help_toolbar = add_help_toolbar(self)
        outer.addWidget(self.help_toolbar)
        self.config_label = QLabel(tr("manager.state.config_path", path=self.config_dir), self)
        self.config_label.setObjectName("configDirectory")
        self.config_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.config_label.setWordWrap(True)
        attach_help(self.config_label, "state")
        outer.addWidget(self.config_label)
        self.runtime_label = QLabel(self)
        self.runtime_label.setObjectName("runtimeStatus")
        self.runtime_label.setWordWrap(True)
        self.runtime_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(with_help(self.runtime_label, "state"))
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        outer.addWidget(splitter, 1)
        library = QWidget(splitter)
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(0, 0, 8, 0)
        presets_row = QHBoxLayout()
        presets_row.addWidget(QLabel(tr("manager.library.title")))
        presets_row.addWidget(help_button("library", self))
        presets_row.addStretch()
        library_layout.addLayout(presets_row)
        self.layout_filter = QLineEdit(self)
        self.layout_filter.setObjectName("layoutSearch")
        self.layout_filter.setPlaceholderText(tr("manager.library.filter_placeholder"))
        self.layout_filter.textChanged.connect(self._filter_layouts)
        library_layout.addWidget(with_help(self.layout_filter, "library"))
        self.layout_list = QListWidget(self)
        self.layout_list.setObjectName("layoutList")
        self.layout_list.currentItemChanged.connect(self._select_layout)
        attach_help(self.layout_list, "library")
        library_layout.addWidget(self.layout_list, 1)
        row = QHBoxLayout()
        self.new_button = button(tr("manager.library.buttons.new"), "newLayout", self.new_layout)
        self.copy_button = button(tr("manager.library.buttons.copy"), "copyLayout", self.copy_layout)
        attach_help(self.new_button, "library")
        attach_help(self.copy_button, "library")
        row.addWidget(self.new_button)
        row.addWidget(self.copy_button)
        library_layout.addLayout(row)
        row = QHBoxLayout()
        import_button = button(tr("manager.library.buttons.import"), "importLayout", self.import_layout)
        attach_help(import_button, "library")
        row.addWidget(import_button)
        self.export_button = button(tr("manager.library.buttons.export"), "exportLayout", self.export_layout)
        attach_help(self.export_button, "library")
        row.addWidget(self.export_button)
        library_layout.addLayout(row)
        row = QHBoxLayout()
        self.rename_button = button(tr("manager.library.buttons.rename"), "renameLayout", self.rename_layout)
        self.delete_button = button(tr("manager.library.buttons.delete"), "deleteLayout", self.delete_layout)
        attach_help(self.rename_button, "save_activate")
        attach_help(self.delete_button, "save_activate")
        row.addWidget(self.rename_button)
        row.addWidget(self.delete_button)
        library_layout.addLayout(row)
        refresh_button = button(tr("manager.library.buttons.refresh"), "refreshLayouts", self.refresh_layouts)
        attach_help(refresh_button, "library")
        library_layout.addWidget(refresh_button)
        editor = QWidget(splitter)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        self.source_label = QLabel(tr("manager.library.select_placeholder"), self)
        self.source_label.setObjectName("sourceProvenance")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        attach_help(self.source_label, "identity")
        editor_layout.addWidget(self.source_label)
        title_row = QFormLayout()
        self.display_name = QLineEdit(self)
        self.display_name.setObjectName("displayName")
        self.display_name.setPlaceholderText(tr("manager.identity.display_name_placeholder"))
        self.display_name.textEdited.connect(self._name_edited)
        attach_help(self.display_name, "identity")
        title_row.addRow(tr("manager.identity.display_name"), with_help(self.display_name, "identity"))
        editor_layout.addLayout(title_row)
        self.revision_label = QLabel(self)
        self.revision_label.setObjectName("revisionState")
        self.revision_label.setWordWrap(True)
        attach_help(self.revision_label, "state")
        editor_layout.addWidget(with_help(self.revision_label, "state"))
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
        attach_help(self.validation_label, "troubleshooting")
        validation_row = QHBoxLayout()
        validation_row.addWidget(self.validation_label, 1)
        validation_row.addWidget(help_button("troubleshooting", self))
        editor_layout.addLayout(validation_row)
        self.pending_label = QLabel(self)
        self.pending_label.setObjectName("pendingOperation")
        self.pending_label.setWordWrap(True)
        attach_help(self.pending_label, "save_activate")
        editor_layout.addWidget(self.pending_label)
        self.cancel_pending_button = button(tr("manager.actions.cancel_pending"), "cancelPendingOperation", self.cancel_pending)
        attach_help(self.cancel_pending_button, "save_activate")
        editor_layout.addWidget(self.cancel_pending_button)
        row = QHBoxLayout()
        self.trust_button = button(tr("manager.actions.trust_convert"), "trustedConversion", self.trusted_conversion)
        attach_help(self.trust_button, "trust")
        row.addWidget(self.trust_button)
        row.addStretch()
        self.reload_button = button(tr("manager.actions.reload_file"), "reloadLayout", self.reload_layout)
        attach_help(self.reload_button, "library")
        row.addWidget(self.reload_button)
        validate_button = button(tr("manager.actions.validate"), "validateLayout", self.validate_current)
        attach_help(validate_button, "troubleshooting")
        row.addWidget(validate_button)
        self.save_button = button(tr("manager.actions.save"), "saveLayout", self.save_current)
        attach_help(self.save_button, "save_activate")
        row.addWidget(self.save_button)
        self.activate_button = button(tr("manager.actions.activate"), "activateLayout", self.activate_current)
        attach_help(self.activate_button, "save_activate")
        row.addWidget(self.activate_button)
        row.addWidget(help_button("save_activate", self))
        editor_layout.addLayout(row)
        splitter.setSizes([265, 935])
        self.setCentralWidget(root)
        self.statusBar().showMessage(tr("manager.status.static_note"))
        save_action = QAction(tr("manager.actions.save"), self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_current)
        self.addAction(save_action)
        self.setStyleSheet("QGroupBox { margin-top: 0.7em; } QGroupBox::title { subcontrol-origin: margin; left: 8px; } "
                           "QPushButton { padding: 5px 9px; } QPlainTextEdit { font-family: monospace; }")

    def _edit_button(self, text, name, callback):
        widget = button(text, name, callback)
        if name in ("addApplication", "renameApplication", "removeApplication", "applicationUp", "applicationDown"):
            attach_help(widget, "rules")
        elif name in ("addFunction", "renameFunction", "removeFunction", "functionUp", "functionDown"):
            attach_help(widget, "functions")
        elif name in ("addAction", "editAction", "removeAction", "actionUp", "actionDown"):
            attach_help(widget, "actions")
        self._edit_widgets.append(widget)
        return widget

    def _build_rules_tab(self):
        tab = QWidget(self)
        root = QVBoxLayout(tab)
        note_row = QHBoxLayout()
        note = QLabel(tr("manager.rules.note"))
        note.setWordWrap(True)
        note_row.addWidget(note, 1)
        note_row.addWidget(help_button("rules", self))
        root.addLayout(note_row)
        split = QSplitter(Qt.Orientation.Horizontal, tab)
        root.addWidget(split, 1)
        rules = QWidget(split)
        rule_box = QVBoxLayout(rules)
        rule_box.setContentsMargins(0, 0, 6, 0)
        self.app_list = QListWidget(self)
        self.app_list.setObjectName("applicationRules")
        self.app_list.currentItemChanged.connect(self._rule_selected)
        attach_help(self.app_list, "rules")
        rule_box.addWidget(self.app_list)
        row = QHBoxLayout()
        row.addWidget(self._edit_button(tr("manager.rules.buttons.add"), "addApplication", self.add_rule))
        row.addWidget(self._edit_button(tr("manager.rules.buttons.rename"), "renameApplication", self.rename_rule))
        row.addWidget(self._edit_button(tr("manager.rules.buttons.remove"), "removeApplication", self.remove_rule))
        rule_box.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._edit_button(tr("manager.rules.buttons.move_up"), "applicationUp", lambda: self.move_rule(-1)))
        row.addWidget(self._edit_button(tr("manager.rules.buttons.move_down"), "applicationDown", lambda: self.move_rule(1)))
        rule_box.addLayout(row)
        bindings = QWidget(split)
        right = QVBoxLayout(bindings)
        right.setContentsMargins(6, 0, 0, 0)
        self.mode = QComboBox(self)
        self.mode.setObjectName("functionMode")
        self.mode.addItem(tr("manager.rules.mode.single"), "direct")
        self.mode.addItem(tr("manager.rules.mode.multifunction"), "functions")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        attach_help(self.mode, "functions")
        right.addWidget(self.mode)
        self.mode_label = QLabel(self)
        self.mode_label.setWordWrap(True)
        self.mode_label.setObjectName("mappingModeDescription")
        right.addWidget(self.mode_label)
        self.function_panel = QWidget(self)
        function_box = QVBoxLayout(self.function_panel)
        function_box.setContentsMargins(0, 0, 0, 0)
        function_header = QHBoxLayout()
        self.function_label = QLabel(tr("manager.functions.title"), self)
        self.function_label.setObjectName("functionListLabel")
        self.function_label.setWordWrap(True)
        function_header.addWidget(self.function_label, 1)
        function_header.addWidget(help_button("functions", self))
        function_box.addLayout(function_header)
        self.function_list = QListWidget(self)
        self.function_list.setObjectName("functionList")
        self.function_list.setMaximumHeight(130)
        self.function_list.currentItemChanged.connect(self._function_selected)
        attach_help(self.function_list, "functions")
        function_box.addWidget(self.function_list)
        row = QHBoxLayout()
        row.addWidget(self._edit_button(tr("manager.functions.buttons.add"), "addFunction", self.add_function))
        row.addWidget(self._edit_button(tr("manager.functions.buttons.rename"), "renameFunction", self.rename_function))
        row.addWidget(self._edit_button(tr("manager.functions.buttons.remove"), "removeFunction", self.remove_function))
        row.addWidget(self._edit_button(tr("manager.functions.buttons.up"), "functionUp", lambda: self.move_function(-1)))
        row.addWidget(self._edit_button(tr("manager.functions.buttons.down"), "functionDown", lambda: self.move_function(1)))
        function_box.addLayout(row)
        self.function_metadata_button = self._edit_button(tr("manager.functions.metadata"), "editFunctionMetadata", self.edit_function_metadata)
        attach_help(self.function_metadata_button, "command")
        row = QHBoxLayout()
        row.addWidget(self.function_metadata_button)
        self.preview_ring_button = button(tr("manager.functions.preview_ring"), "previewToolRing", self.show_ring_preview)
        self.preview_ring_button.setToolTip(tr("manager.functions.preview_ring_tooltip"))
        attach_help(self.preview_ring_button, "functions")
        row.addWidget(self.preview_ring_button)
        row.addStretch()
        function_box.addLayout(row)
        right.addWidget(self.function_panel)
        direction_row = QHBoxLayout()
        self.direction = QComboBox(self)
        self.direction.setObjectName("actionDirection")
        for text, key in [(tr("manager.directions.center"), "center"), (tr("manager.directions.clockwise"), "clockwise"), (tr("manager.directions.counterclockwise"), "counterclockwise")]:
            self.direction.addItem(text, key)
        self.direction.currentIndexChanged.connect(self._refresh_actions)
        attach_help(self.direction, "actions")
        direction_row.addWidget(self.direction, 1)
        direction_row.addWidget(help_button("actions", self))
        right.addLayout(direction_row)
        self.action_list = QListWidget(self)
        self.action_list.setObjectName("actionList")
        self.action_list.setMinimumHeight(110)
        self.action_list.itemDoubleClicked.connect(lambda _item: self.edit_action())
        attach_help(self.action_list, "actions")
        right.addWidget(self.action_list, 1)
        row = QHBoxLayout()
        row.addWidget(self._edit_button(tr("manager.actions.buttons.add"), "addAction", self.add_action))
        row.addWidget(self._edit_button(tr("manager.actions.buttons.edit"), "editAction", self.edit_action))
        row.addWidget(self._edit_button(tr("manager.actions.buttons.remove"), "removeAction", self.remove_action))
        row.addWidget(self._edit_button(tr("manager.actions.buttons.up"), "actionUp", lambda: self.move_action(-1)))
        row.addWidget(self._edit_button(tr("manager.actions.buttons.down"), "actionDown", lambda: self.move_action(1)))
        right.addLayout(row)
        precedence = QLabel(tr("manager.actions.precedence_note"))
        precedence.setWordWrap(True)
        attach_help(precedence, "modifier")
        right.addWidget(precedence)
        split.setSizes([230, 530])
        self.tabs.addTab(tab, tr("manager.rules.tab"))

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
        geometry_box = QGroupBox(tr("manager.geometry.template"), self)
        form = QFormLayout(geometry_box)
        self.geometry_spins = {}
        for name, label in GEOMETRY_LABELS.items():
            spin = QDoubleSpinBox(self)
            spin.setObjectName(name)
            spin.setDecimals(6)
            spin.setRange(-1e12 if name in ("circle_center_x", "circle_center_y") else 0, 1e12)
            spin.valueChanged.connect(lambda value, key=name: self._geometry_edited(key, value))
            self.geometry_spins[name] = spin
            form.addRow(tr(label), spin)
            attach_help(spin, "geometry")
            self._edit_widgets.append(spin)
        geometry_header = QHBoxLayout()
        geometry_header.addWidget(geometry_box, 1)
        geometry_header.addWidget(help_button("geometry", self))
        left.addLayout(geometry_header)
        instructions = QLabel(tr("manager.geometry.instructions"))
        instructions.setWordWrap(True)
        attach_help(instructions, "geometry")
        left.addWidget(instructions)
        self.ring = RingPreview(self)
        attach_help(self.ring, "functions")
        left.addWidget(self.ring, 1)
        layout.addLayout(left, 1)
        self.geometry_canvas = GeometryCanvas(self)
        self.geometry_canvas.geometryChanged.connect(self._canvas_edited)
        attach_help(self.geometry_canvas, "geometry")
        self._edit_widgets.append(self.geometry_canvas)
        layout.addWidget(self.geometry_canvas, 2)
        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        self.geometry_tab = tab
        self.tabs.addTab(tab, tr("manager.geometry.tab"))

    def _build_raw_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        raw_header = QHBoxLayout()
        self.raw_note = QLabel(tr("manager.raw.note"))
        self.raw_note.setWordWrap(True)
        raw_header.addWidget(self.raw_note, 1)
        raw_header.addWidget(help_button("raw_json", self))
        layout.addLayout(raw_header)
        self.raw_editor = QPlainTextEdit(self)
        self.raw_editor.setObjectName("rawJsonEditor")
        self.raw_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_editor.textChanged.connect(self._raw_edited)
        layout.addWidget(with_help(self.raw_editor, "raw_json"))
        row = QHBoxLayout()
        self.apply_raw_button = button(tr("manager.raw.apply"), "applyRawJson", self.apply_raw)
        self.discard_raw_button = button(tr("manager.raw.discard"), "discardRawJson", self.discard_raw)
        attach_help(self.apply_raw_button, "raw_json")
        attach_help(self.discard_raw_button, "raw_json")
        row.addWidget(self.apply_raw_button)
        row.addWidget(self.discard_raw_button)
        row.addStretch()
        layout.addLayout(row)
        self.tabs.addTab(tab, tr("manager.raw.tab"))

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
            self._error(tr("manager.library.discover_failed"), exc)
            return
        self._changing = True
        self.layout_list.clear()
        for source in self.sources:
            if source.builtin:
                text = tr("manager.library.item.bundled", identifier=source.identifier, format=source.format)
            else:
                text = tr("manager.library.item.user", identifier=source.identifier, format=source.format)
            if source.overrides:
                text += tr("manager.library.item.overrides_suffix", count=len(source.overrides))
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, source)
            if source.overrides:
                item.setToolTip(str(source.path) + tr("manager.library.item.tooltip_overrides", paths="\n".join(map(str, source.overrides))))
            else:
                item.setToolTip(str(source.path))
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
            self.validation_label.setText(tr("manager.validation.open_failed", error=str(exc)))
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
        self.save_button.setText(tr("manager.actions.save_active") if active and self.source and self.source.format == "json" else tr("manager.actions.save"))
        self.save_button.setToolTip(tr("manager.actions.save_tooltip"))
        if self.source:
            provenance = tr("manager.state.source_bundled") if self.source.builtin else tr("manager.state.source_user")
            if self.source.overrides:
                overrides = tr("manager.state.overrides_inline", paths="; ".join(map(str, self.source.overrides)))
            else:
                overrides = ""
            self.source_label.setText(tr("manager.state.source_descriptor", identifier=self.source.identifier,
                                         provenance=provenance, format=self.source.format, path=self.source.path) + overrides)
        elif self.draft:
            self.source_label.setText(tr("manager.state.new_draft"))
        dirty = bool(self.draft and self.draft.dirty)
        revision = self.draft.revision if self.draft else self.source_revision
        state = tr("manager.state.unapplied_raw") if raw_pending else tr("manager.state.unsaved") if dirty else tr("manager.state.saved") if self.draft else tr("manager.state.no_document")
        applied = self.runtime_status.get("applied") or {} if self.runtime_status else {}
        exact = bool(self.source and revision and applied.get("identifier") == self.source.identifier and applied.get("revision") == revision)
        running = tr("manager.state.running") if exact else tr("manager.state.not_running")
        if exact and dirty:
            running = tr("manager.state.running_with_draft", running=running)
        self.revision_label.setText(tr("manager.state.summary", state=state,
                                       revision=revision[:12] if revision else tr("manager.common.none"), running=running))
        self.revision_label.setToolTip(tr("manager.state.revision_tooltip",
                                          revision=revision or tr("manager.common.none"), applied=applied.get("revision") or tr("manager.common.none")))
        self.setWindowTitle(("* " if dirty else "") + tr("manager.window.title"))
        self.cancel_pending_button.setVisible(self.pending_removal is not None)
        if self.pending_removal:
            pending = self.pending_removal
            if pending.operation == "rename":
                pending_text = tr("manager.state.pending_rename",
                                  replacement=pending.replacement_identifier, revision=pending.replacement_revision[:12])
            else:
                pending_text = tr("manager.state.pending_deletion",
                                  replacement=pending.replacement_identifier, revision=pending.replacement_revision[:12])
            self.pending_label.setText(pending_text)
        elif self.activation_request:
            identifier, requested_revision, instance = self.activation_request
            acknowledged = bool(self.runtime_status and self.runtime_status.get("instance_id") == instance
                                and self.runtime_status.get("state") == "applied"
                                and applied.get("identifier") == identifier
                                and applied.get("revision") == requested_revision)
            if acknowledged:
                self.activation_request = None
                self.pending_label.setText(tr("manager.state.acknowledged", identifier=identifier, revision=requested_revision[:12]))
                self.statusBar().showMessage(tr("manager.state.applied", identifier=identifier, revision=requested_revision[:12]), 7000)
            else:
                self.pending_label.setText(tr("manager.state.requested", identifier=identifier, revision=requested_revision[:12]))
        else:
            self.pending_label.clear()
        self._render_runtime_status()

    def _render_runtime_status(self):
        if self.config_error:
            config = tr("manager.state.config_error", error=self.config_error)
        else:
            value = self.configuration.get("main", "layout", fallback="").strip()
            if value:
                config = tr("manager.state.config_request", identifier=value)
            else:
                config = tr("manager.state.config_default")
        status = self.runtime_status
        if status:
            request, applied = status.get("requested") or {}, status.get("applied") or {}
            requested = tr("manager.state.identity", identifier=request.get("identifier") or "?",
                           revision=(request.get("revision") or "?")[:12])
            running = tr("manager.state.identity", identifier=applied.get("identifier") or tr("manager.common.none"),
                         revision=(applied.get("revision") or "?")[:12])
            text = tr("manager.state.driver_summary",
                      config=config, state=translated_state(status.get("state", "unknown")), requested=requested,
                      running=running, generation=status.get("generation", "?"))
            if not status.get("recovery_current", False):
                text += tr("manager.state.recovery_warning",
                           detail=status.get("recovery_error") or tr("manager.state.recovery_detail"))
            if status.get("error"):
                text += "\n" + str(status["error"])
            self.runtime_label.setToolTip(tr("manager.state.instance_tooltip",
                                             instance=status.get("instance_id"), requested=request.get("revision"),
                                             applied=applied.get("revision")))
        else:
            text = tr("manager.state.offline_summary", config=config, error=self.runtime_error)
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
            self.validation_label.setText(tr("manager.validation.valid_layout"))
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
            self.mode_label.setText(tr("manager.rules.mapping.none_selected"))
        elif not profile:
            self.mode_label.setText(tr("manager.rules.mapping.empty"))
        else:
            names = function_names(profile)
            direct = any(key in profile for key in DIRECTIONS)
            if names and direct:
                actual = tr("manager.rules.mapping.combined")
            elif names:
                actual = tr("manager.rules.mapping.multifunction")
            else:
                actual = tr("manager.rules.mapping.single")
            self.mode_label.setText(tr("manager.rules.mapping.suffix", description=actual))
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
            if keys:
                description = " + ".join(keys)
                if action.get("command"):
                    description = tr("manager.actions.item.key_command", keys=description)
            elif action.get("command"):
                description = tr("manager.actions.item.command", command=action["command"])
            else:
                description = tr("manager.actions.item.control")
            if isinstance(action.get("value"), list):
                description = tr("manager.actions.item.value_suffix", description=description,
                                 values=", ".join(map(str, action["value"])))
            if action.get("modifier"):
                description = tr("manager.actions.item.modifier_prefix", modifier=action["modifier"], description=description)
            if action.get("title"):
                text = tr("manager.actions.item.titled", index=index + 1, title=action["title"], description=description)
            else:
                text = tr("manager.actions.item.plain", index=index + 1, description=description)
            text += tr("manager.actions.item.hold", trigger=translated_trigger(action.get("trigger", "release")),
                       duration=action.get("duration", 0))
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
        self.statusBar().showMessage(tr("manager.functions.preview_status"), 7000)

    def _ask_name(self, title, label, current="", *, identifier=False):
        value, accepted = QInputDialog.getText(self, title, label, text=current)
        if not accepted:
            return None
        value = value.strip()
        if not value:
            self._error(title, tr("manager.library.name_required"))
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
        name = self._ask_name(tr("manager.rules.add_title"), tr("manager.rules.name_label"))
        if name is None:
            return
        profiles = self.draft.document["app_shortcuts"]
        if name in profiles:
            self._error(tr("manager.rules.duplicate_title"), tr("manager.rules.duplicate_body"))
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
            self._error(tr("manager.rules.fallback_title"), tr("manager.rules.fallback_rename_body"))
            return
        new = self._ask_name(tr("manager.rules.rename_title"), tr("manager.rules.name_label"), name)
        if new is None:
            return
        try:
            self.draft.document["app_shortcuts"] = rename_ordered(self.draft.document["app_shortcuts"], name, new)
        except ValueError as exc:
            self._error(tr("manager.rules.duplicate_title"), exc)
            return
        self.app_list.currentItem().setText(new)
        self._form_changed()

    def remove_rule(self):
        name = self._rule_name()
        if not self._editable() or not name:
            return
        if name == "none":
            self._error(tr("manager.rules.fallback_title"), tr("manager.rules.fallback_remove_body"))
            return
        if QMessageBox.question(self, tr("manager.rules.remove_title"), tr("manager.rules.remove_body", name=repr(name))) != QMessageBox.StandardButton.Yes:
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
        name = self._ask_name(tr("manager.functions.add_title"), tr("manager.functions.add_label"))
        if name is None:
            return
        if name in profile or name in DIRECTIONS:
            self._error(tr("manager.functions.invalid_name_title"), tr("manager.functions.name_conflict_body"))
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
        new = self._ask_name(tr("manager.functions.rename_title"), tr("manager.functions.rename_label"), old)
        if new is None:
            return
        if new in DIRECTIONS:
            self._error(tr("manager.functions.reserved_title"), tr("manager.functions.reserved_body"))
            return
        try:
            replacement = rename_ordered(profile, old, new)
        except ValueError as exc:
            self._error(tr("manager.functions.duplicate_title"), exc)
            return
        self.draft.document["app_shortcuts"][self._rule_name()] = replacement
        self._rebuild_functions(new)
        self._form_changed()

    def remove_function(self):
        item = self.function_list.currentItem()
        profile = self._profile()
        if not self._editable() or item is None or profile is None:
            return
        if QMessageBox.question(self, tr("manager.functions.remove_title"), tr("manager.functions.remove_body", name=repr(item.text()))) != QMessageBox.StandardButton.Yes:
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
            self._error(tr("manager.functions.invalid_metadata_title"), exc)
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
        self.validation_label.setText(tr("manager.raw.pending_note"))
        self._update_state()

    def apply_raw(self):
        if not self.draft or not self.draft.raw_pending:
            return True
        try:
            self.draft.apply_raw()
        except ValueError as exc:
            self.validation_label.setText(tr("manager.raw.invalid_body", error=str(exc)))
            self.validation_label.setStyleSheet("color: #c34735")
            self.tabs.setCurrentIndex(2)
            return False
        self._populate_form()
        self._validate_inline()
        return True

    def discard_raw(self):
        if not self.draft or not self.draft.raw_pending:
            return
        if QMessageBox.question(self, tr("manager.raw.discard_title"), tr("manager.raw.discard_body")) != QMessageBox.StandardButton.Yes:
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
        choice = QMessageBox.question(self, tr("manager.raw.apply_title"), tr("manager.raw.apply_body"),
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
        self.validation_label.setText(tr("manager.validation.valid_raw") if self.draft.raw_pending else tr("manager.validation.valid_layout_plain"))
        self.validation_label.setStyleSheet("")

    def _guard_navigation(self):
        if self.pending_removal:
            self._error(tr("manager.operations.guard.pending_title"), tr("manager.operations.guard.pending_body"))
            return False
        if not self.draft or not self.draft.dirty:
            return True
        choice = QMessageBox.question(self, tr("manager.operations.guard.draft_title"), tr("manager.operations.guard.draft_body"),
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
        value = self._ask_name(tr("manager.operations.copy.title"), tr("manager.operations.copy.label"), suggestion or self._suggested_identifier, identifier=True)
        if value is None:
            return None
        try:
            sources = discover_layouts(self.config_dir, self.install_dir)
            if any(source.identifier == value for source in sources):
                raise ValueError(tr("manager.operations.copy.identifier_exists"))
            path = user_layout_path(self.config_dir, value)
            if path.exists():
                raise ValueError(tr("manager.operations.copy.destination_exists"))
        except (OSError, ValueError) as exc:
            self._error(tr("manager.operations.copy.failed_title"), exc)
            return None
        return value, path

    def _save_copy(self):
        if not self.draft:
            return False
        try:
            layout = normalize_document(self.draft.document)
        except ValueError as exc:
            self._error(tr("manager.operations.copy.invalid_title"), exc)
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
            self._error(tr("manager.operations.copy.copy_failed_title"), exc)
            return False
        self._suggested_identifier = identifier + "_copy"
        self.refresh_layouts()
        self._populate_form()
        self.statusBar().showMessage(tr("manager.operations.copy.saved_status"), 7000)
        return True

    def _check_source_revision(self):
        if not self.source or not self.source_revision:
            return
        if revision_bytes(self.source.path.read_bytes()) != self.source_revision:
            raise ValueError(tr("manager.operations.save.external_change"))

    def _conflict_dialog(self, error):
        box = QMessageBox(QMessageBox.Icon.Warning, tr("manager.operations.rename.conflict_title"), str(error), parent=self)
        reload_button = box.addButton(tr("manager.operations.rename.reload_button"), QMessageBox.ButtonRole.DestructiveRole)
        copy_button = box.addButton(tr("manager.operations.rename.copy_button"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() == copy_button:
            return self._save_copy()
        if box.clickedButton() == reload_button:
            if QMessageBox.question(self, tr("manager.operations.rename.discard_title"), tr("manager.operations.rename.discard_body")) == QMessageBox.StandardButton.Yes:
                try:
                    source = resolve_layout(self.source.identifier, self.config_dir, self.install_dir)
                except (OSError, ValueError) as exc:
                    self._error(tr("manager.operations.reload.external_failed_title"), exc)
                else:
                    self.open_source(source)
        return False

    def save_current(self):
        if not self.draft or self.pending_removal:
            return False
        if self.source and self.source.builtin:
            self._error(tr("manager.operations.save.read_only_title"), tr("manager.operations.save.read_only_body"))
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
            if QMessageBox.question(self, tr("manager.operations.save.confirm_title"), tr("manager.operations.save.confirm_body"),
                                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Save:
                return False
        try:
            layout = normalize_document(self.draft.document)
            revision = save_layout(self.source.path, layout, expected_revision=self.source_revision, overwrite=True)
        except (OSError, ValueError) as exc:
            self._error(tr("manager.operations.save.failed_title"), exc)
            return False
        self.source_revision = revision
        self.draft.mark_saved(layout, revision)
        self._populate_form()
        self._validate_inline()
        self.statusBar().showMessage(tr("manager.operations.save.saved_status"), 7000)
        return True

    def reload_layout(self):
        if not self.source or not self._guard_navigation():
            return
        try:
            source = resolve_layout(self.source.identifier, self.config_dir, self.install_dir)
        except (OSError, ValueError) as exc:
            self._error(tr("manager.operations.reload.failed_title"), exc)
            return
        self.open_source(source)

    def import_layout(self):
        if not self._guard_navigation():
            return
        path, _filter = QFileDialog.getOpenFileName(self, tr("manager.operations.import.dialog_title"), "", tr("manager.operations.import.filter"))
        if not path:
            return
        try:
            layout = parse_source(Path(path), Path(path).read_bytes())
        except (OSError, ValueError, SyntaxError) as exc:
            self._error(tr("manager.operations.import.failed_title"), tr("manager.operations.import.failed_body", error=str(exc)))
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
        self.validation_label.setText(tr("manager.validation.imported", path=path, count=len(fields)))

    def export_layout(self):
        if not self.draft or not self._resolve_raw():
            return
        try:
            layout = normalize_document(self.draft.document)
        except ValueError as exc:
            self._error(tr("manager.operations.export.invalid_title"), exc)
            return
        suggestion = (self.source.identifier if self.source else self._suggested_identifier) + ".json"
        filename, _filter = QFileDialog.getSaveFileName(self, tr("manager.operations.export.dialog_title"), suggestion, tr("manager.operations.export.filter"))
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            protected = {source.path.resolve() for source in self.sources}
            protected.update(shadow.resolve() for source in self.sources for shadow in source.overrides)
            if path.resolve() in protected:
                raise ValueError(tr("manager.operations.export.protected"))
            expected = revision_bytes(path.read_bytes()) if path.exists() else None
            if expected and QMessageBox.question(self, tr("manager.operations.export.replace_title"), tr("manager.operations.export.replace_body", path=path)) != QMessageBox.StandardButton.Yes:
                return
            save_layout(path, layout, expected_revision=expected, overwrite=expected is not None)
        except (OSError, ValueError) as exc:
            self._error(tr("manager.operations.export.failed_title"), exc)
            return
        self.statusBar().showMessage(tr("manager.operations.export.saved_status", path=path), 7000)

    def _linux_adapter(self):
        if self.offline:
            raise RuntimeError(tr("manager.runtime.disabled"))
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
                self.runtime_error = tr("manager.runtime.unavailable", error=str(exc))
        bounds = self.runtime_status.get("device_geometry") if self.runtime_status else None
        self.geometry_canvas.set_device_bounds(bounds)
        self._refresh_ring()
        if self.pending_removal and self.runtime_status:
            if self.runtime_status.get("instance_id") != self.pending_removal.instance_id:
                self.pending_removal = None
                self._error(tr("manager.state.instance_changed_title"), tr("manager.state.instance_changed_body"))
            elif replacement_acknowledged(self.runtime_status, self.pending_removal):
                self._finish_pending_removal()
            elif self.runtime_status.get("state") == "rejected":
                self.pending_removal = None
                self._error(tr("manager.state.replacement_rejected_title"), tr("manager.state.replacement_rejected_body"))
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
        box = QMessageBox(QMessageBox.Icon.Warning, tr("manager.trust.confirm_title"),
                          tr("manager.trust.confirm_body"),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.setDetailedText("\n\n".join(f"{path}\n{command}" for path, command in fields))
        return box.exec() == QMessageBox.StandardButton.Yes

    def activate_current(self):
        if not self.source or not self.draft or self.draft.dirty or self.pending_removal:
            return
        self.poll_runtime()
        if not self.runtime_status or self.config_error:
            self._error(tr("manager.trust.unavailable_title"), self.config_error or self.runtime_error)
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
            self._error(tr("manager.trust.activation_failed_title"), exc)
            return
        self.activation_request = (self.source.identifier, loaded.revision, self.runtime_status.get("instance_id"))
        self.statusBar().showMessage(tr("manager.trust.requested_status", identifier=self.source.identifier, revision=loaded.revision[:12]), 10000)
        self.poll_runtime()

    def trusted_conversion(self):
        if not self.source or self.source.format != "python" or not self._guard_navigation():
            return
        choice = QMessageBox.warning(self, tr("manager.trust.convert_title"),
                                     tr("manager.trust.convert_body", path=self.source.path),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            loaded = self._linux_adapter().load_layout(self.source.identifier, self.config_dir, self.install_dir, trusted_python=True)
        except Exception as exc:
            self._error(tr("manager.trust.convert_failed_title"), exc)
            return
        self._suggested_identifier = self.source.identifier + "_converted"
        self.source = None
        self.source_revision = None
        self.draft = DocumentDraft.opened(loaded.layout)
        self.draft.baseline = None
        self._populate_form()
        self._highlight_source()
        self.validation_label.setText(tr("manager.validation.trusted_converted"))

    def _assert_user_source(self):
        if not self.source or self.source.builtin or self.source.provenance != "user":
            raise ValueError(tr("manager.operations.ownership.user_only"))
        contained = user_layout_path(self.config_dir, self.source.identifier)
        if self.source.path.is_symlink() or self.source.path.parent.resolve() != contained.parent.resolve():
            raise ValueError(tr("manager.operations.ownership.symlink_refused"))

    def _prepare_removal(self):
        if not self.source or not self._guard_navigation():
            return False
        self.poll_runtime()
        if not self.runtime_status or not self.runtime_status.get("instance_id") or self.config_error:
            self._error(tr("manager.operations.refused.offline_title"), tr("manager.operations.refused.offline_body"))
            return False
        if self.draft and self.draft.dirty:
            # A Discard choice permits the operation, but must not accidentally
            # include discarded local changes in the renamed file.
            self.open_source(self.source)
        try:
            self._assert_user_source()
            self._check_source_revision()
        except (OSError, ValueError) as exc:
            self._error(tr("manager.operations.refused.title"), exc)
            return False
        return True

    def _require_still_inactive(self, identifier):
        self.poll_runtime()
        if not self.runtime_status or self.config_error or self._source_is_referenced(identifier):
            raise ValueError(tr("manager.operations.refused.changed"))

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
            self._error(tr("manager.operations.rename.failed_title"), tr("manager.operations.rename.failed_body", error=str(exc)))
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
                self._error(tr("manager.operations.delete.replace_required_title"), tr("manager.operations.delete.replace_required_body"))
                return
            identifier, accepted = QInputDialog.getItem(self, tr("manager.operations.delete.select_title"), tr("manager.operations.delete.select_label"), choices, editable=False)
            if not accepted:
                return
            try:
                loaded = self._linux_adapter().load_layout(identifier, self.config_dir, self.install_dir)
                if not self._confirm_commands(loaded.layout):
                    return
                if QMessageBox.question(self, tr("manager.operations.delete.confirm_title"), tr("manager.operations.delete.confirm_body", name=repr(original.identifier), replacement=repr(identifier), revision=loaded.revision[:12])) != QMessageBox.StandardButton.Yes:
                    return
                replacement = self._linux_adapter().activate_layout(self.config_dir, identifier, self.install_dir, expected_revision=loaded.revision)
                self._begin_pending("deletion", original, revision, replacement)
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._error(tr("manager.operations.delete.replacement_failed_title"), exc)
                return
        else:
            if QMessageBox.question(self, tr("manager.operations.delete.confirm_plain_title"), tr("manager.operations.delete.confirm_plain_body", path=original.path)) != QMessageBox.StandardButton.Yes:
                return
            try:
                self._require_still_inactive(original.identifier)
                self._linux_adapter().remove_user_layout(
                    self.config_dir, original, revision, self.install_dir,
                    instance_id=self.runtime_status["instance_id"])
            except (OSError, ValueError, RuntimeError, ImportError) as exc:
                self._error(tr("manager.operations.delete.failed_title"), exc)
                return
            self.source = None
            self.source_revision = None
            self.draft = None
            self._populate_form()
            self.refresh_layouts()
            self.source_label.setText(tr("manager.operations.delete.deleted_status"))
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
                raise ValueError(tr("manager.operations.rename.source_changed"))
            self._linux_adapter().remove_user_layout(
                self.config_dir, original, pending.original_revision, self.install_dir,
                instance_id=pending.instance_id,
                replacement=(pending.replacement_identifier, pending.replacement_revision))
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self.pending_removal = None
            self._error(tr("manager.operations.rename.original_title"), tr("manager.operations.rename.original_body", error=str(exc)))
            return
        self.pending_removal = None
        self.refresh_layouts()
        try:
            self.open_source(resolve_layout(pending.replacement_identifier, self.config_dir, self.install_dir))
        except (OSError, ValueError) as exc:
            self._error(tr("manager.operations.display_replacement_failed_title"), exc)
        if pending.operation == "rename":
            self.statusBar().showMessage(tr("manager.operations.rename.saved_status", identifier=pending.original_identifier), 8000)
        else:
            self.statusBar().showMessage(tr("manager.operations.delete.saved_status", identifier=pending.original_identifier), 8000)

    def cancel_pending(self):
        if self.pending_removal:
            self.pending_removal = None
            self._update_state()
            self.statusBar().showMessage(tr("manager.operations.removal.canceled_status"), 12000)

    def closeEvent(self, event):
        if self.pending_removal:
            if QMessageBox.question(self, tr("manager.operations.removal.close_title"), tr("manager.operations.removal.close_body"),
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
    initialize_i18n(app)
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
