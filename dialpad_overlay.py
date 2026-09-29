"""Optional Qt rendering shared by the floating overlay and static editor preview.

This module only paints supplied feedback and reads local icon assets. It does
not bind sockets, import the driver, or evaluate commands or display queries.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QPainter, QPainterPath, QPen,
    QTextLayout, QTextOption,
)
from PySide6.QtWidgets import QWidget

BOX_WIDTH = 275
BOX_HEIGHT = 275
COLOR_PROGRESS = QColor("#a5988a")
COLOR_OUTER_BG = QColor("#0e131b")
COLOR_CENTER_BG = QColor("#212535")
COLOR_CENTER_FONT = QColor("#b9bab9")


def label_lines(text, font, width, maximum=2):
    """Wrap complete words where possible, eliding only the last visible line."""
    text = " ".join(str(text).split())
    if not text or width <= 0 or maximum <= 0:
        return []
    layout = QTextLayout(text, font)
    option = QTextOption()
    # QTextLine offsets count UTF-16 code units, not Python code points.
    encoded = text.encode("utf-16-le")
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    metrics = QFontMetricsF(font)
    lines = []
    layout.beginLayout()
    for index in range(maximum):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        start = line.textStart() * 2
        if index == maximum - 1:
            remainder = encoded[start:].decode("utf-16-le").strip()
            lines.append(metrics.elidedText(remainder, Qt.TextElideMode.ElideRight, width))
        else:
            end = start + line.textLength() * 2
            lines.append(encoded[start:end].decode("utf-16-le").strip())
    layout.endLayout()
    return lines


def ring_sector(outer, center, start, span):
    path = QPainterPath()
    path.moveTo(outer.center())
    path.arcTo(outer, start, span)
    path.closeSubpath()
    hole = QPainterPath()
    hole.addEllipse(center)
    return path.subtracted(hole)


class OverlayCanvas(QWidget):
    """The real 275-by-275 overlay surface, without transport or window policy."""

    def __init__(self, parent=None, *, background=Qt.GlobalColor.transparent):
        super().__init__(parent)
        self.setFixedSize(BOX_WIDTH, BOX_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.background = background
        self.enabled = True
        self.title = None
        self.titles = []
        self.icons = []
        self.center_pressed = False
        self.value = None
        self.value_angle_start = None
        self.unit = None
        self.value_show_only_progress = True

    def set_feedback(self, payload):
        """Apply the existing driver feedback semantics without showing a window."""
        if payload.get("enabled") is not None:
            self.enabled = payload["enabled"]
        value = payload.get("value")
        if payload.get("input") == "center":
            self.center_pressed = value
            if self.value_show_only_progress is True:
                self.value = None
                self.value_angle_start = None
                self.unit = None
        else:
            self.center_pressed = False
            self.value = value
            self.value_angle_start = payload.get("value_angle_start")
            self.unit = payload.get("unit")
        self.value_show_only_progress = payload.get("value_show_only_progress")
        titles = payload.get("titles", [])
        if isinstance(titles, list):
            self.titles = titles
        icons = payload.get("icons", [])
        if isinstance(icons, list):
            self.icons = icons
        self.title = payload.get("title")
        self.setToolTip("\n".join(title for title in self.titles if title))
        self.update()

    def _draw_label(self, painter, rect, text, color, *, size=11, maximum=2):
        font = QFont(self.font())
        font.setPixelSize(size)
        metrics = QFontMetricsF(font)
        lines = label_lines(text, font, rect.width(), min(maximum, int(rect.height() / metrics.height())))
        painter.setFont(font)
        painter.setPen(color)
        y = rect.center().y() - len(lines) * metrics.height() / 2
        for line in lines:
            line_rect = QRectF(rect.x(), y, rect.width(), metrics.height())
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignCenter, line)
            y += metrics.height()

    def _draw_icon(self, painter, name, rect, color):
        if not name:
            return False
        if os.path.isfile(name):
            icon = QIcon(name)
        elif os.path.isabs(name) or "/" in name or "\\" in name:
            return False
        else:
            icon = QIcon.fromTheme(name)
        if icon.isNull():
            return False
        size = max(1, round(rect.width()))
        pixmap = icon.pixmap(QSize(size, size), self.devicePixelRatioF())
        if pixmap.isNull():
            return False
        # Tint the isolated icon, never the already-painted ring/background.
        image = pixmap.toImage()
        tint = QPainter(image)
        tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tint.fillRect(image.rect(), color)
        tint.end()
        destination = QRectF(0, 0, pixmap.width() / pixmap.devicePixelRatio(),
                             pixmap.height() / pixmap.devicePixelRatio())
        destination.moveCenter(rect.center())
        painter.drawImage(destination, image)
        return True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), self.background)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not self.enabled:
            return

        diameter = min(self.width(), self.height()) - 92
        outer = QRectF(0, 0, diameter, diameter)
        outer.moveCenter(QRectF(self.rect()).center())
        center = QRectF(0, 0, diameter * 9 / 14, diameter * 9 / 14)
        center.moveCenter(outer.center())
        painter.setPen(QPen(COLOR_OUTER_BG, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_PROGRESS if self.center_pressed else COLOR_CENTER_BG)
        painter.drawEllipse(center)

        if self.value is not None:
            try:
                progress = float(self.value)
            except ValueError:
                progress = 0.0
            progress = max(min(progress, 100.0), -100.0)
            start = 90 - self.value_angle_start if self.value_angle_start is not None else 0
            painter.setBrush(COLOR_PROGRESS)
            painter.drawPath(ring_sector(outer, center, start, -360 * progress / 100))

        count = max(len(self.titles), len(self.icons))
        active_index = self.titles.index(self.title) if self.title and self.title in self.titles else -1
        for index in range(count):
            span = 360 / count
            angle = math.radians((index + .5) * span - 90)
            active = index == active_index
            painter.setPen(QPen(COLOR_OUTER_BG, 1))
            painter.setBrush(COLOR_PROGRESS if active else Qt.BrushStyle.NoBrush)
            painter.drawPath(ring_sector(outer, center, 90 - index * span, -span))

            icon_radius = (outer.width() + center.width()) / 4
            icon_size = min(22, 2 * icon_radius * math.sin(math.pi / max(2, count)) * .7)
            icon_rect = QRectF(0, 0, icon_size, icon_size)
            icon_rect.moveCenter(outer.center() + QPointF(math.cos(angle), math.sin(angle)) * icon_radius)
            icon = self.icons[index] if index < len(self.icons) else None
            if self._draw_icon(painter, icon, icon_rect, COLOR_CENTER_BG if active else COLOR_CENTER_FONT):
                continue

            title = self.titles[index] if index < len(self.titles) else None
            if not title:
                continue
            # Outside labels have room for two lines, but stay inside the window
            # and within the neighboring slice's chord. Icons share slice angles.
            radius = outer.width() / 2 + 16
            position = outer.center() + QPointF(math.cos(angle), math.sin(angle)) * radius
            width = min(94, 2 * radius * math.sin(math.pi / max(2, count)) - 8,
                        2 * (min(position.x(), self.width() - position.x()) - 6))
            height = min(32, 2 * (min(position.y(), self.height() - position.y()) - 4))
            rect = QRectF(0, 0, max(1, width), max(1, height))
            rect.moveCenter(position)
            # Labels sit outside the highlighted wedge, so retain readable color.
            self._draw_label(painter, rect, title, COLOR_CENTER_FONT)

        color = COLOR_CENTER_BG if self.center_pressed else COLOR_CENTER_FONT
        show_value = self.value is not None and self.value_show_only_progress is not True
        title_rect = QRectF(center.x() + center.width() * .1, center.center().y() - 20,
                            center.width() * .8, 40)
        if show_value:
            title_rect.translate(0, -10)
        if self.title:
            self._draw_label(painter, title_rect, self.title, color, size=13)
        if show_value:
            value_rect = QRectF(title_rect.x(), center.center().y() + 12, title_rect.width(), 22)
            value = str(self.value) + (str(self.unit) if self.unit is not None else "")
            self._draw_label(painter, value_rect, value, color, size=13, maximum=1)
