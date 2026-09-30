
import sys
import os
os.environ.pop("QT_STYLE_OVERRIDE", None)
import json
import socket
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

from dialpad_overlay import OverlayCanvas
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


class FloatingWindow(OverlayCanvas):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)

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
                self.set_feedback(obj)
                if obj.get("enabled") is not None:
                    self.setVisible(bool(self.enabled))

            except json.JSONDecodeError:
                pass



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
