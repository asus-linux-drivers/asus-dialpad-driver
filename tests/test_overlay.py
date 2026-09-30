"""Rendering and isolated driver feedback; no sockets or hardware."""
import ast
from copy import deepcopy
import math
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
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
from dialpad_runtime import Geometry, GestureState, matching_action


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["overlay-tests"])
        # Compile only feedback functions: importing the driver opens hardware.
        driver_path = Path(__file__).resolve().parents[1] / "dialpad.py"
        functions = {
            "publish_runtime_snapshot", "request_ring_metadata", "request_action_metadata",
            "gesture_modifiers", "gesture_feedback", "center_action", "rotation_action",
            "finish_gesture", "process_touch_frame",
        }
        tree = ast.parse(driver_path.read_text(encoding="utf-8"), filename=str(driver_path))
        cls.driver_code = compile(ast.Module(
            body=[node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name in functions],
            type_ignores=[],
        ), str(driver_path), "exec")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.canvas = OverlayCanvas()
        self.addCleanup(self.canvas.deleteLater)

    def image(self, payload=None):
        if payload is not None:
            self.canvas.set_feedback(payload)
        image = QImage(self.canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.canvas.render(image)
        return image

    def assert_selection(self, selected_index):
        image = self.image()
        for index in range(4):
            angle = math.radians((index + .5) * 90 - 90)
            x = round(137.5 + 75 * math.cos(angle))
            y = round(137.5 + 75 * math.sin(angle))
            color = image.pixelColor(x, y)
            if index == selected_index:
                self.assertEqual(color, COLOR_PROGRESS)
            else:
                self.assertEqual(color.alpha(), 0)

    def driver_feedback(self, second_title):
        snapshot = SimpleNamespace(
            prepared=SimpleNamespace(settings={}, modifiers=frozenset()),
            profile={
                "center": [{"trigger": "immediate"}],
                "First": {"title": "Shared"},
                "Second": {"title": second_title, "clockwise": [
                    {"trigger": "immediate", "title": "Rotation feedback"}]},
            },
            function_names=("First", "Second", None, None),
            function_titles=("Shared", second_title, None, None),
            function_icons=(None,) * 4,
            geometry=Geometry(0, 0, 100, 30, (200, 210, 200, 210)),
        )
        pending = []

        def queue_metadata(work):
            pending[:] = [work]

        def flush_metadata():
            self.canvas.set_feedback(pending.pop()())

        runtime = SimpleNamespace(
            current=snapshot, gesture=GestureState(),
            contacts=SimpleNamespace(synchronized=True, frame_complete=True, touching=True,
                                     position=mock.Mock(return_value=(60, 60))),
            request_metadata=queue_metadata, invalidate_metadata=pending.clear,
        )
        namespace = {
            "runtime": runtime, "socket_enabled": True, "dialpad": True,
            "pressed_keys": frozenset(), "multi_app_mode_icons": [None] * 4,
            "default_treshold": 60, "socket_send_progress_above_treshold": 30,
            "send_to_socket": self.canvas.set_feedback,
            "emulate_shortcuts": matching_action, "matching_action": matching_action,
            "get_current_value": mock.Mock(return_value=None),
            "set_touchpad_prop_send_events": mock.Mock(), "load_all_config_values": mock.Mock(),
        }
        exec(self.driver_code, namespace)
        return SimpleNamespace(**namespace, flush_metadata=flush_metadata)

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
                image = self.image({"titles": titles, "title": titles[0], "selected_index": 0})
                for fraction in (.1, .9):
                    angle = math.radians(360 / count * fraction - 90)
                    x = round(137.5 + 75 * math.cos(angle))
                    y = round(137.5 + 75 * math.sin(angle))
                    self.assertEqual(image.pixelColor(x, y), COLOR_PROGRESS)
                angle = math.radians(360 / count * 1.5 - 90)
                x = round(137.5 + 75 * math.cos(angle))
                y = round(137.5 + 75 * math.sin(angle))
                self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_duplicate_and_empty_titles_do_not_determine_the_selected_sector(self):
        for title in ("Shared", ""):
            with self.subTest(title=title):
                self.canvas.set_feedback({
                    "titles": ["Shared", title, None, None],
                    "title": title, "selected_index": 1,
                })
                self.assert_selection(1)
                self.assertEqual(self.canvas.title, title)
                # Partial packets keep the ring and identity, not a label match.
                self.canvas.set_feedback({"input": "center", "value": 1, "title": "Changed"})
                self.assert_selection(1)
                self.canvas.set_feedback({"icons": [None] * 4})
                self.assert_selection(1)
                self.canvas.set_feedback({"selected_index": None, "title": None})
                self.assert_selection(None)
                self.canvas.set_feedback({"title": "Shared"})
                self.assert_selection(None)

    def test_driver_selection_survives_actions_metadata_and_confirmed_release(self):
        for title in ("Shared", ""):
            with self.subTest(title=title):
                driver = self.driver_feedback(title)
                driver.publish_runtime_snapshot(driver.runtime.current)
                driver.flush_metadata()
                driver.process_touch_frame(1)
                self.assert_selection(1)
                self.assertEqual(self.canvas.title, title)

                driver.runtime.contacts.position.return_value = (0, 0)
                driver.process_touch_frame(2)
                self.assert_selection(1)
                driver.flush_metadata()
                self.assert_selection(1)
                self.assertEqual(self.canvas.title, title)

                driver.runtime.contacts.position.return_value = (60, 60)
                driver.process_touch_frame(3)
                self.assert_selection(1)
                driver.runtime.contacts.position.return_value = (-60, 60)
                driver.process_touch_frame(4)
                self.assert_selection(1)
                driver.flush_metadata()
                self.assert_selection(1)
                self.assertEqual(self.canvas.title, "Rotation feedback")

                driver.runtime.contacts.touching = False
                driver.process_touch_frame(5)
                self.assert_selection(1)
                # A committed profile resets the gesture before publication.
                driver.runtime.gesture.reset()
                driver.publish_runtime_snapshot(driver.runtime.current)
                self.assert_selection(None)
                driver.flush_metadata()
                self.assert_selection(None)

    def test_driver_unconfirmed_release_and_cancellation_clear_selection(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                driver = self.driver_feedback("Shared")
                driver.publish_runtime_snapshot(driver.runtime.current)
                driver.process_touch_frame(1)
                self.assert_selection(1)
                if cancelled:
                    # Even a confirmed function and pending metadata are reset.
                    driver.runtime.contacts.position.return_value = (0, 0)
                    driver.process_touch_frame(2)
                    self.assert_selection(1)
                driver.finish_gesture(3, cancelled=cancelled)
                self.assert_selection(None)
                driver.flush_metadata()
                self.assert_selection(None)

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
            preview.canvas.set_feedback({"titles": ["Old", "Old"], "selected_index": 1})
            preview.set_profile(profile, 4)
            image = preview.canvas.grab().toImage()
        self.assertFalse(marker.exists())
        self.assertEqual(profile, original)
        self.assertEqual(preview.canvas.titles, ["", "Scroll preview", None, None])
        self.assertEqual(preview.canvas.icons, [None, str(self.root / "missing.svg"), None, None])
        self.assertIsNone(preview.canvas.value)
        self.assertEqual(image.pixelColor(191, 191), preview.canvas.background)


if __name__ == "__main__":
    unittest.main()
