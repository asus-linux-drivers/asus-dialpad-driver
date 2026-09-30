"""Portable model regressions; no driver, Linux device, or shell action is loaded."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import dialpad_layout as model


ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ("asusvivobook16x", "proartp16", "zenbookpro14")


def document():
    return {
        "schema_version": 1,
        "geometry": {
            "top_right_icon_width": 250,
            "top_right_icon_height": 250,
            "circle_diameter": 919,
            "center_button_diameter": 364,
            "circle_center_x": 586,
            "circle_center_y": 573,
        },
        "app_shortcuts": {"none": {}},
    }


def python_source(mapping="{'none': {}}", *, extra=""):
    geometry = "\n".join(f"{key} = {value!r}" for key, value in document()["geometry"].items())
    return f"from libevdev import EV_KEY, EV_REL\n{geometry}\napp_shortcuts = {mapping}\n{extra}"


def put(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def manifest(directory: Path, *names: str):
    put(directory / "bundled-layouts.json", json.dumps({
        "schema_version": 1, "layouts": [f"layouts/{name}" for name in names],
    }))


class NormalizationTests(unittest.TestCase):
    def assert_issue(self, callback, path):
        with self.assertRaises(model.ValidationError) as caught:
            callback()
        self.assertTrue(any(path in issue.path for issue in caught.exception.issues), caught.exception)
        self.assertTrue(all(issue.severity == "error" for issue in caught.exception.issues))

    def test_bundled_roundtrips_preserve_behavior_and_precedence(self):
        for identifier in BUNDLED:
            with self.subTest(layout=identifier):
                layout = model.load_path(ROOT / "layouts" / f"{identifier}.py")
                reloaded = model.parse_json(model.dumps_layout(layout))
                self.assertEqual(reloaded.document, layout.document)
                self.assertEqual(reloaded.profiles, layout.profiles)
                self.assertEqual(list(reloaded.profiles), ["/usr/share/code/code", "firefox", "none"])
                self.assertEqual(reloaded.profiles["firefox"], model.Profile({}, {}))
                multi = reloaded.profiles["/usr/share/code/code"]
                self.assertEqual(list(multi.functions), ["Notifications", "Edit", "Volume", "Scroll", "Brightness"])
                self.assertEqual(multi.actions["center"][0].kind, "control")
                notifications = multi.functions["Notifications"]
                self.assertEqual(notifications.actions, {})
                self.assertIn("dconf write", notifications.command)
                self.assertIn("dconf read", notifications.metadata.value_query)
                fallback = reloaded.profiles["none"]
                self.assertEqual(fallback.functions, {})
                clockwise = fallback.actions["clockwise"]
                self.assertEqual([action.metadata.title for action in clockwise], ["Scroll", "Volume"])
                self.assertEqual(clockwise[0].keys, ("REL_WHEEL", "REL_WHEEL_HI_RES"))
                self.assertEqual(clockwise[0].values, (1, 120))
                self.assertIsNone(clockwise[0].metadata.value_query)
                self.assertEqual(clockwise[1].modifier, "KEY_LEFTSHIFT")
                self.assertEqual(clockwise[1].values, ())
                self.assertIn("pactl", clockwise[1].metadata.value_query)
                self.assertEqual(fallback.actions["counterclockwise"][0].values, (-1, -120))
                self.assertEqual(reloaded.geometry["circle_diameter"], 1400 if identifier == "asusvivobook16x" else 919)

    def test_singletons_empty_mappings_and_metadata_are_lossless(self):
        raw = document()
        raw["name"] = "../A display label is not a destination"
        raw["geometry"] = dict(reversed(list(raw["geometry"].items())))
        raw["app_shortcuts"] = {
            "window-b": {
                "Second": {"command": "notify-send two", "icons": {"on": "a.svg", "off": "b.svg"}},
                "center": {"trigger": "release", "duration": 0.5},
                "First": {"clockwise": [], "title": "Different display label", "treshold": 180},
            },
            "window-a": {},
            "none": {
                "center": {},
                "clockwise": {"key": "KEY_A", "command": "notify-send one", "value": "printf 42", "unit": "%"},
                "counterclockwise": [{"key": ["KEY_LEFTCTRL", "KEY_Z"]}],
            },
        }
        original = copy.deepcopy(raw)
        layout = model.normalize_document(raw)
        raw["app_shortcuts"]["window-a"]["center"] = {"key": "KEY_B"}
        self.assertEqual(layout.document, original)
        result = model.parse_json(model.dumps_layout(layout))
        self.assertEqual(result.document, original)
        self.assertEqual(list(result.geometry), list(original["geometry"]))
        self.assertEqual(list(result.profiles), ["window-b", "window-a", "none"])
        self.assertEqual(list(result.profiles["window-b"].functions), ["Second", "First"])
        self.assertEqual(list(result.profiles["window-b"].functions["Second"].metadata.icons), ["on", "off"])
        self.assertEqual(result.profiles["window-a"], model.Profile({}, {}))
        action = result.profiles["none"].actions["clockwise"][0]
        self.assertEqual(action.kind, "keys")
        self.assertEqual(action.command, "notify-send one")
        self.assertEqual(action.metadata.value_query, "printf 42")
        self.assertEqual(action.trigger, "release")
        self.assertEqual(action.duration, 0.0)
        self.assertIsInstance(result.document["app_shortcuts"]["none"]["clockwise"], dict)
        self.assertIsInstance(result.document["app_shortcuts"]["none"]["counterclockwise"], list)

    def test_catalog_supports_keys_buttons_and_relative_axes(self):
        for key in ("KEY_A", "BTN_LEFT", "REL_WHEEL_HI_RES"):
            raw = document()
            action = {"key": key}
            if key.startswith("REL_"):
                action["value"] = [120]
            raw["app_shortcuts"]["none"]["center"] = action
            normalized = model.normalize_document(raw).profiles["none"].actions["center"][0]
            self.assertEqual(normalized.keys, (key,))
        self.assertNotIn("KEY_CNT", model.event_names())
        self.assertNotIn("REL_MAX", model.event_names())

    def test_invalid_action_combinations_report_leaf_paths(self):
        cases = (
            ({"key": "A"}, ".key"),
            ({"key": "KEY_THIS_DOES_NOT_EXIST"}, ".key"),
            ({"key": []}, ".key"),
            ({"key": ["KEY_A", "REL_X"], "value": [1, 2]}, ".key"),
            ({"key": "REL_X"}, ".value"),
            ({"key": ["REL_X", "REL_Y"], "value": [1]}, ".value"),
            ({"key": "REL_X", "value": [True]}, ".value[0]"),
            ({"key": "REL_X", "value": [1.5]}, ".value[0]"),
            ({"key": "REL_X", "value": [2 ** 31]}, ".value[0]"),
            ({"key": "REL_X", "value": "printf 1"}, ".value"),
            ({"key": "KEY_A", "value": [1]}, ".value"),
            ({"value": [1]}, ".value"),
            ({"modifier": ["KEY_LEFTSHIFT"]}, ".modifier"),
            ({"modifier": "REL_X"}, ".modifier"),
            ({"trigger": "repeat"}, ".trigger"),
            ({"trigger": True}, ".trigger"),
            ({"duration": -1}, ".duration"),
            ({"duration": True}, ".duration"),
            ({"duration": float("inf")}, ".duration"),
            ({"command": 7}, ".command"),
            ({"title": None}, ".title"),
            ({"icons": {"on": False}}, ".icons.on"),
            ({"treshold": 0}, ".treshold"),
            ({"future_field": 1}, ".future_field"),
        )
        for action, ending in cases:
            with self.subTest(action=action):
                raw = document()
                raw["app_shortcuts"]["none"]["clockwise"] = [action]
                self.assert_issue(lambda: model.normalize_document(raw), "$.app_shortcuts.none.clockwise[0]" + ending)

    def test_malformed_envelopes_geometry_and_functions_fail_without_loss(self):
        changes = (
            (lambda raw: raw.update(schema_version=True), "$.schema_version"),
            (lambda raw: raw.update(schema_version=2), "$.schema_version"),
            (lambda raw: raw.update(future_extension={}), "$.future_extension"),
            (lambda raw: raw["app_shortcuts"].pop("none"), "$.app_shortcuts.none"),
            (lambda raw: raw["geometry"].update(circle_diameter=0), "$.geometry.circle_diameter"),
            (lambda raw: raw["geometry"].update(center_button_diameter=920), "$.geometry.center_button_diameter"),
            (lambda raw: raw["geometry"].update(top_right_icon_width=-1), "$.geometry.top_right_icon_width"),
            (lambda raw: raw["geometry"].update(circle_center_x=float("nan")), "$.geometry.circle_center_x"),
            (lambda raw: raw["geometry"].update(circle_center_y=10 ** 1000), "$.geometry.circle_center_y"),
            (lambda raw: raw["geometry"].update(screen_pixels=True), "$.geometry.screen_pixels"),
            (lambda raw: raw["app_shortcuts"]["none"].update(Volume={"typo": []}), "$.app_shortcuts.none.Volume.typo"),
            (lambda raw: raw["app_shortcuts"]["none"].update(center=None), "$.app_shortcuts.none.center"),
        )
        for change, path in changes:
            with self.subTest(path=path):
                raw = document()
                change(raw)
                self.assert_issue(lambda: model.normalize_document(raw), path)
        cyclic = document()
        cyclic["loop"] = cyclic
        self.assert_issue(lambda: model.normalize_document(cyclic), "$.loop")

    def test_json_duplicates_and_nonfinite_numbers_have_precise_paths(self):
        valid = json.dumps(document())
        malformed = (
            (valid.replace('"circle_center_x": 586', '"circle_center_x": 586, "circle_center_x": 587'), "$.geometry.circle_center_x"),
            (valid.replace('"none": {}', '"none": {}, "none": {}'), "$.app_shortcuts.none"),
            (valid.replace('"circle_center_y": 573', '"circle_center_y": NaN'), "$.geometry.circle_center_y"),
            (valid.replace('"circle_center_y": 573', '"circle_center_y": -Infinity'), "$.geometry.circle_center_y"),
            (valid.replace('"circle_center_y": 573', '"circle_center_y": 1e400'), "$.geometry.circle_center_y"),
        )
        for text, path in malformed:
            with self.subTest(path=path, text=text):
                self.assert_issue(lambda: model.parse_json(text), path)
        self.assert_issue(lambda: model.parse_json('{"schema_version":'), "$")

    def test_static_import_never_executes_code_or_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "executed"
            shell = f"touch {marker}"
            mapping = repr({"none": {"Notifications": {"command": shell, "value": shell}, "center": {"command": shell}}})
            imported = model.parse_python(python_source(mapping))
            model.parse_json(model.dumps_layout(imported))
            self.assertFalse(marker.exists())
            for operation in (
                f"open({str(marker)!r}, 'w').write('executed')",
                f"import os\nos.system({shell!r})",
                f"def dynamic():\n    open({str(marker)!r}, 'w').write('executed')",
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(model.ValidationError):
                        model.parse_python(python_source(mapping, extra=operation))
                    self.assertFalse(marker.exists())

    def test_static_aliases_and_negative_values_are_supported_but_not_dynamic_values(self):
        source = python_source("{'none': {'center': {'key': EV_KEY.BTN_LEFT}, 'clockwise': {'key': EV_REL.REL_WHEEL, 'value': [-1]}}}")
        aliased = source.replace("from libevdev import EV_KEY, EV_REL", "import libevdev as events").replace("EV_KEY.", "events.EV_KEY.").replace("EV_REL.", "events.EV_REL.")
        layout = model.parse_python(aliased)
        self.assertEqual(layout.profiles["none"].actions["clockwise"][0].values, (-1,))
        direct_alias = source.replace("EV_KEY, EV_REL", "EV_KEY as keys, EV_REL").replace("EV_KEY.", "keys.")
        self.assertEqual(model.parse_python(direct_alias).profiles, layout.profiles)
        for source in (
            python_source("{'none': {'center': {'key': EV_KEY.REL_X}}}"),
            python_source("{'none': {'center': {'key': EV_KEY.KEY_A, 'key': EV_KEY.KEY_B}}}"),
            python_source("dict(none={})"),
            python_source("{'none': {'center': ({'key': EV_KEY.KEY_A},)}}"),
            python_source(extra="circle_diameter = 1000"),
            python_source(extra="unknown_extension = {}"),
        ):
            with self.subTest(source=source):
                with self.assertRaises(model.ValidationError):
                    model.parse_python(source)


class DiscoveryTests(unittest.TestCase):
    def test_directory_precedence_beats_format_and_invalid_winner_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config, install = base / "config", base / "install"
            put(config / "layouts" / "shared.py", "raise RuntimeError('must not execute')")
            put(install / "layouts" / "shared.json", json.dumps(document()))
            put(config / "layouts" / "pair.py", python_source())
            put(config / "layouts" / "pair.json", "invalid preferred JSON")
            put(config / "layouts" / ".hidden.json", "ignored")
            put(config / "layouts" / "__init__.py", "ignored")
            put(config / "layouts" / "notes.txt", "ignored")
            (config / "layouts" / "directory.json").mkdir()
            manifest(install, "shared.json")
            sources = model.discover_layouts(config, install)
            self.assertEqual([source.identifier for source in sources], ["pair", "shared"])
            chosen = model.resolve_layout("shared", config, install)
            self.assertEqual(chosen.path, config / "layouts" / "shared.py")
            self.assertEqual(chosen.provenance, "user")
            self.assertEqual(chosen.overrides, (install / "layouts" / "shared.json",))
            with self.assertRaises(model.ValidationError):
                model.load_path(chosen.path)
            pair = model.resolve_layout("pair", config, install)
            self.assertEqual(pair.format, "json")
            self.assertEqual(pair.overrides, (config / "layouts" / "pair.py",))
            with self.assertRaises(model.ValidationError):
                model.load_path(pair.path)

    def test_manifest_protects_file_identity_when_directories_coincide(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            put(root / "layouts" / "bundled.py", "not even valid Python")
            put(root / "layouts" / "custom.py", python_source())
            manifest(root, "bundled.py")
            sources = {source.identifier: source for source in model.discover_layouts(root, root)}
            self.assertTrue(sources["bundled"].builtin)
            self.assertEqual(sources["bundled"].overrides, ())
            self.assertFalse(sources["custom"].builtin)
            put(root / "layouts" / "bundled.json", json.dumps(document()))
            winner = model.resolve_layout("bundled", root, root)
            self.assertFalse(winner.builtin)
            self.assertEqual(winner.overrides, (root / "layouts" / "bundled.py",))

    def test_equivalent_directories_are_deduplicated_and_builtin_aliases_stay_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            install, config = base / "install", base / "config"
            put(install / "layouts" / "builtin.py", python_source())
            manifest(install, "builtin.py")
            config.mkdir()
            try:
                (config / "layouts").symlink_to(install / "layouts", target_is_directory=True)
                (install / "layouts" / "alias.py").symlink_to(install / "layouts" / "builtin.py")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            sources = model.discover_layouts(config, install)
            self.assertEqual([source.identifier for source in sources], ["alias", "builtin"])
            self.assertTrue(all(source.builtin for source in sources))
            self.assertTrue(all(source.overrides == () for source in sources))

    def test_creation_identifiers_and_symlinks_cannot_escape_layout_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for identifier in ("", ".", "..", "../outside", "/tmp/outside", "a/b", "a\\b", "a.json", "has spaces"):
                with self.subTest(identifier=identifier):
                    with self.assertRaises(model.ValidationError):
                        model.user_layout_path(root, identifier)
            self.assertEqual(model.user_layout_path(root, "User-layout_2"), root / "layouts" / "User-layout_2.json")
            (root / "config").mkdir()
            (root / "outside").mkdir()
            try:
                (root / "config" / "layouts").symlink_to(root / "outside", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaises(model.ValidationError):
                model.user_layout_path(root / "config", "copy")
            (root / "safe" / "layouts").mkdir(parents=True)
            put(root / "outside" / "data.json", "external")
            (root / "safe" / "layouts" / "alias.json").symlink_to(root / "outside" / "data.json")
            with self.assertRaises(model.ValidationError):
                model.user_layout_path(root / "safe", "alias")
            self.assertEqual((root / "outside" / "data.json").read_text(), "external")

    def test_manifest_cannot_claim_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for entry in ("../outside.py", "/outside.py", "layouts/../outside.py", "layouts/bad name.py"):
                put(root / "bundled-layouts.json", json.dumps({"schema_version": 1, "layouts": [entry]}))
                with self.subTest(entry=entry):
                    with self.assertRaises(model.ValidationError):
                        model.discover_layouts(root, root)


class AtomicSaveTests(unittest.TestCase):
    def test_saved_revision_matches_exact_bytes_and_existing_mode_survives(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom.json"
            initial = model.normalize_document(document())
            first = model.save_layout(path, initial)
            self.assertEqual(first, model.revision_bytes(path.read_bytes()))
            path.chmod(0o640)
            mode = path.stat().st_mode & 0o777
            changed = document()
            changed["name"] = "Second revision"
            second = model.save_layout(path, model.normalize_document(changed), overwrite=True, expected_revision=first)
            self.assertNotEqual(first, second)
            self.assertEqual(second, model.revision_bytes(path.read_bytes()))
            self.assertEqual(model.load_path(path).name, "Second revision")
            self.assertEqual(path.stat().st_mode & 0o777, mode)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_conflicts_overwrites_and_failed_replace_preserve_source_and_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            put(source, python_source())
            source_bytes = source.read_bytes()
            path = root / "copy.json"
            layout = model.load_path(source)
            revision = model.save_layout(path, layout)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                model.save_layout(path, layout)
            self.assertEqual(path.read_bytes(), original)
            path.write_bytes(b"external changes")
            with self.assertRaises(FileExistsError):
                model.save_layout(path, layout, overwrite=True, expected_revision=revision)
            self.assertEqual(path.read_bytes(), b"external changes")
            external_revision = model.revision_bytes(path.read_bytes())
            with mock.patch.object(model.os, "replace", side_effect=PermissionError("denied before replacement")):
                with self.assertRaises(PermissionError):
                    model.save_layout(path, layout, overwrite=True, expected_revision=external_revision)
            self.assertEqual(path.read_bytes(), b"external changes")
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(set(root.iterdir()), {source, path})
            path.unlink()
            with self.assertRaises(FileExistsError):
                model.save_layout(path, layout, overwrite=True, expected_revision=external_revision)
            self.assertFalse(path.exists())

    def test_external_edit_during_temporary_write_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "copy.json"
            layout = model.normalize_document(document())
            revision = model.save_layout(path, layout)
            with mock.patch.object(model.os, "fsync", side_effect=lambda fd: path.write_bytes(b"new external content")):
                with self.assertRaises(FileExistsError):
                    model.save_layout(path, layout, overwrite=True, expected_revision=revision)
            self.assertEqual(path.read_bytes(), b"new external content")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_concurrent_new_file_wins_over_non_overwrite_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "copy.json"
            layout = model.normalize_document(document())
            link = model.os.link

            def competing_create(temporary_path, destination):
                Path(destination).write_bytes(b"another editor saved first")
                link(temporary_path, destination)

            with mock.patch.object(model.os, "link", side_effect=competing_create):
                with self.assertRaises(FileExistsError):
                    model.save_layout(path, layout)
            self.assertEqual(path.read_bytes(), b"another editor saved first")
            self.assertEqual(list(path.parent.iterdir()), [path])


class PortableCliTests(unittest.TestCase):
    def test_listing_validation_and_conversion_need_no_linux_or_qt_imports(self):
        code = r'''
import importlib.abc
import pathlib
import sys
class BlockPlatformModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'libevdev', 'fcntl', 'PySide6', 'pyinotify', 'dialpad', 'dialpad_layout_linux'}:
            raise AssertionError('platform import in portable operation: ' + fullname)
sys.meta_path.insert(0, BlockPlatformModules())
sys.path.insert(0, sys.argv[1])
import dialpad_layout
root = pathlib.Path(sys.argv[2])
common = ['--config-dir', str(root), '--install-dir', str(root)]
assert dialpad_layout.main(common + ['list', '--identifiers']) == 0
assert dialpad_layout.main(common + ['validate', 'safe']) == 0
assert dialpad_layout.main(common + ['convert', 'safe', str(root / 'converted.json')]) == 0
assert dialpad_layout.load_path(root / 'converted.json').profiles['none'].actions['center'][0].keys == ('KEY_A',)
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "executed"
            put(root / "layouts" / "dynamic.py", f"open({str(marker)!r}, 'w').write('bad')")
            put(root / "layouts" / "safe.py", python_source("{'none': {'center': {'key': EV_KEY.KEY_A}}}"))
            original = (root / "layouts" / "safe.py").read_bytes()
            completed = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT), str(root)],
                                       capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual((root / "layouts" / "safe.py").read_bytes(), original)
            self.assertTrue((root / "converted.json").is_file())

    def test_cli_never_guesses_configuration_directory(self):
        environment = dict(os.environ)
        environment.pop("DIALPAD_CONFIG_DIR", None)
        completed = subprocess.run([sys.executable, str(ROOT / "dialpad_layout.py"), "list"],
                                   env=environment, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--config-dir or DIALPAD_CONFIG_DIR is required", completed.stderr)


if __name__ == "__main__":
    unittest.main()
