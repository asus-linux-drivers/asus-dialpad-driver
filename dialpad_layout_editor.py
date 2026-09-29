"""Portable draft state and structured Qt controls for the layout manager.

Nothing in this module imports the Linux adapter or evaluates layout commands. The
tool-ring preview replays driver-shaped feedback through the shared overlay canvas.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from dialpad_layout import dumps_layout, event_names, normalize_document, parse_json
from dialpad_overlay import BOX_HEIGHT, BOX_WIDTH, OverlayCanvas

DIRECTIONS = ("center", "clockwise", "counterclockwise")
GEOMETRY_LABELS = {
    "circle_center_x": "Circle center X",
    "circle_center_y": "Circle center Y",
    "circle_diameter": "Outer diameter",
    "center_button_diameter": "Center button diameter",
    "top_right_icon_width": "Activation region width",
    "top_right_icon_height": "Activation region height",
}


@dataclass
class DocumentDraft:
    """The last valid form and an unapplied raw draft are independent buffers."""

    document: dict
    revision: str | None = None
    baseline: str | None = None
    raw_text: str = ""
    raw_pending: bool = False

    @classmethod
    def opened(cls, layout, revision=None):
        serialized = dumps_layout(layout)
        return cls(deepcopy(layout.document), revision, serialized, serialized)

    @property
    def dirty(self):
        if self.raw_pending or self.baseline is None:
            return True
        try:
            return dumps_layout(normalize_document(self.document)) != self.baseline
        except ValueError:
            return True

    def edit_raw(self, text):
        self.raw_text = text
        self.raw_pending = text != self.form_json()

    def form_json(self):
        # Form controls can temporarily contain invalid geometry; raw editing is
        # still useful for correcting it, so do not validate just to display it.
        import json
        return json.dumps(self.document, ensure_ascii=False, indent=2) + "\n"

    def apply_raw(self):
        layout = parse_json(self.raw_text)
        self.document = deepcopy(layout.document)
        self.raw_pending = False
        return layout

    def discard_raw(self):
        self.raw_text = self.form_json()
        self.raw_pending = False

    def mark_saved(self, layout, revision):
        if self.raw_pending:
            raise ValueError("Apply or discard the raw JSON draft before saving")
        self.document = deepcopy(layout.document)
        self.revision = revision
        self.baseline = dumps_layout(layout)
        self.raw_text = self.baseline


def rename_ordered(mapping, old, new):
    if new != old and new in mapping:
        raise ValueError(f"{new!r} already exists")
    return {new if key == old else key: value for key, value in mapping.items()}


def move_ordered(mapping, name, offset, *, eligible=None):
    """Move only within a logical list, retaining unrelated fields in place."""
    keys = list(mapping)
    movable = [key for key in keys if eligible is None or eligible(key)]
    index = movable.index(name)
    target = index + offset
    if not 0 <= target < len(movable):
        return mapping
    other = movable[target]
    a, b = keys.index(name), keys.index(other)
    keys[a], keys[b] = keys[b], keys[a]
    return {key: mapping[key] for key in keys}


def action_items(target, direction):
    value = target.get(direction, [])
    return [value] if isinstance(value, dict) else value


def put_actions(target, direction, actions):
    """Retain the accepted singleton-object spelling until a list is needed."""
    if isinstance(target.get(direction), dict) and len(actions) == 1:
        target[direction] = actions[0]
    else:
        target[direction] = actions


def function_names(profile):
    return [key for key in profile if key not in DIRECTIONS]


def command_fields(document):
    """Return executable fields for a trust warning, without inspecting code."""
    found = []

    def collect(value, path):
        for field in ("command", "value"):
            text = value.get(field)
            if isinstance(text, str) and text:
                found.append((f"{path}.{field}", text))

    def actions(value, path):
        values = [value] if isinstance(value, dict) else value
        for index, action in enumerate(values):
            collect(action, f"{path}[{index}]")

    for rule, profile in document.get("app_shortcuts", {}).items():
        for name, value in profile.items():
            path = f"app_shortcuts.{rule}.{name}"
            if name in DIRECTIONS:
                actions(value, path)
            else:
                collect(value, path)
                for direction in DIRECTIONS:
                    if direction in value:
                        actions(value[direction], f"{path}.{direction}")
    return found


def button(text, name, callback, parent=None):
    result = QPushButton(text, parent)
    result.setObjectName(name)
    result.clicked.connect(callback)
    return result


class MetadataFields(QWidget):
    """Display metadata, including conditional icons, with lossless patching."""

    def __init__(self, original, parent=None, *, relative=False, command=False):
        super().__init__(parent)
        self.original = deepcopy(original)
        form = QFormLayout(self)
        self.fields = {}
        names = [("title", "Display title"), ("icon", "Icon file path or theme name"), ("unit", "Unit")]
        if command:
            names.insert(0, ("command", "Shell command"))
        names.append(("value", "Display-value query"))
        for key, label in names:
            value = original.get(key, "")
            if key == "value" and not isinstance(value, str):
                value = ""
            edit = QLineEdit(value, self)
            edit.setObjectName("metadata_" + key)
            if key in ("command", "value"):
                edit.setPlaceholderText("Executed by the driver only, never by this preview")
            elif key == "icon":
                edit.setPlaceholderText("Path to an SVG file, or an icon-theme name")
            self.fields[key] = edit
            form.addRow(label, edit)
            if key == "icon":
                hint = QLabel("Missing or unreadable icons fall back to the label.", self)
                hint.setObjectName("iconInputHint")
                hint.setWordWrap(True)
                form.addRow("", hint)
        self.fields["value"].setEnabled(not relative)
        if relative:
            self.fields["value"].setToolTip("Relative events use numeric values, not a display query")
        threshold_row = QHBoxLayout()
        self.threshold_enabled = QCheckBox("Override", self)
        self.threshold_enabled.setObjectName("thresholdEnabled")
        self.threshold_enabled.setChecked("treshold" in original)
        self.threshold = QDoubleSpinBox(self)
        self.threshold.setObjectName("thresholdValue")
        self.threshold.setDecimals(6)
        self.threshold.setRange(0.000001, 1e12)
        self.threshold.setSuffix(" degrees")
        self.threshold.setValue(original.get("treshold", 90))
        self.threshold.setEnabled(self.threshold_enabled.isChecked())
        self.threshold_enabled.toggled.connect(self.threshold.setEnabled)
        threshold_row.addWidget(self.threshold_enabled)
        threshold_row.addWidget(self.threshold)
        form.addRow("Rotation threshold (treshold)", threshold_row)
        self.icons = QTableWidget(0, 2, self)
        self.icons.setObjectName("conditionalIcons")
        self.icons.setHorizontalHeaderLabels(["Query result", "Icon (file path or theme name)"])
        self.icons.horizontalHeader().setStretchLastSection(True)
        self.icons.setMinimumHeight(115)
        self.icons.setMaximumHeight(180)
        for key, value in original.get("icons", {}).items():
            self.add_icon(key, value)
        form.addRow("Conditional icons", self.icons)
        controls = QHBoxLayout()
        controls.addWidget(button("Add icon", "addConditionalIcon", lambda: self.add_icon("", "")))
        controls.addWidget(button("Remove icon", "removeConditionalIcon", self.remove_icon))
        controls.addStretch()
        form.addRow(controls)
        self._initial_text = {key: edit.text() for key, edit in self.fields.items()}
        self._initial_threshold = self.threshold.value()
        self._initial_icons = self.icon_pairs()

    def add_icon(self, key, value):
        row = self.icons.rowCount()
        self.icons.insertRow(row)
        self.icons.setItem(row, 0, QTableWidgetItem(key))
        self.icons.setItem(row, 1, QTableWidgetItem(value))

    def remove_icon(self):
        if self.icons.currentRow() >= 0:
            self.icons.removeRow(self.icons.currentRow())

    def icon_pairs(self):
        return [(self.icons.item(row, 0).text(), self.icons.item(row, 1).text())
                for row in range(self.icons.rowCount())]

    def patch(self, result):
        for key, edit in self.fields.items():
            if edit.isEnabled() and edit.text() != self._initial_text[key]:
                if edit.text():
                    result[key] = edit.text()
                else:
                    result.pop(key, None)
        enabled = self.threshold_enabled.isChecked()
        if enabled and ("treshold" not in self.original or self.threshold.value() != self._initial_threshold):
            result["treshold"] = self.threshold.value()
        elif not enabled:
            result.pop("treshold", None)
        pairs = self.icon_pairs()
        if pairs != self._initial_icons:
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError("Conditional icon query results must be unique")
            result["icons"] = dict(pairs)
        return result


class FunctionDialog(QDialog):
    def __init__(self, name, original, parent=None):
        super().__init__(parent)
        self.setObjectName("functionDialog")
        self.setWindowTitle(f"Tool-ring function metadata — {name}")
        self.resize(650, 480)
        self.original = deepcopy(original)
        self.result_value = None
        layout = QVBoxLayout(self)
        self.metadata = MetadataFields(original, self, command=True)
        layout.addWidget(self.metadata)
        note = QLabel("The display title and icon are what the tool-ring slice shows. A command-only function is valid. Rotation bindings are optional.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            self.result_value = self.metadata.patch(deepcopy(self.original))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid metadata", str(exc))
            return
        super().accept()


class ActionDialog(QDialog):
    """Edit all supported action kinds without inventing dummy key bindings."""

    def __init__(self, original=None, parent=None):
        super().__init__(parent)
        self.setObjectName("actionDialog")
        self.setWindowTitle("Edit shortcut action" if original is not None else "Add shortcut action")
        self.resize(760, 790)
        self.original = deepcopy(original or {})
        self.result_value = None
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        body = QVBoxLayout(content)
        form = QFormLayout()
        self.kind = QComboBox(self)
        self.kind.setObjectName("actionKind")
        for title, kind in [("Keyboard combination", "keys"), ("Relative-axis events", "relative"),
                            ("Shell command", "command"), ("Control / selection only", "control")]:
            self.kind.addItem(title, kind)
        keys = self.original.get("key", [])
        if isinstance(keys, str):
            keys = [keys]
        current_kind = ("relative" if keys and keys[0].startswith("REL_") else "keys") if keys else (
            "command" if "command" in self.original else "control")
        self.kind.setCurrentIndex(self.kind.findData(current_kind))
        form.addRow("Action type", self.kind)
        self.modifier = QComboBox(self)
        self.modifier.setObjectName("actionModifier")
        self.modifier.setEditable(True)
        self.modifier.addItem("")
        self.modifier.addItems([name for name in event_names() if name.startswith("KEY_")])
        self.modifier.setCurrentText(self.original.get("modifier", ""))
        self.modifier.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        form.addRow("Required modifier (optional)", self.modifier)
        self.trigger = QComboBox(self)
        self.trigger.setObjectName("actionTrigger")
        self.trigger.addItems(["release", "immediate"])
        self.trigger.setCurrentText(self.original.get("trigger", "release"))
        form.addRow("Trigger", self.trigger)
        self.duration = QDoubleSpinBox(self)
        self.duration.setObjectName("actionDuration")
        self.duration.setRange(0, 1e12)
        self.duration.setDecimals(6)
        self.duration.setSuffix(" seconds")
        self.duration.setValue(self.original.get("duration", 0))
        form.addRow("Minimum hold duration", self.duration)
        self.command = QLineEdit(self.original.get("command", ""), self)
        self.command.setObjectName("actionCommand")
        self.command.setPlaceholderText("Optional alongside keys; runs only in the driver")
        form.addRow("Shell command", self.command)
        body.addLayout(form)
        self.event_group = QGroupBox("Output events — ordered combination / numeric relative values", self)
        event_layout = QHBoxLayout(self.event_group)
        catalog_column = QVBoxLayout()
        self.search = QLineEdit(self)
        self.search.setObjectName("eventSearch")
        self.search.setPlaceholderText("Search event names, e.g. volume or ctrl")
        catalog_column.addWidget(self.search)
        self.catalog = QListWidget(self)
        self.catalog.setObjectName("eventCatalog")
        self.catalog.setMinimumHeight(140)
        catalog_column.addWidget(self.catalog)
        catalog_column.addWidget(button("Add selected event", "addEvent", self.add_selected_event))
        event_layout.addLayout(catalog_column, 1)
        selected_column = QVBoxLayout()
        self.events = QTableWidget(0, 2, self)
        self.events.setObjectName("selectedEvents")
        self.events.setHorizontalHeaderLabels(["Event", "Numeric value"])
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        selected_column.addWidget(self.events)
        event_controls = QHBoxLayout()
        event_controls.addWidget(button("Remove", "removeEvent", self.remove_event))
        event_controls.addWidget(button("Up", "eventUp", lambda: self.move_event(-1)))
        event_controls.addWidget(button("Down", "eventDown", lambda: self.move_event(1)))
        selected_column.addLayout(event_controls)
        event_layout.addLayout(selected_column, 1)
        body.addWidget(self.event_group)
        values = self.original.get("value", [])
        for index, key in enumerate(keys):
            value = values[index] if isinstance(values, list) and index < len(values) else 1
            self.add_event(key, value)
        self.metadata = MetadataFields(self.original, self, relative=current_kind == "relative")
        metadata_box = QGroupBox("Display metadata — static preview never runs queries", self)
        QVBoxLayout(metadata_box).addWidget(self.metadata)
        body.addWidget(metadata_box)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.error = QLabel(self)
        self.error.setObjectName("actionError")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c34735")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.filter_events)
        self.catalog.itemDoubleClicked.connect(lambda _item: self.add_selected_event())
        self.kind.currentIndexChanged.connect(self.kind_changed)
        self._initial_kind = current_kind
        self._initial_events = self.event_pairs()
        self._initial_duration = self.duration.value()
        self.kind_changed()

    def filter_events(self):
        self.catalog.clear()
        prefix = "REL_" if self.kind.currentData() == "relative" else ("KEY_", "BTN_")
        search = self.search.text().casefold()
        self.catalog.addItems([name for name in event_names() if name.startswith(prefix) and search in name.casefold()])

    def kind_changed(self):
        relative = self.kind.currentData() == "relative"
        self.event_group.setEnabled(self.kind.currentData() in ("keys", "relative"))
        self.events.setColumnHidden(1, not relative)
        self.command.setEnabled(self.kind.currentData() != "control")
        self.metadata.fields["value"].setEnabled(not relative)
        self.filter_events()

    def add_event(self, name, value=1):
        row = self.events.rowCount()
        self.events.insertRow(row)
        item = QTableWidgetItem(name)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.events.setItem(row, 0, item)
        numeric = QLineEdit(str(value), self.events)
        numeric.setObjectName("relativeEventValue")
        numeric.setToolTip("Signed integer event value")
        self.events.setCellWidget(row, 1, numeric)
        self.events.selectRow(row)

    def add_selected_event(self):
        item = self.catalog.currentItem()
        if item:
            self.add_event(item.text())

    def remove_event(self):
        if self.events.currentRow() >= 0:
            self.events.removeRow(self.events.currentRow())

    def event_pairs(self):
        return [(self.events.item(row, 0).text(), int(self.events.cellWidget(row, 1).text()))
                for row in range(self.events.rowCount())]

    def move_event(self, offset):
        index = self.events.currentRow()
        try:
            pairs = self.event_pairs()
        except ValueError:
            self.error.setText("Relative event values must be signed integers")
            return
        if index < 0 or not 0 <= index + offset < len(pairs):
            return
        pairs[index], pairs[index + offset] = pairs[index + offset], pairs[index]
        self.events.setRowCount(0)
        for name, value in pairs:
            self.add_event(name, value)
        self.events.selectRow(index + offset)

    def accept(self):
        try:
            result = deepcopy(self.original)
            kind = self.kind.currentData()
            pairs = self.event_pairs()
            if kind in ("keys", "relative"):
                keys = [key for key, _value in pairs]
                if not keys:
                    raise ValueError("Add at least one event, or choose a command/control action")
                if kind == "relative" and any(not key.startswith("REL_") for key in keys):
                    raise ValueError("Remove keyboard events before using relative-axis mode")
                if kind == "keys" and any(key.startswith("REL_") for key in keys):
                    raise ValueError("Remove relative events before using keyboard mode")
                if pairs != self._initial_events or kind != self._initial_kind:
                    result["key"] = keys[0] if len(keys) == 1 and not isinstance(self.original.get("key"), list) else keys
                if kind == "relative":
                    result["value"] = [value for _key, value in pairs]
                elif isinstance(result.get("value"), list):
                    result.pop("value")
            else:
                result.pop("key", None)
                if isinstance(result.get("value"), list):
                    result.pop("value")
            command = self.command.text() if kind != "control" else ""
            command_changed = command != self.original.get("command", "") or kind != self._initial_kind
            if command_changed:
                if command:
                    result["command"] = command
                else:
                    result.pop("command", None)
            if kind == "command" and not command and (command_changed or "command" not in self.original):
                raise ValueError("Enter a shell command, or choose a control-only action")
            if self.modifier.currentText():
                result["modifier"] = self.modifier.currentText()
            else:
                result.pop("modifier", None)
            if self.trigger.currentText() != self.original.get("trigger", "release"):
                result["trigger"] = self.trigger.currentText()
            if self.duration.value() != self._initial_duration:
                result["duration"] = self.duration.value()
            self.result_value = self.metadata.patch(result)
            # Validate in a minimal complete document using the same validator.
            normalize_document({"schema_version": 1, "geometry": {
                "circle_center_x": 100, "circle_center_y": 100, "circle_diameter": 100,
                "center_button_diameter": 40, "top_right_icon_width": 20, "top_right_icon_height": 20,
            }, "app_shortcuts": {"none": {"center": [self.result_value]}}})
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class GeometryCanvas(QWidget):
    geometryChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("geometryCanvas")
        self.setMinimumSize(360, 300)
        self.geometry_data = {}
        self.device_bounds = None
        self._drag = None
        self._drag_bounds = None
        self.setToolTip("Drag the circle to move it. Drag square handles to resize the outer circle, center button, or top-right activation region.")

    def set_geometry(self, geometry):
        self.geometry_data = dict(geometry)
        self.update()

    def set_device_bounds(self, bounds):
        self.device_bounds = dict(bounds) if bounds else None
        self.update()

    def bounds(self):
        if self._drag_bounds:
            return self._drag_bounds
        if self.device_bounds:
            b = self.device_bounds
            return b["min_x"], b["min_y"], b["max_x"], b["max_y"]
        g = self.geometry_data
        radius = max(1, g.get("circle_diameter", 100)) / 2
        x, y = g.get("circle_center_x", 100), g.get("circle_center_y", 100)
        return (min(0, x - radius * 1.3), min(0, y - radius * 1.3),
                max(x + radius * 1.5, g.get("top_right_icon_width", 20) * 2),
                max(y + radius * 1.5, g.get("top_right_icon_height", 20) * 2))

    def transform(self):
        x0, y0, x1, y1 = self.bounds()
        scale = min((self.width() - 48) / max(1, x1 - x0), (self.height() - 72) / max(1, y1 - y0))
        origin = QPointF((self.width() - (x1 - x0) * scale) / 2,
                         42 + (self.height() - 66 - (y1 - y0) * scale) / 2)
        return origin, scale

    def point(self, x, y):
        origin, scale = self.transform()
        x0, y0, _x1, _y1 = self.bounds()
        return origin + QPointF((x - x0) * scale, (y - y0) * scale)

    def handles(self):
        g = self.geometry_data
        x, y = g["circle_center_x"], g["circle_center_y"]
        _x0, y0, x1, _y1 = self.bounds()
        return {"outer": self.point(x + g["circle_diameter"] / 2, y),
                "inner": self.point(x, y + g["center_button_diameter"] / 2),
                "activation": self.point(x1 - g["top_right_icon_width"], y0 + g["top_right_icon_height"])}

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(self.palette().text().color())
        label = "Measured device bounds · absolute touchpad coordinates" if self.device_bounds else "Schematic · device bounds unknown · absolute coordinates"
        painter.drawText(QRectF(8, 4, self.width() - 16, 30), Qt.AlignmentFlag.AlignCenter, label)
        if not self.geometry_data:
            return
        g = self.geometry_data
        x0, y0, x1, y1 = self.bounds()
        rect = QRectF(self.point(x0, y0), self.point(x1, y1))
        painter.setPen(QPen(QColor("#8b99a7"), 1.5))
        painter.setBrush(QColor("#eaf0f5"))
        painter.drawRoundedRect(rect, 8, 8)
        activation = QRectF(self.point(x1 - g["top_right_icon_width"], y0),
                            self.point(x1, y0 + g["top_right_icon_height"]))
        painter.setBrush(QColor("#e8ad5577"))
        painter.setPen(QPen(QColor("#ad7924"), 2))
        painter.drawRect(activation)
        painter.drawText(activation, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, "Activation")
        center = self.point(g["circle_center_x"], g["circle_center_y"])
        _origin, scale = self.transform()
        painter.setPen(QPen(QColor("#3d78b1"), 2))
        painter.setBrush(QColor("#6ca5d655"))
        r = g["circle_diameter"] * scale / 2
        painter.drawEllipse(center, r, r)
        painter.setBrush(QColor("#6ca5d699"))
        r = g["center_button_diameter"] * scale / 2
        painter.drawEllipse(center, r, r)
        painter.drawText(QRectF(center.x() - 60, center.y() - 12, 120, 24), Qt.AlignmentFlag.AlignCenter, "Drag to move")
        painter.setBrush(QColor("#ffffff"))
        for point in self.handles().values():
            painter.drawRect(QRectF(point.x() - 5, point.y() - 5, 10, 10))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.geometry_data:
            return
        point = event.position()
        for name, handle in self.handles().items():
            if (point - handle).manhattanLength() <= 18:
                self._drag = (name, point, dict(self.geometry_data))
                break
        if self._drag is None:
            g = self.geometry_data
            center = self.point(g["circle_center_x"], g["circle_center_y"])
            _origin, scale = self.transform()
            if math.hypot(point.x() - center.x(), point.y() - center.y()) <= g["circle_diameter"] * scale / 2:
                self._drag = ("move", point, dict(g))
        if self._drag:
            self._drag_bounds = self.bounds()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if not self._drag:
            return
        name, start, original = self._drag
        _origin, scale = self.transform()
        delta = (event.position() - start) / scale
        g = dict(original)
        if name == "move":
            g["circle_center_x"] = round(original["circle_center_x"] + delta.x(), 3)
            g["circle_center_y"] = round(original["circle_center_y"] + delta.y(), 3)
        elif name == "outer":
            g["circle_diameter"] = round(max(original["center_button_diameter"], original["circle_diameter"] + delta.x() * 2, 1), 3)
        elif name == "inner":
            g["center_button_diameter"] = round(min(original["circle_diameter"], max(1, original["center_button_diameter"] + delta.y() * 2)), 3)
        else:
            g["top_right_icon_width"] = round(max(1, original["top_right_icon_width"] - delta.x()), 3)
            g["top_right_icon_height"] = round(max(1, original["top_right_icon_height"] + delta.y()), 3)
        self.geometry_data = g
        self.geometryChanged.emit(g)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self._drag_bounds = None
        self.unsetCursor()
        self.update()


class RingPreview(QWidget):
    """Static tool-ring appearance drawn by the same canvas as the live overlay.

    The preview replays one complete feedback payload rebuilt from the stored
    document, so it shows the live rendering without a socket or any execution.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("functionRingPreview")
        self.names = []
        self.titles = []
        self.icons = []
        self.minimum = 4
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.caption = QLabel(f"Tool-ring appearance · live overlay canvas · {BOX_WIDTH}×{BOX_HEIGHT}", self)
        self.caption.setObjectName("ringPreviewCaption")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.canvas = OverlayCanvas(self, background=QColor("#30343b"))
        layout.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignHCenter)
        self.note = QLabel(self)
        self.note.setObjectName("ringPreviewNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch(1)
        self.set_profile({})


    def set_profile(self, profile, minimum=4):
        self.names = function_names(profile)
        self.titles = [profile[name].get("title", name) for name in self.names]
        self.icons = [profile[name].get("icon") for name in self.names]
        self.minimum = max(1, minimum)
        slices = max(len(self.names), self.minimum)
        padding = slices - len(self.names)
        # The driver's own keys: titles in stored order clockwise from the top,
        # base icons only, and None for every unnamed padding slice.
        self.canvas.set_feedback({
            "titles": self.titles + [None] * padding,
            "icons": self.icons + [None] * padding,
            "title": None,
            "value": None,
            "unit": None,
            "value_angle_start": None,
            "value_show_only_progress": True,
            "input": None,
            "enabled": True,
        })
        if self.names:
            order = (f"{len(self.names)} named functions · {slices} slices clockwise from the top · "
                     f"{padding} empty padding slice(s) from slices_minimum_count = {self.minimum}")
        else:
            order = ("Single function / shared controls: no named tool-ring entries, so no slice carries a "
                     "function label. Named functions in the Application rules & shortcuts tab become the "
                     "tool-ring entries.")
        self.note.setText(order + "\nStatic appearance only: commands, display-value queries, and conditional "
                                  "icon-query results are never run.")
