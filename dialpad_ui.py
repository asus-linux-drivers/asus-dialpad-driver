
import sys
import os
os.environ.pop("QT_STYLE_OVERRIDE", None)
import json
import socket
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath, QIcon, QImage
from PySide6.QtSvg import QSvgRenderer
import logging
import signal
import math

SOCKET_PATH = "/tmp/dialpad.sock"

SYSTEMD_JOURNAL_AVAILABLE = False
try:
    from systemd.journal import JournalHandler
    SYSTEMD_JOURNAL_AVAILABLE = True
except ImportError:
    pass

# Logging setup
logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s',
    level=os.environ.get('LOG', 'INFO')
)
log = logging.getLogger('asus-dialpad-driver-ui')
if SYSTEMD_JOURNAL_AVAILABLE:
    log.addHandler(JournalHandler())

BOX_WIDTH = 275
BOX_HEIGHT = 275

CIRCLE_DIAMETER = 1400
CENTER_BUTTON_DIAMETER = 900

COLOR_PROGRESS = QColor("#a5988a")
COLOR_OUTER_BG = QColor("#0e131b")
COLOR_CENTER_BG = QColor("#212535")
COLOR_CENTER_BG_PRESSED = QColor("#a5988a")
COLOR_CENTER_FONT = "#b9bab9"
COLOR_CENTER_PRESSED_FONT = COLOR_CENTER_BG

class FloatingWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(BOX_WIDTH, BOX_HEIGHT)

        self.drag_enabled = False
        self.drag_position = None

        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(SOCKET_PATH)
        self.sock.setblocking(False)
        log.info(f"Listening on {SOCKET_PATH}")

        self.timer = QTimer()
        self.timer.timeout.connect(self.read_socket)
        self.timer.start(50)

        self.buffer = ""
        self.enabled = False
        self.title = ""
        self.icons = []
        self.titles = []
        self.center_pressed = False
        self.value = None
        self.value_angle_start = None
        self.unit = None
        self.value_show_only_progress = True

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_enabled = not self.drag_enabled

            if self.drag_enabled:
                self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            else:
                self.drag_position = None

            event.accept()

    def mousePressEvent(self, event):
        if self.drag_enabled and event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_enabled and self.drag_position is not None:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
            
    def read_socket(self):
        try:
            data, _ = self.sock.recvfrom(1024)
            self.buffer += data.decode()
        except BlockingIOError:
            return
        except Exception as e:
            log.exception(f"Socket error")
            return

        while "}" in self.buffer:
            idx = self.buffer.find("}") + 1
            chunk = self.buffer[:idx]
            self.buffer = self.buffer[idx:]
            try:
                obj = json.loads(chunk)
                log.debug(obj)
                enabled = obj.get("enabled", None)

                if enabled is not None:
                    self.enabled = enabled

                    if self.enabled:
                        self.show()
                    else:
                        self.hide()

                value = obj.get("value", None)
                value_angle_start = obj.get("value_angle_start", None)
                unit = obj.get("unit", None)
                input = obj.get("input", None)
                if input == "center":
                    self.center_pressed = value

                    # If was any progress - reset value display
                    if self.value_show_only_progress is True:
                        self.value = None
                        self.value_angle_start = None
                        self.unit = None
                else:
                    self.center_pressed = False
                    self.value = value
                    self.value_angle_start = value_angle_start
                    self.unit = unit

                value_show_only_progress = obj.get("value_show_only_progress", None)
                self.value_show_only_progress = value_show_only_progress

                titles = obj.get("titles", [])
                if isinstance(titles, list):
                    self.titles = titles

                icons = obj.get("icons", [])
                if isinstance(icons, list):
                    self.icons = icons

                title = obj.get("title", None)
                self.title = title

                self.update()

            except json.JSONDecodeError:
                pass

    def draw_svg_icon(self, painter, svg_path, rect, color: QColor):
        if not os.path.isfile(svg_path):
            return

        renderer = QSvgRenderer(svg_path)
        if not renderer.isValid():
            return

        size = renderer.defaultSize()
        if size.isEmpty():
            return

        scale = min(
            rect.width() / size.width(),
            rect.height() / size.height()
        )

        img_w = int(size.width() * scale)
        img_h = int(size.height() * scale)

        image = QImage(
            img_w,
            img_h,
            QImage.Format_ARGB32_Premultiplied
        )
        image.fill(Qt.transparent)

        p = QPainter(image)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(image.rect(), color)
        p.end()

        x = rect.x() + (rect.width() - img_w) / 2
        y = rect.y() + (rect.height() - img_h) / 2

        painter.drawImage(QPointF(x, y), image)

    def paintEvent(self, event):

        if not self.enabled:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Clean up
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        pen = QPen(COLOR_OUTER_BG)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        margin = 20
        available_width = self.width() - 2*margin
        available_height = self.height() - 50 - 2*margin
        max_diameter = min(available_width, available_height)

        scale = min(1.0, max_diameter / CIRCLE_DIAMETER)

        outer_d = CIRCLE_DIAMETER * scale
        center_d = CENTER_BUTTON_DIAMETER * scale

        outer_rect = QRectF(
            (self.width() - outer_d) / 2,
            50 + (available_height - outer_d) / 2,
            outer_d,
            outer_d
        )

        angle_step = 360 / 100
        for i in range(100):
            start_angle = i * angle_step
            painter.drawArc(outer_rect, int(start_angle*16), int(angle_step*16))

        pen.setWidth(1)
        painter.setPen(Qt.NoPen)
        if self.center_pressed:
            painter.setBrush(COLOR_CENTER_BG_PRESSED)
        else:
            painter.setBrush(COLOR_CENTER_BG)

        center_rect = QRectF(
            (self.width() - center_d) / 2,
            50 + (available_height - center_d) / 2,
            center_d,
            center_d
        )
        painter.drawEllipse(center_rect)

        if self.value is not None:
            try:
                progress = float(self.value)
            except ValueError:
                progress = 0.0

            progress = max(min(progress, 100.0), -100.0)
            span_angle = -360.0 * (progress / 100.0)

            if self.value_angle_start is not None:
                qt_start_angle = -(self.value_angle_start - 90)
            else:
                qt_start_angle = 0

            path = QPainterPath()
            path.moveTo(outer_rect.center())
            path.arcTo(outer_rect, qt_start_angle, span_angle)
            path.closeSubpath()

            hole = QPainterPath()
            hole.addEllipse(center_rect)

            ring = path.subtracted(hole)

            painter.setPen(Qt.NoPen)
            painter.setBrush(COLOR_PROGRESS)
            painter.drawPath(ring)

        if hasattr(self, 'title') and self.title:
            if self.center_pressed:
                painter.setPen(COLOR_CENTER_PRESSED_FONT)
            else:
                painter.setPen(COLOR_CENTER_FONT)
            font = painter.font()
            font.setPointSizeF(center_d * 0.09)
            painter.setFont(font)
            painter.drawText(center_rect, Qt.AlignCenter, self.title)

        if self.value is not None and self.value_show_only_progress is not True:
            if self.center_pressed:
                painter.setPen(COLOR_CENTER_PRESSED_FONT)
            else:
                painter.setPen(COLOR_CENTER_FONT)
            font = painter.font()
            font.setPointSizeF(center_d * 0.09)
            painter.setFont(font)

            value_rect = QRectF(
                center_rect.x(),
                center_rect.y() + center_d * 0.25,
                center_rect.width(),
                center_rect.height()
            )
            if self.unit is not None:
                painter.drawText(value_rect, Qt.AlignCenter, str(self.value) + str(self.unit))
            else:
                painter.drawText(value_rect, Qt.AlignCenter, str(self.value))

        if hasattr(self, 'titles') and self.titles and type(self.titles) is list and len(self.titles) > 0:

            slice_angle = 360 / len(self.titles)

            font = painter.font()
            font.setPointSizeF(center_d * 0.08)
            painter.setFont(font)

            active_title = getattr(self, 'title', None)
            active_index = -1
            if active_title:
                try:
                    active_index = self.titles.index(active_title)
                except ValueError:
                    active_index = -1

            for idx in range(len(self.titles)):
                title_text = self.titles[idx] if idx < len(self.titles) else None

                start_angle = idx * slice_angle
                span_angle = slice_angle

                # segment
                path = QPainterPath()
                path.moveTo(outer_rect.center())
                path.arcTo(outer_rect, -start_angle, span_angle)
                path.closeSubpath()

                center_rect = QRectF(
                    (self.width() - center_d) / 2,
                    50 + (self.height() - 50 - 2*20 - center_d) / 2,
                    center_d,
                    center_d
                )
                hole = QPainterPath()
                hole.addEllipse(center_rect)
                segment_path = path.subtracted(hole)

                if idx == active_index:
                    painter.setBrush(COLOR_PROGRESS)
                else:
                    painter.setBrush(Qt.NoBrush)

                painter.setPen(QPen(COLOR_OUTER_BG, 1))
                painter.drawPath(segment_path)

                # text
                has_icon = (
                    hasattr(self, 'icons')
                    and idx < len(self.icons)
                    and self.icons[idx]
                )

                if title_text and not has_icon:
                    angle_deg = start_angle + span_angle / 2
                    angle_rad = math.radians(angle_deg - 90)
                    radius = outer_d / 2 + 10
                    x = outer_rect.center().x() + radius * math.cos(angle_rad)
                    y = outer_rect.center().y() + radius * math.sin(angle_rad)

                    text_rect = QRectF(
                        x - center_d * 0.2,
                        y - center_d * 0.1,
                        center_d * 0.4,
                        center_d * 0.2
                    )

                    if idx == active_index:
                        painter.setPen(COLOR_CENTER_PRESSED_FONT)
                    else:
                        painter.setPen(COLOR_CENTER_FONT)

                    painter.drawText(text_rect, Qt.AlignCenter, title_text)

        if hasattr(self, 'icons') and type(self.icons) is list and len(self.icons) > 0:
            slice_angle = 360 / len(self.icons)

            active_title = getattr(self, 'title', None)
            active_index = -1
            if active_title:
                try:
                    active_index = self.titles.index(active_title)
                except ValueError:
                    pass

            for idx in range(len(self.icons)):
                icon = self.icons[idx]
                if not icon:
                    continue

                start_angle = idx * slice_angle
                span_angle = slice_angle

                angle_deg = start_angle + span_angle / 2
                angle_rad = math.radians(angle_deg - slice_angle)
                radius = outer_d / 2 + 10

                x = outer_rect.center().x() + radius * math.cos(angle_rad)
                y = outer_rect.center().y() + radius * math.sin(angle_rad)

                icon_size = center_d * 0.17

                ux = math.cos(angle_rad)
                uy = math.sin(angle_rad)

                radius_inner = radius * 0.74

                x = outer_rect.center().x() + radius_inner * ux
                y = outer_rect.center().y() + radius_inner * uy

                icon_rect = QRectF(
                    x - icon_size / 2,
                    y - icon_size / 2,
                    icon_size,
                    icon_size
                )

                is_active = (idx == active_index)

                icon_color = QColor(COLOR_CENTER_FONT)
                if is_active:
                    icon_color = QColor(COLOR_CENTER_PRESSED_FONT)

                # SVG icon
                if isinstance(icon, str) and icon.endswith(".svg") and os.path.isfile(icon):
                    self.draw_svg_icon(painter, icon, icon_rect, icon_color)

                else:
                    qicon = QIcon.fromTheme(icon)
                    if not qicon.isNull():
                        pixmap = qicon.pixmap(
                            int(icon_size),
                            int(icon_size)
                        )

                        painter.save()
                        painter.drawPixmap(icon_rect.topLeft(), pixmap)
                        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                        painter.fillRect(icon_rect, icon_color)
                        painter.restore()


def signal_handler(sig, frame):
    log.info("Exiting...")
    app.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FloatingWindow()

    try:
        signal.signal(signal.SIGINT, lambda sig, frame: app.quit())

        sys.exit(app.exec())
    except KeyboardInterrupt:
        log.info("Exiting main application.")
