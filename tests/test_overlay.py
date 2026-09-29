"""Consumer-visible rendering boundaries; no driver, sockets, or hardware."""
from copy import deepcopy
import math
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontMetricsF, QIcon, QImage
    from PySide6.QtWidgets import QApplication
except ImportError as exc:
    raise unittest.SkipTest("The optional overlay requires PySide6") from exc

from dialpad_layout_editor import RingPreview
from dialpad_overlay import COLOR_CENTER_BG, COLOR_CENTER_FONT, COLOR_PROGRESS, OverlayCanvas, label_lines


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["overlay-tests"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.canvas = OverlayCanvas()
        self.addCleanup(self.canvas.deleteLater)

    def image(self, payload):
        self.canvas.set_feedback(payload)
        image = QImage(self.canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.canvas.render(image)
        return image

    def test_missing_unreadable_and_unknown_theme_icons_preserve_slice_labels(self):
        payload = {"titles": ["Volume Preview", "Brightness", "Scroll", None]}
        expected = self.image(payload)
        broken = self.root / "broken.svg"
        broken.write_text("This is not an SVG document.", encoding="utf-8")
        for icon in (str(self.root / "missing.svg"), str(broken), "dialpad-test-icon-that-does-not-exist"):
            with self.subTest(icon=icon):
                self.assertEqual(self.image({**payload, "icons": [icon]}), expected)

    def test_local_svg_and_theme_icons_share_the_slice_angle_and_do_not_erase_the_center(self):
        theme = self.root / "dialpad-test-theme"
        icons = theme / "16x16" / "actions"
        icons.mkdir(parents=True)
        (theme / "index.theme").write_text(
            "[Icon Theme]\nName=DialPad test\nDirectories=16x16/actions\n"
            "[16x16/actions]\nSize=16\nType=Fixed\nContext=Actions\n", encoding="utf-8")
        svg = icons / "dialpad-test-solid.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
                       '<rect width="16" height="16" fill="black"/></svg>', encoding="utf-8")
        self.addCleanup(QIcon.setThemeName, QIcon.themeName())
        self.addCleanup(QIcon.setThemeSearchPaths, QIcon.themeSearchPaths())
        QIcon.setThemeSearchPaths([str(self.root)])
        QIcon.setThemeName("dialpad-test-theme")
        # Five slices previously placed the icon at a different angle from its label.
        payload = {"titles": ["First", "Second", "Third", "Fourth", "Fifth"]}
        local = self.image({**payload, "icons": [str(svg)]})
        themed = self.image({**payload, "icons": ["dialpad-test-solid"]})
        self.assertEqual(themed, local)
        angle = math.radians(36 - 90)
        x, y = round(137.5 + 75 * math.cos(angle)), round(137.5 + 75 * math.sin(angle))
        self.assertEqual(local.pixelColor(x, y), COLOR_CENTER_FONT)
        self.assertEqual(local.pixelColor(137, 137), COLOR_CENTER_BG)

    def test_selected_sectors_follow_clockwise_from_top_for_non_quadrant_counts(self):
        for count in (3, 5, 8):
            with self.subTest(count=count):
                titles = [str(index) for index in range(count)]
                image = self.image({"titles": titles, "title": titles[0]})
                for fraction in (.1, .9):
                    angle = math.radians(360 / count * fraction - 90)
                    x = round(137.5 + 75 * math.cos(angle))
                    y = round(137.5 + 75 * math.sin(angle))
                    self.assertEqual(image.pixelColor(x, y), COLOR_PROGRESS)
                angle = math.radians(360 / count * 1.5 - 90)
                x = round(137.5 + 75 * math.cos(angle))
                y = round(137.5 + 75 * math.sin(angle))
                self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_wrapping_preserves_readable_labels_and_elides_only_overflow(self):
        font = QFont()
        font.setPixelSize(11)
        metrics = QFontMetricsF(font)
        for text in ("Volume Preview", "Brightness", "\U0001d11e Musical notation"):
            width = max(metrics.horizontalAdvance(word) for word in text.split()) + 1
            lines = label_lines(text, font, width, maximum=3)
            self.assertEqual(" ".join(lines), text)
            self.assertTrue(all(metrics.horizontalAdvance(line) <= width for line in lines))
        lines = label_lines("A very long function name with many more words than fit", font, 55)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("\u2026"))
        self.assertTrue(all(metrics.horizontalAdvance(line) <= 55 for line in lines))

    def test_center_feedback_preserves_numeric_value_but_clears_gesture_progress(self):
        self.canvas.set_feedback({"value": "42", "unit": "%", "value_show_only_progress": False})
        self.canvas.set_feedback({"input": "center", "value": True})
        self.assertEqual((self.canvas.value, self.canvas.unit), ("42", "%"))
        self.assertTrue(self.canvas.center_pressed)
        self.canvas.set_feedback({"value": 75, "value_angle_start": 90, "value_show_only_progress": True})
        self.canvas.set_feedback({"input": "center", "value": True})
        self.assertIsNone(self.canvas.value)
        self.assertIsNone(self.canvas.value_angle_start)
        self.assertIsNone(self.canvas.unit)

    def test_preview_never_executes_queries_or_commands_and_preserves_empty_titles(self):
        marker = self.root / "must-not-be-created"
        command = "touch " + shlex.quote(str(marker))
        profile = {
            "center": [{"command": command}],
            "Volume": {"title": "", "command": command, "value": command,
                       "icons": {"true": str(self.root / "conditional.svg")}},
            "Scroll": {"title": "Scroll preview", "icon": str(self.root / "missing.svg")},
        }
        original = deepcopy(profile)
        with mock.patch("socket.socket", side_effect=AssertionError("Preview attempted socket access")):
            preview = RingPreview()
            self.addCleanup(preview.deleteLater)
            preview.set_profile(profile, 4)
            preview.canvas.grab()
        self.assertFalse(marker.exists())
        self.assertEqual(profile, original)
        self.assertEqual(preview.canvas.titles, ["", "Scroll preview", None, None])
        self.assertEqual(preview.canvas.icons, [None, str(self.root / "missing.svg"), None, None])
        self.assertIsNone(preview.canvas.value)


if __name__ == "__main__":
    unittest.main()
