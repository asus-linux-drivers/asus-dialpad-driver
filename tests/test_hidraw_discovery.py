"""Test sysfs discovery without importing the hardware-opening daemon."""
import ast
import glob
import os
from pathlib import Path
import tempfile
import unittest


source = Path(__file__).resolve().parents[1] / "dialpad.py"
function = next(node for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "find_touchpad_hidraw")
namespace = {"glob": glob, "os": os}
exec(compile(ast.Module([function], type_ignores=[]), str(source), "exec"), namespace)
find_touchpad_hidraw = namespace["find_touchpad_hidraw"]


class HidrawDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs = self.root / "sys"
        self.dev = self.root / "dev"
        self.hid = self.sysfs / "devices/platform/i2c-0/touchpad/hid-device"
        self.input = self.hid / "input/input30"
        self.input.mkdir(parents=True)
        self.event = self.sysfs / "class/input/event43"
        self.event.mkdir(parents=True)
        (self.event / "device").symlink_to(self.input)

    def hidraw(self, name, device):
        node = self.sysfs / "class/hidraw" / name
        node.mkdir(parents=True)
        (node / "device").symlink_to(device)

    def find(self):
        return find_touchpad_hidraw("event43", str(self.sysfs), str(self.dev))

    def test_matches_the_hid_ancestor_of_the_selected_event_node(self):
        self.hidraw("hidraw17", self.hid)
        self.assertEqual(self.find(), str(self.dev / "hidraw17"))

    def test_does_not_select_another_hid_device_on_the_same_bus(self):
        other = self.sysfs / "devices/platform/i2c-0/other/hid-device"
        other.mkdir(parents=True)
        self.hidraw("hidraw0", other)
        self.hidraw("hidraw17", self.hid)
        self.assertEqual(self.find(), str(self.dev / "hidraw17"))

    def test_unrelated_interfaces_do_not_disable_the_existing_i2c_fallback(self):
        other = self.sysfs / "devices/unrelated"
        other.mkdir(parents=True)
        self.hidraw("hidraw0", other)
        self.assertIsNone(self.find())

    def test_missing_input_node_returns_none(self):
        (self.event / "device").unlink()
        self.assertIsNone(self.find())

    def test_broken_hidraw_link_is_ignored(self):
        self.hidraw("hidraw0", self.sysfs / "missing")
        self.assertIsNone(self.find())

    def test_missing_hidraw_class_returns_none(self):
        self.assertIsNone(self.find())


if __name__ == "__main__":
    unittest.main()
