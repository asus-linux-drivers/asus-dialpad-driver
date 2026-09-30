"""Data-loss boundaries in the optional editor (Qt dependency is optional)."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
except ImportError as exc:
    raise unittest.SkipTest("The optional layout editor requires PySide6") from exc

from dialpad_layout import load_path, normalize_document, save_layout
from dialpad_layout_editor import ActionDialog, DocumentDraft
from dialpad_layout_manager import LayoutManagerWindow, PendingRemoval, replacement_acknowledged


def document():
    return {
        "schema_version": 1,
        "name": "Original",
        "geometry": {
            "top_right_icon_width": 10, "top_right_icon_height": 10,
            "circle_diameter": 80, "center_button_diameter": 20,
            "circle_center_x": 50, "circle_center_y": 50,
        },
        "app_shortcuts": {
            "editor": {"center": {"trigger": "release", "duration": 0.5},
                       "Notify": {"command": "printf never-executed"},
                       "Scroll": {"clockwise": [{"key": "REL_WHEEL", "value": [1]}]}},
            "empty": {},
            "none": {"center": [{"key": "KEY_MUTE", "trigger": "release"}]},
        },
    }


class DraftTests(unittest.TestCase):
    def test_invalid_raw_apply_preserves_valid_form_saved_baseline_and_exact_raw_text(self):
        draft = DocumentDraft.opened(normalize_document(document()), "saved-revision")
        original = deepcopy(draft.document)
        baseline = draft.baseline
        raw = '{"schema_version": 1, "unfinished": '
        draft.edit_raw(raw)
        with self.assertRaises(ValueError):
            draft.apply_raw()
        self.assertEqual(draft.document, original)
        self.assertEqual(draft.raw_text, raw)
        self.assertEqual(draft.baseline, baseline)
        self.assertEqual(draft.revision, "saved-revision")
        self.assertTrue(draft.raw_pending)
        self.assertTrue(draft.dirty)
        with self.assertRaises(ValueError):
            draft.mark_saved(normalize_document(original), "new-revision")
        self.assertEqual(draft.raw_text, raw)

    def test_valid_raw_apply_preserves_rule_function_and_action_representation_order(self):
        draft = DocumentDraft.opened(normalize_document(document()), "saved-revision")
        changed = document()
        changed["app_shortcuts"] = {"empty": {}, "editor": changed["app_shortcuts"]["editor"],
                                    "none": changed["app_shortcuts"]["none"]}
        draft.edit_raw(json.dumps(changed))
        draft.apply_raw()
        self.assertEqual(list(draft.document["app_shortcuts"]), ["empty", "editor", "none"])
        self.assertEqual(list(draft.document["app_shortcuts"]["editor"]), ["center", "Notify", "Scroll"])
        self.assertIsInstance(draft.document["app_shortcuts"]["editor"]["center"], dict)
        self.assertEqual(draft.document["app_shortcuts"]["empty"], {})
        self.assertEqual(draft.revision, "saved-revision")
        self.assertTrue(draft.dirty)


class EditorBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["layout-editor-tests"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "config with spaces"
        (self.config / "layouts").mkdir(parents=True)
        self.install = self.root / "install"
        (self.install / "layouts").mkdir(parents=True)
        self.path = self.config / "layouts" / "first.json"
        save_layout(self.path, normalize_document(document()))
        save_layout(self.config / "layouts" / "second.json", normalize_document(document()))
        self.window = LayoutManagerWindow(self.config, self.install, offline=True)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.timer.stop)

    def test_canceling_dirty_navigation_keeps_selection_and_draft(self):
        first = self.window.source.identifier
        self.window.draft.document["name"] = "Local unsaved work"
        self.window._form_changed()
        index = next(index for index in range(self.window.layout_list.count())
                     if self.window.layout_list.item(index).data(256).identifier != first)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.layout_list.setCurrentRow(index)
        self.assertEqual(self.window.source.identifier, first)
        self.assertEqual(self.window.layout_list.currentItem().data(256).identifier, first)
        self.assertEqual(self.window.draft.document["name"], "Local unsaved work")
        self.assertTrue(self.window.draft.dirty)

    def test_saving_new_draft_during_navigation_retains_destination_identity(self):
        self.window.new_layout()
        self.window.draft.document["name"] = "New work to preserve"
        self.window._form_changed()
        destination = next(self.window.layout_list.item(index)
                           for index in range(self.window.layout_list.count())
                           if self.window.layout_list.item(index).data(256).identifier == "second")
        copy_path = self.config / "layouts" / "new_copy.json"
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save), \
                mock.patch.object(self.window, "_new_identifier", return_value=("new_copy", copy_path)):
            self.window._select_layout(destination, None)
        self.assertEqual(load_path(copy_path).name, "New work to preserve")
        self.assertEqual(self.window.source.identifier, "second")
        self.assertEqual(self.window.draft.document["name"], "Original")
        self.assertFalse(self.window.draft.dirty)

    def test_save_with_invalid_raw_keeps_both_drafts_and_file(self):
        before = self.path.read_bytes()
        valid = deepcopy(self.window.draft.document)
        raw = '{"name": "unfinished raw change", '
        self.window.raw_editor.setPlainText(raw)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Apply):
            self.assertFalse(self.window.save_current())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.window.draft.document, valid)
        self.assertEqual(self.window.raw_editor.toPlainText(), raw)
        self.assertTrue(self.window.draft.raw_pending)

    def test_external_revision_conflict_does_not_overwrite_either_draft(self):
        self.window.draft.document["name"] = "Local change"
        self.window._form_changed()
        external = document()
        external["name"] = "Externally edited"
        save_layout(self.path, normalize_document(external), overwrite=True)
        external_bytes = self.path.read_bytes()
        with mock.patch.object(self.window, "_conflict_dialog", return_value=False):
            self.assertFalse(self.window.save_current())
        self.assertEqual(self.path.read_bytes(), external_bytes)
        self.assertEqual(self.window.draft.document["name"], "Local change")
        self.assertTrue(self.window.draft.dirty)

    def test_builtin_in_same_directory_cannot_be_overwritten(self):
        (self.config / "bundled-layouts.json").write_text(json.dumps({
            "schema_version": 1, "layouts": ["layouts/first.json"],
        }))
        self.window.install_dir = self.config
        self.window.refresh_layouts()
        builtin = next(source for source in self.window.sources if source.identifier == "first")
        self.window.open_source(builtin)
        before = self.path.read_bytes()
        self.window.draft.document["name"] = "Must not be persisted"
        with mock.patch.object(QMessageBox, "warning"):
            self.assertFalse(self.window.save_current())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.window.raw_editor.isReadOnly())

    def test_offline_delete_refuses_without_touching_file(self):
        before = self.path.read_bytes()
        with mock.patch.object(QMessageBox, "warning"):
            self.window.delete_layout()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.window.source.identifier, "first")

    def test_unchanged_action_editor_preserves_combined_command_metadata_and_integer_boundaries(self):
        alternatives = [
            {"key": ["KEY_LEFTCTRL", "KEY_Z"], "command": "printf not-executed", "title": "Undo",
             "value": "printf query-not-executed", "icons": {"0": "/icon/off.svg", "1": "/icon/on.svg"},
             "modifier": "KEY_LEFTSHIFT", "treshold": 91.125, "duration": 0.375},
            {"key": ["REL_WHEEL", "REL_WHEEL_HI_RES"], "value": [2 ** 31 - 1, -(2 ** 31)], "title": "Scroll"},
            {"trigger": "release", "duration": 0.5},
        ]
        for original in alternatives:
            with self.subTest(action=original):
                dialog = ActionDialog(original, self.window)
                dialog.accept()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(dialog.result_value, original)
                dialog.deleteLater()


class RemovalTests(unittest.TestCase):
    def test_acknowledgment_requires_same_instance_exact_request_and_applied_revision(self):
        pending = PendingRemoval("deletion", Path("old.json"), "old", "old-rev",
                                 "replacement", "reviewed-rev", Path("replacement.json"), "instance-a", 8)
        status = {"instance_id": "instance-a", "generation": 9, "state": "applied",
                  "requested": {"identifier": "replacement", "revision": "reviewed-rev"},
                  "applied": {"identifier": "replacement", "revision": "reviewed-rev"}}
        self.assertTrue(replacement_acknowledged(status, pending))
        wrong_states = [
            {"instance_id": "instance-b"}, {"generation": 7}, {"state": "pending"}, {"state": "recovered"},
            {"applied": {"identifier": "replacement", "revision": "previous-rev"}},
            {"requested": {"identifier": "other", "revision": "reviewed-rev"}},
        ]
        for patch in wrong_states:
            with self.subTest(patch=patch):
                self.assertFalse(replacement_acknowledged({**status, **patch}, pending))



if __name__ == "__main__":
    unittest.main()
