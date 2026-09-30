"""Language settings and offline contextual help must not alter layout data."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QToolButton
except ImportError as exc:
    raise unittest.SkipTest("The optional manager requires PySide6") from exc

from dialpad_help import HelpDialog
from dialpad_i18n import current_language, initialize_i18n, language_for, load_catalog, tr
from dialpad_layout import normalize_document, save_layout
from dialpad_layout_editor import ActionDialog
from dialpad_layout_manager import LayoutManagerWindow


def document(command):
    return {
        "schema_version": 1, "name": "Save / 使用者標題",
        "geometry": {"top_right_icon_width": 10, "top_right_icon_height": 10,
                     "circle_diameter": 80, "center_button_diameter": 20,
                     "circle_center_x": 50, "circle_center_y": 50},
        "app_shortcuts": {"none": {"Volume": {"title": "Help / 音量", "command": command,
                                               "value": command, "icon": "/not/an/installed/icon.svg"}}},
    }


class HelpLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["manager-help-tests"])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = QSettings(str(self.root / "manager.ini"), QSettings.Format.IniFormat)
        initialize_i18n(self.app, settings=self.settings, preference="en_US")
        self.addCleanup(initialize_i18n, self.app, settings=self.settings, preference="en_US")
        self.marker = self.root / "must-not-run"
        self.config = self.root / "configuration"
        (self.config / "layouts").mkdir(parents=True)
        self.source = self.config / "layouts" / "user.json"
        self.original = document("touch " + shlex.quote(str(self.marker)))
        save_layout(self.source, normalize_document(self.original))
        (self.config / "dialpad_dev").write_text("[main]\nlayout = user\n", encoding="utf-8")

    def window(self):
        window = LayoutManagerWindow(self.config, self.config, offline=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.timer.stop)
        return window

    def test_system_locale_mapping_and_explicit_preference(self):
        cases = {"en_US": "en_US", "de_DE": "en_US", "zh_CN": "zh_CN", "zh-SG": "zh_CN",
                 "zh-TW": "zh_TW", "zh_HK": "zh_TW", "zh_MO": "zh_TW", "zh-Hant-CN": "zh_TW"}
        for locale, expected in cases.items():
            with self.subTest(locale=locale):
                self.assertEqual(language_for("system", locale), expected)
                self.assertEqual(language_for("en_US", locale), "en_US")
                self.assertEqual(language_for("zh_TW", locale), "zh_TW")
        self.assertEqual(language_for("obsolete-language", "zh_HK"), "zh_TW")
        with self.assertRaises(ValueError):
            load_catalog("en", self.root)

    def test_legacy_english_preference_resolves_to_the_standard_catalog(self):
        self.settings.setValue("ui/language", "en")
        self.settings.sync()
        stored = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(initialize_i18n(self.app, settings=stored, system_locale="zh_CN"), "en_US")
        window = self.window()
        selector = window.findChild(QComboBox, "interfaceLanguage")
        self.assertEqual(selector.currentData(), "en_US")
        self.assertEqual(selector.currentText(), "English")
        self.assertEqual(tr("common.language.names.en_us"), "English")

    def test_changing_language_preserves_unapplied_raw_draft_and_config_until_next_start(self):
        window = self.window()
        raw = '{"name": "unsaved and invalid '
        window.raw_editor.setPlainText(raw)
        original_form = deepcopy(window.draft.document)
        source_bytes = self.source.read_bytes()
        config_bytes = (self.config / "dialpad_dev").read_bytes()
        title = window.windowTitle()
        selector = window.findChild(QComboBox, "interfaceLanguage")
        selector.setCurrentIndex(selector.findData("zh_TW"))
        self.assertEqual(current_language(), "en_US")
        self.assertEqual(window.windowTitle(), title)
        self.assertEqual(window.raw_editor.toPlainText(), raw)
        self.assertTrue(window.draft.raw_pending)
        self.assertEqual(window.draft.document, original_form)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual((self.config / "dialpad_dev").read_bytes(), config_bytes)
        # A fresh settings reader and new window represent a subsequent launch.
        fresh = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(initialize_i18n(self.app, settings=fresh, system_locale="en_US"), "zh_TW")
        restarted = self.window()
        self.assertEqual(restarted.draft.document, original_form)
        self.assertEqual(restarted.display_name.text(), self.original["name"])
        self.assertEqual(restarted.ring.canvas.titles[0], "Help / 音量")

    def test_translated_action_selectors_keep_canonical_trigger_and_user_values(self):
        original = {"key": ["KEY_LEFTCTRL", "KEY_Z"], "trigger": "immediate",
                    "modifier": "KEY_LEFTSHIFT", "title": "Save / 使用者標題",
                    "command": "printf 'Help {unchanged}'", "value": "printf 42", "unit": "%"}
        for language in ("zh_CN", "zh_TW"):
            with self.subTest(language=language):
                initialize_i18n(self.app, settings=self.settings, preference=language)
                dialog = ActionDialog(original)
                self.addCleanup(dialog.deleteLater)
                self.assertEqual(dialog.trigger.currentData(), "immediate")
                dialog.accept()
                self.assertEqual(dialog.result_value, original)
                dialog.trigger.setCurrentIndex(dialog.trigger.findData("release"))
                dialog.accept()
                self.assertEqual(dialog.result_value, {**original, "trigger": "release"})

    def test_help_search_filters_explanations_and_recovers_from_no_results(self):
        initialize_i18n(self.app, settings=self.settings, preference="zh_CN")
        dialog = HelpDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.search.setText("REL_WHEEL_HI_RES")
        visible = [dialog.topics.item(index).data(Qt.ItemDataRole.UserRole)
                   for index in range(dialog.topics.count()) if not dialog.topics.item(index).isHidden()]
        self.assertEqual(visible, ["events"])
        self.assertIn("REL_WHEEL_HI_RES", dialog.browser.toPlainText())
        dialog.search.setText("no-such-topic-918329")
        self.assertTrue(dialog.empty.isVisibleTo(dialog))
        self.assertEqual(dialog.browser.toPlainText(), "")
        dialog.select_topic("save_activate")
        self.assertEqual(dialog.topics.currentItem().data(Qt.ItemDataRole.UserRole), "save_activate")
        self.assertEqual(dialog.search.text(), "")
        self.assertFalse(dialog.empty.isVisibleTo(dialog))

    def test_help_and_readonly_preview_never_contact_runtime_execute_or_mutate(self):
        (self.config / "bundled-layouts.json").write_text(json.dumps({
            "schema_version": 1, "layouts": ["layouts/user.json"]}), encoding="utf-8")
        before = self.source.read_bytes()
        with mock.patch("socket.socket", side_effect=AssertionError("Help attempted socket access")):
            window = self.window()
            window.show()
            self.app.processEvents()
            self.assertFalse(window.save_button.isEnabled())
            question = window.findChild(QToolButton, "help_save_activate")
            self.assertTrue(question.isEnabled())
            QTest.mouseClick(question, Qt.MouseButton.LeftButton)
            dialog = window._dialpad_help_dialog
            self.assertEqual(dialog.topics.currentItem().data(Qt.ItemDataRole.UserRole), "save_activate")
            window.ring.canvas.grab()
            dialog.close()
            window.hide()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(self.marker.exists())
        self.assertFalse(window.draft.dirty)

    def test_f1_in_editor_opens_focused_input_help_instead_of_generic_dialog_help(self):
        dialog = ActionDialog({"key": "KEY_A"})
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        dialog.activateWindow()
        dialog.metadata.fields["value"].setFocus()
        self.app.processEvents()
        QTest.keyClick(dialog.metadata.fields["value"], Qt.Key.Key_F1)
        help_window = dialog._dialpad_help_dialog
        self.assertEqual(help_window.topics.currentItem().data(Qt.ItemDataRole.UserRole), "value")
        help_window.close()
        dialog.hide()

    def test_f1_relative_numeric_value_explains_events_not_shell_queries(self):
        original = {"key": "REL_WHEEL", "value": [-2], "trigger": "immediate"}
        dialog = ActionDialog(original)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        dialog.activateWindow()
        numeric = dialog.events.cellWidget(0, 1)
        self.assertEqual(numeric.objectName(), "relativeEventValue")
        numeric.setFocus()
        self.app.processEvents()
        QTest.keyClick(numeric, Qt.Key.Key_F1)
        help_window = dialog._dialpad_help_dialog
        self.assertEqual(help_window.topics.currentItem().data(Qt.ItemDataRole.UserRole), "events")
        help_window.close()
        dialog.accept()
        self.assertEqual(dialog.result_value, original)
        dialog.hide()

    def test_nested_path_lookup_uses_english_resource_and_formats_values_once(self):
        directory = self.root / "translations"
        directory.mkdir()
        english = {"sample": {"message": "English {detail}", "fallback": "Fallback {detail}"}}
        translated = {"sample": {"message": "Translated {detail}"}}
        (directory / "en_US.json").write_text(json.dumps(english), encoding="utf-8")
        (directory / "zh_CN.json").write_text(json.dumps(translated), encoding="utf-8")
        initialize_i18n(self.app, settings=self.settings, preference="zh_CN", catalog_dir=directory)
        opaque = "sample.message {unexpanded} /home/example/KEY_A"
        self.assertEqual(tr("sample.message", detail=opaque), "Translated " + opaque)
        self.assertEqual(tr("sample.fallback", detail=opaque), "Fallback " + opaque)
        # Editing English wording does not change its lookup identity.
        english["sample"]["fallback"] = "Revised {detail}"
        (directory / "en_US.json").write_text(json.dumps(english), encoding="utf-8")
        initialize_i18n(self.app, settings=self.settings, preference="en_US", catalog_dir=directory)
        self.assertEqual(tr("sample.fallback", detail=opaque), "Revised " + opaque)

    def test_message_keys_reject_source_text_spaces_and_nonleaf_paths(self):
        for invalid in ("Save", "manager.save label", " manager.save", "manager.save\\t",
                        "manager..save", ".manager.save", "manager.save.", "manager.保存"):
            with self.subTest(key=invalid), self.assertRaises(ValueError):
                tr(invalid)
        for missing in ("manager", "manager.not_a_real_message"):
            with self.subTest(key=missing):
                expected = ValueError if "." not in missing else KeyError
                with self.assertRaises(expected):
                    tr(missing)
        with self.assertRaises(KeyError):
            tr("help.topics")

    def test_catalog_rejects_flat_dotted_properties_and_whitespace_segments(self):
        for invalid in ({"sample.message": "Flat"}, {"sample": {"bad key": "Spaced"}},
                        {"sample": ["Not an object"]}, {"sample": {"message": ""}}):
            with self.subTest(catalog=invalid):
                (self.root / "en_US.json").write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_catalog("en_US", self.root)

    def test_incompatible_translation_falls_back_to_english_without_losing_other_messages(self):
        (self.root / "en_US.json").write_text(json.dumps({
            "sample": {"first": "Original {name}", "second": "Second"}}), encoding="utf-8")
        (self.root / "zh_TW.json").write_text(json.dumps({
            "sample": {"first": "Wrong {renamed}", "second": "Translated second"}}), encoding="utf-8")
        with self.assertLogs("dialpad_i18n", level="WARNING"):
            initialize_i18n(self.app, settings=self.settings, preference="zh_TW", catalog_dir=self.root)
        self.assertEqual(tr("sample.first", name="opaque"), "Original opaque")
        self.assertEqual(tr("sample.second"), "Translated second")

    def test_translation_format_spec_and_conversion_mismatches_fall_back(self):
        (self.root / "en_US.json").write_text(json.dumps({"sample": {
            "numeric": "Status {detail}",
            "conversion": "Detail {detail!s}",
            "reordered": "{first} / {second}",
        }}), encoding="utf-8")
        (self.root / "zh_CN.json").write_text(json.dumps({"sample": {
            "numeric": "数值 {detail:d}",
            "conversion": "内容 {detail!r}",
            "reordered": "{second} 和 {first}",
        }}), encoding="utf-8")
        with self.assertLogs("dialpad_i18n", level="WARNING") as logged:
            initialize_i18n(self.app, settings=self.settings, preference="zh_CN", catalog_dir=self.root)
        self.assertEqual(len(logged.output), 2)
        self.assertEqual(tr("sample.numeric", detail="opaque"), "Status opaque")
        self.assertEqual(tr("sample.conversion", detail="opaque"), "Detail opaque")
        self.assertEqual(tr("sample.reordered", first="one", second="two"), "two 和 one")

    def test_missing_optional_translation_uses_english_but_english_is_required(self):
        (self.root / "en_US.json").write_text(json.dumps({"sample": {"message": "Required English"}}), encoding="utf-8")
        with self.assertLogs("dialpad_i18n", level="WARNING"):
            initialize_i18n(self.app, settings=self.settings, preference="zh_CN", catalog_dir=self.root)
        self.assertEqual(tr("sample.message"), "Required English")
        (self.root / "en_US.json").unlink()
        with self.assertRaises(FileNotFoundError):
            initialize_i18n(self.app, settings=self.settings, preference="en_US", catalog_dir=self.root)


if __name__ == "__main__":
    unittest.main()
