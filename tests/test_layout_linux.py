"""Behavioral checks for Linux persistence and the read-only control boundary."""

import configparser
import concurrent.futures
from dataclasses import replace
import json
import os
from pathlib import Path
import py_compile
import socket
import tempfile
import threading
import struct
import types
import unittest
from unittest import mock

from dialpad_layout import (
    LayoutSource, ValidationError, normalize_document,
    revision_bytes, save_layout,
)
from dialpad_layout_linux import (
    LoadedLayout, StatusServer, activate_layout, compile_layout, get_status,
    initialize_config, load_layout, load_recovery, load_trusted_python,
    read_config, remove_user_layout, requested_layout, save_recovery, update_config,
    validate_device_geometry, select_keyboard_device, uses_gnome_input_sources,
    read_gnome_input_source,
)

ROOT = Path(__file__).resolve().parents[1]


def simple_document():
    return {
        "schema_version": 1,
        "geometry": {
            "top_right_icon_width": 10, "top_right_icon_height": 10,
            "circle_diameter": 80, "center_button_diameter": 20,
            "circle_center_x": 50, "circle_center_y": 50,
        },
        "app_shortcuts": {"none": {"center": [{"trigger": "release"}]}},
    }


class InputSessionTests(unittest.TestCase):
    def keyboard_record(self, name, event, *, virtual=False, keys=None, word_bits=None):
        if keys is None:
            keys = (30, 44, 57, 29, 42, 56)
        bits = sum(1 << code for code in keys)
        width = word_bits or struct.calcsize("L") * 8
        words = []
        while bits:
            words.insert(0, format(bits & ((1 << width) - 1), "x"))
            bits >>= width
        root = "/devices/virtual/input" if virtual else "/devices/pci0000:00/input"
        return (f'N: Name="{name}"\nS: Sysfs={root}/input{event}\n'
                f'H: Handlers=kbd event{event}\nB: KEY={" ".join(words) or "0"}\n\n')

    def test_unknown_physical_keyboard_beats_injected_and_hotkey_devices(self):
        records = (
            self.keyboard_record("Asus Virtual Keyboard", 1, virtual=True) +
            self.keyboard_record("Asus WMI hotkeys", 2, keys=(116, 224, 225)) +
            self.keyboard_record("ITE Tech. Inc. ITE Device(8910)", 3)
        )
        self.assertEqual(select_keyboard_device(records), "3")

    def test_known_physical_keyboard_retains_priority(self):
        records = (
            self.keyboard_record("Generic USB Keyboard", 3) +
            self.keyboard_record("AT Translated Set 2 keyboard", 7)
        )
        self.assertEqual(select_keyboard_device(records), "7")

    def test_proc_bitmap_native_word_boundaries(self):
        for width in (32, 64):
            with self.subTest(width=width), mock.patch(
                    "dialpad_layout_linux.struct.calcsize", return_value=width // 8):
                record = self.keyboard_record("Unknown keyboard", 9, word_bits=width)
                self.assertEqual(select_keyboard_device(record), "9")

    def test_bluetooth_uhid_is_not_a_uinput_keyboard(self):
        record = self.keyboard_record("Bluetooth Keyboard", 8).replace(
            "/devices/pci0000:00/input", "/devices/virtual/misc/uhid/0005:1234/input",
        )
        self.assertEqual(select_keyboard_device(record), "8")

    def test_only_partial_or_injected_keyboards_leave_listener_unavailable(self):
        records = (
            self.keyboard_record("Remote Keyboard", 1, virtual=True) +
            self.keyboard_record("Power Button", 2, keys=(116,)) +
            self.keyboard_record("AT Translated Set 2 keyboard", 3, keys=(30, 44, 57))
        )
        self.assertIsNone(select_keyboard_device(records))

    def test_current_desktop_controls_gnome_polling_not_login_defaults(self):
        self.assertFalse(uses_gnome_input_sources({
            "XDG_CURRENT_DESKTOP": "Hyprland", "DESKTOP_SESSION": "ubuntu",
        }))
        self.assertFalse(uses_gnome_input_sources({"XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertTrue(uses_gnome_input_sources({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}))
        self.assertTrue(uses_gnome_input_sources({"DESKTOP_SESSION": "gnome-xorg"}))
        self.assertFalse(uses_gnome_input_sources({}))

    def test_empty_gvariant_sources_do_not_require_an_active_source(self):
        for value in (b"@a(ss) []", b"[]"):
            with self.subTest(value=value):
                settings = {"sources": value}
                self.assertIsNone(read_gnome_input_source(lambda _, name: settings[name]))

    def test_gnome_recent_source_takes_precedence_over_stale_current_index(self):
        settings = {
            "sources": b"[('xkb', 'us'), ('xkb', 'de+nodeadkeys')]",
            "mru-sources": b"@a(ss) [('xkb', 'de+nodeadkeys'), ('xkb', 'us')]",
            "current": b"uint32 99",
        }
        self.assertEqual(read_gnome_input_source(lambda _, name: settings[name]), (1, "de"))

    def test_empty_or_removed_recent_source_falls_back_to_current(self):
        for recent in (b"@a(ss) []", b"[('xkb', 'removed')]"):
            with self.subTest(recent=recent):
                settings = {
                    "sources": b"[('xkb', 'de'), ('xkb', 'us')]",
                    "mru-sources": recent, "current": b"uint32 1",
                }
                self.assertEqual(read_gnome_input_source(lambda _, name: settings[name]), (1, "us"))

    def test_invalid_index_or_input_engine_is_not_an_xkb_layout(self):
        settings = {
            "sources": b"[('xkb', 'us'), ('ibus', 'mozc-jp')]",
            "mru-sources": b"[]", "current": b"uint32 2",
        }
        self.assertIsNone(read_gnome_input_source(lambda _, name: settings[name]))
        settings["current"] = b"uint32 1"
        self.assertIsNone(read_gnome_input_source(lambda _, name: settings[name]))

    def test_malformed_source_data_is_reported_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            read_gnome_input_source(lambda *_: b"[('xkb', 4)]")


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "config with spaces"
        self.directory.mkdir()

    def test_concurrent_patches_preserve_every_writer_and_unrelated_section(self):
        path = self.directory / "dialpad_dev"
        path.write_text("[main]\nunchanged = 30%\n[other]\ntoken = retained\n")
        barrier = threading.Barrier(12)

        def writer(index):
            barrier.wait()
            patch = {f"writer_{index}": index}
            if index == 0:
                patch["layout"] = "new-layout"
            if index == 1:
                patch["enabled"] = True
            update_config(self.directory, patch)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(writer, range(12)))
        result = read_config(self.directory)
        self.assertEqual(result.get("main", "layout"), "new-layout")
        self.assertEqual(result.get("main", "enabled"), "1")
        self.assertEqual(result.get("main", "unchanged"), "30%")
        self.assertEqual(result.get("other", "token"), "retained")
        for index in range(12):
            self.assertEqual(result.get("main", f"writer_{index}"), str(index))

    def test_defaults_and_argv_do_not_mask_explicit_layout(self):
        self.assertEqual(requested_layout(self.directory, "argv-default"), "argv-default")
        self.assertFalse((self.directory / "dialpad_dev").exists())
        update_config(self.directory, {"layout": "manager-copy"})
        update_config(self.directory, {}, {"layout": "old-default", "enabled": False})
        self.assertEqual(requested_layout(self.directory, "new-nix-default"), "manager-copy")
        self.assertEqual(read_config(self.directory).get("main", "enabled"), "0")
        update_config(self.directory, {"layout": ""})
        self.assertEqual(requested_layout(self.directory, "new-nix-default"), "new-nix-default")
        with self.assertRaises(ValueError):
            requested_layout(self.directory)

    def test_malformed_config_is_not_replaced(self):
        path = self.directory / "dialpad_dev"
        original = b"[main]\nlayout = one\nlayout = two\n"
        path.write_bytes(original)
        with self.assertRaises(configparser.Error):
            update_config(self.directory, {"enabled": True})
        self.assertEqual(path.read_bytes(), original)

    def test_patch_preserves_permissions_and_pre_replace_failure(self):
        path = self.directory / "dialpad_dev"
        update_config(self.directory, {"layout": "old"})
        path.chmod(0o640)
        update_config(self.directory, {"enabled": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        before = path.read_bytes()
        with mock.patch("dialpad_layout_linux.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                update_config(self.directory, {"layout": "new"})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_nix_defaults_merge_sections_without_replacing_user_settings(self):
        update_config(self.directory, {"layout": "user", "enabled": True})
        defaults = configparser.ConfigParser(interpolation=None)
        defaults.read_string("[main]\nlayout = default\nenabled = 0\nslices_minimum_count = 5\n[extra]\nquery = 100%\n")
        result = initialize_config(self.directory, defaults)
        self.assertEqual(result.get("main", "layout"), "user")
        self.assertEqual(result.get("main", "enabled"), "1")
        self.assertEqual(result.get("main", "slices_minimum_count"), "5")
        self.assertEqual(result.get("extra", "query"), "100%")

    def test_activation_never_selects_an_unsaved_or_invalid_layout(self):
        update_config(self.directory, {"layout": "old"})
        folder = self.directory / "layouts"
        folder.mkdir()
        (folder / "broken.json").write_text('{"schema_version":99}')
        with self.assertRaises(ValidationError):
            activate_layout(self.directory, "broken", ROOT)
        self.assertEqual(requested_layout(self.directory), "old")
        layout = normalize_document(simple_document())
        revision = save_layout(folder / "copy.json", layout)
        loaded = activate_layout(self.directory, "copy", ROOT)
        self.assertEqual(loaded.revision, revision)
        self.assertEqual(requested_layout(self.directory), "copy")

    def test_activation_rejects_content_changed_after_review(self):
        folder = self.directory / "layouts"
        folder.mkdir()
        document = simple_document()
        reviewed = save_layout(folder / "copy.json", normalize_document(document))
        document["name"] = "Changed externally"
        save_layout(folder / "copy.json", normalize_document(document), overwrite=True)
        update_config(self.directory, {"layout": "old"})
        with self.assertRaisesRegex(ValueError, "changed after review"):
            activate_layout(self.directory, "copy", ROOT, expected_revision=reviewed)
        self.assertEqual(requested_layout(self.directory), "old")


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.layouts = self.directory / "layouts"
        self.layouts.mkdir()

    def dynamic_source(self, diameter="80 + 0"):
        return (
            f"circle_diameter = {diameter}\n"
            "center_button_diameter = 20\n"
            "circle_center_x = 50\ncircle_center_y = 50\n"
            "top_right_icon_width = 10\ntop_right_icon_height = 10\n"
            "app_shortcuts = {'none': {'center': [{'trigger': 'release'}]}}\n"
        ).encode()

    def fake_libevdev(self):
        return types.SimpleNamespace(EV_KEY=object(), EV_REL=object())

    def test_equal_size_same_second_trusted_reload_ignores_stale_bytecode(self):
        path = self.layouts / "dynamic.py"
        old = self.dynamic_source("80 + 0")
        new = self.dynamic_source("80 + 1")
        path.write_bytes(old)
        timestamp = int(path.stat().st_mtime)
        os.utime(path, (timestamp, timestamp))
        py_compile.compile(str(path), doraise=True)
        with mock.patch.dict("sys.modules", {"libevdev": self.fake_libevdev()}):
            first = load_layout("dynamic", self.directory, ROOT, trusted_python=True)
            path.write_bytes(new)
            os.utime(path, (timestamp, timestamp))
            second = load_layout("dynamic", self.directory, ROOT, trusted_python=True)
        self.assertEqual(len(old), len(new))
        self.assertEqual(first.layout.geometry["circle_diameter"], 80)
        self.assertEqual(second.layout.geometry["circle_diameter"], 81)
        self.assertEqual(second.revision, revision_bytes(new))
        self.assertNotEqual(first.revision, second.revision)

    def test_trusted_execution_supports_package_metadata_and_relative_imports(self):
        helper = self.layouts / "helper_for_trusted_test.py"
        helper.write_text("DIAMETER = 80\n")
        path = self.layouts / "dynamic.py"
        source = b"from .helper_for_trusted_test import DIAMETER\nassert __package__ == 'layouts'\nassert __spec__.name == __name__\n" + self.dynamic_source("DIAMETER")
        with mock.patch.dict("sys.modules", {"libevdev": self.fake_libevdev()}):
            layout = load_trusted_python(source, path)
        self.assertEqual(layout.geometry["circle_diameter"], 80)

    def test_static_validation_never_executes_python_or_shell(self):
        marker = self.directory / "executed"
        path = self.layouts / "unsafe.py"
        path.write_bytes(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n".encode() + self.dynamic_source())
        with self.assertRaises(ValidationError):
            load_layout("unsafe", self.directory, ROOT)
        self.assertFalse(marker.exists())

    def test_higher_priority_invalid_file_does_not_select_bundled_python(self):
        (self.layouts / "proartp16.json").write_text("invalid json")
        with self.assertRaises(ValidationError) as failure:
            load_layout("proartp16", self.directory, ROOT)
        self.assertEqual(failure.exception.requested["revision"], revision_bytes(b"invalid json"))
        self.assertEqual(failure.exception.requested["identifier"], "proartp16")

    def test_compile_separates_event_values_queries_and_modifier_precedence(self):
        document = simple_document()
        document["app_shortcuts"]["none"]["clockwise"] = [
            {"key": ["REL_WHEEL", "REL_WHEEL_HI_RES"], "value": [1, 120], "title": "Scroll"},
            {"key": "KEY_VOLUMEUP", "modifier": "KEY_LEFTSHIFT", "value": "echo 50", "title": "Volume"},
        ]
        key = types.SimpleNamespace(KEY_VOLUMEUP=object(), KEY_LEFTSHIFT=object())
        relative = types.SimpleNamespace(REL_WHEEL=object(), REL_WHEEL_HI_RES=object())
        compiled = compile_layout(normalize_document(document), libevdev_module=types.SimpleNamespace(EV_KEY=key, EV_REL=relative))
        actions = compiled["none"]["clockwise"]
        self.assertEqual(actions[0]["title"], "Volume")
        self.assertEqual(actions[0]["value_query"], "echo 50")
        self.assertNotIn("event_values", actions[0])
        self.assertEqual(actions[1]["event_values"], [1, 120])
        self.assertNotIn("value_query", actions[1])
        self.assertTrue(all("value" not in action for action in actions))
        del relative.REL_WHEEL_HI_RES
        with self.assertRaisesRegex(ValidationError, "REL_WHEEL_HI_RES"):
            compile_layout(normalize_document(document), libevdev_module=types.SimpleNamespace(EV_KEY=key, EV_REL=relative))

    def test_geometry_only_uses_actual_bounds_when_available(self):
        layout = normalize_document(simple_document())
        validate_device_geometry(layout, None)
        validate_device_geometry(layout, dict(min_x=0, max_x=100, min_y=0, max_y=100))
        with self.assertRaisesRegex(ValidationError, "circle_center_x"):
            validate_device_geometry(layout, dict(min_x=20, max_x=100, min_y=0, max_y=100))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        source = LayoutSource("copy", self.directory / "layouts" / "copy.py", "python", "user", False)
        self.loaded = LoadedLayout(normalize_document(simple_document()), source, "a" * 64)

    def test_recovery_retains_applied_revision_without_loading_source(self):
        update_config(self.directory, {"layout": "invalid-request"})
        save_recovery(self.directory, self.loaded)
        recovered = load_recovery(self.directory)
        self.assertEqual(recovered.revision, "a" * 64)
        self.assertEqual(recovered.layout.document, self.loaded.layout.document)
        self.assertEqual(recovered.source.path, self.loaded.source.path)
        self.assertEqual(requested_layout(self.directory), "invalid-request")
        self.assertFalse(self.loaded.source.path.exists())

    def test_recovery_rejects_invalid_snapshot_and_duplicate_keys(self):
        save_recovery(self.directory, self.loaded)
        path = self.directory / ".layout-state" / "last-successful.json"
        envelope = json.loads(path.read_bytes())
        envelope["layout"]["geometry"]["circle_diameter"] = -1
        path.write_text(json.dumps(envelope))
        with self.assertRaises(ValidationError):
            load_recovery(self.directory)
        path.write_text('{"recovery_version":1,"recovery_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_recovery(self.directory)

    def test_pre_replace_failure_retains_last_successful_snapshot(self):
        save_recovery(self.directory, self.loaded)
        with mock.patch("dialpad_layout_linux.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save_recovery(self.directory, replace(self.loaded, revision="b" * 64))
        self.assertEqual(load_recovery(self.directory).revision, "a" * 64)

    def test_sync_failure_is_not_reported_as_durable_success(self):
        with mock.patch("dialpad_layout_linux.os.fsync", side_effect=OSError("sync failed")):
            with self.assertRaisesRegex(OSError, "sync failed"):
                save_recovery(self.directory, self.loaded)
        self.assertFalse((self.directory / ".layout-state" / "last-successful.json").exists())

    def test_directory_sync_failure_reports_uncertain_durability_after_replace(self):
        save_recovery(self.directory, self.loaded)
        with mock.patch("dialpad_layout_linux.os.fsync", side_effect=[None, OSError("directory sync failed")]):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                save_recovery(self.directory, replace(self.loaded, revision="b" * 64))
        # The current filesystem view changed, but the write did not promise
        # crash durability: runtime must report recovery_current=False.
        self.assertEqual(load_recovery(self.directory).revision, "b" * 64)



class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        runtime = self.directory / "runtime"
        runtime.mkdir(mode=0o700)
        environment = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)})
        environment.start()
        self.addCleanup(environment.stop)

    def server(self, name):
        directory = self.directory / name
        status = {"instance_id": name, "state": "applied", "generation": 1,
                  "applied": {"identifier": "copy", "revision": "a" * 64}}
        server = StatusServer(directory, lambda: status).start()
        self.addCleanup(server.close)
        return directory, server

    def test_multiple_clients_and_directories_have_separate_instances(self):
        first, _ = self.server("first")
        second, _ = self.server("second")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(get_status, [first, second, first, second]))
        self.assertEqual([result["instance_id"] for result in results], ["first", "second", "first", "second"])

    def test_second_driver_cannot_take_over_live_endpoint(self):
        directory, original = self.server("first")
        inode = original.path.stat().st_ino
        contender = StatusServer(directory, lambda: {"instance_id": "imposter"})
        with self.assertRaisesRegex(RuntimeError, "Another driver"):
            contender.start()
        contender.close()
        self.assertEqual(original.path.stat().st_ino, inode)
        self.assertEqual(get_status(directory)["instance_id"], "first")

    def test_status_is_read_only_and_offline_is_not_applied(self):
        directory, server = self.server("first")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(server.path))
            client.sendall(b'{"op":"activate","layout":"unwanted"}\n')
            response = json.loads(client.recv(4096))
        self.assertIn("read-only", response["error"])
        self.assertEqual(get_status(directory)["applied"]["identifier"], "copy")
        server.close()
        with self.assertRaises(OSError):
            get_status(directory)

    def test_group_readable_socket_is_not_trusted(self):
        directory, server = self.server("first")
        server.path.chmod(0o660)
        with self.assertRaises(PermissionError):
            get_status(directory)

    def removal_fixture(self):
        config = self.directory / "config"
        (config / "layouts").mkdir(parents=True)
        layout = normalize_document(simple_document())
        for identifier in ("original", "replacement"):
            save_layout(config / "layouts" / f"{identifier}.json", layout)
        original = load_layout("original", config, config)
        replacement = load_layout("replacement", config, config)
        update_config(config, {"layout": "replacement"})
        identity = {"identifier": "replacement", "revision": replacement.revision}
        status = {"instance_id": "live", "generation": 3, "state": "applied",
                  "requested": identity, "applied": identity}
        server = StatusServer(config, lambda: status).start()
        self.addCleanup(server.close)
        return config, original, replacement, status

    def test_external_edit_before_guarded_removal_is_retained(self):
        config, original, replacement, _status = self.removal_fixture()
        changed = simple_document()
        changed["name"] = "External changes must survive"
        save_layout(original.source.path, normalize_document(changed), overwrite=True)
        with self.assertRaisesRegex(ValueError, "changed externally"):
            remove_user_layout(config, original.source, original.revision, config,
                               instance_id="live", replacement=("replacement", replacement.revision))
        self.assertEqual(load_layout("original", config, config).layout.name, changed["name"])

    def test_reviewed_activation_cannot_select_file_removed_by_another_manager(self):
        config, original, replacement, _status = self.removal_fixture()
        validated, proceed = threading.Event(), threading.Event()
        outcomes = []
        original_loader = load_layout

        def paused_loader(*args, **kwargs):
            loaded = original_loader(*args, **kwargs)
            validated.set()
            if not proceed.wait(3):
                raise TimeoutError("Removal did not complete")
            return loaded

        def activate():
            try:
                activate_layout(config, "original", config)
            except Exception as error:
                outcomes.append(error)

        with mock.patch("dialpad_layout_linux.load_layout", side_effect=paused_loader):
            thread = threading.Thread(target=activate)
            thread.start()
            try:
                self.assertTrue(validated.wait(3))
                remove_user_layout(config, original.source, original.revision, config,
                                   instance_id="live", replacement=("replacement", replacement.revision))
            finally:
                proceed.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], FileNotFoundError)
        self.assertEqual(requested_layout(config), "replacement")
        self.assertFalse(original.source.path.exists())

    def test_new_selection_prevents_removal_even_with_stale_status(self):
        config, original, replacement, _status = self.removal_fixture()
        activate_layout(config, "original", config)
        with self.assertRaisesRegex(ValueError, "still requested"):
            remove_user_layout(config, original.source, original.revision, config,
                               instance_id="live", replacement=("replacement", replacement.revision))
        self.assertTrue(original.source.path.exists())
        self.assertEqual(requested_layout(config), "original")

    def test_changed_replacement_after_acknowledgment_retains_original(self):
        config, original, replacement, _status = self.removal_fixture()
        changed = simple_document()
        changed["name"] = "New replacement revision"
        save_layout(replacement.source.path, normalize_document(changed), overwrite=True)
        with self.assertRaisesRegex(ValueError, "changed after acknowledgment"):
            remove_user_layout(config, original.source, original.revision, config,
                               instance_id="live", replacement=("replacement", replacement.revision))
        self.assertTrue(original.source.path.exists())


if __name__ == "__main__":
    unittest.main()
