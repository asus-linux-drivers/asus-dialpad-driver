
import sys
import os
os.environ.pop("QT_STYLE_OVERRIDE", None)
import json
import socket
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPen, QColor
import logging
import signal

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
        print(f"Listening on {SOCKET_PATH}")

        self.timer = QTimer()
        self.timer.timeout.connect(self.read_socket)
        self.timer.start(50)

        self.buffer = ""
        self.enabled = False
        self.title = ""
        self.center_pressed = False
        self.current_value = None

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
            print(f"Socket error: {e}")
            return

        while "}" in self.buffer:
            idx = self.buffer.find("}") + 1
            chunk = self.buffer[:idx]
            self.buffer = self.buffer[idx:]
            try:
                obj = json.loads(chunk)
                enabled = obj.get("enabled", None)

                if enabled is not None:
                    self.enabled = enabled

                    if self.enabled:
                        self.show()
                    else:
                        self.hide()

                input_type = obj.get("input", None)

                if input_type == "clockwise" or input_type == "counterclockwise":
                    value = obj.get("value", None)
                    self.current_value = value
                elif input_type == "center":
                    value = obj.get("value", False)
                    self.current_value = value

                title = obj.get("title", None)
                if title is not None:
                    self.title = title
                self.update()

            except json.JSONDecodeError:
                pass

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

        if self.current_value is not None:
            try:
                progress = float(self.current_value)
            except ValueError:
                progress = 0.0

            progress = max(0.0, min(100.0, progress))
            span_angle = -360.0 * (progress / 100.0)

            from PySide6.QtGui import QPainterPath

            path = QPainterPath()
            path.moveTo(outer_rect.center())
            path.arcTo(outer_rect, 0, span_angle)
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

        if self.current_value is not None:
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
            painter.drawText(value_rect, Qt.AlignCenter, str(self.current_value))


def signal_handler(sig, frame):
    print("Exiting...")
    app.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FloatingWindow()

    try:
        signal.signal(signal.SIGINT, lambda sig, frame: app.quit())

        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("Exiting main application.")
