"""Regression checks for automatic quality, review controls, and keyboard mappings."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from studio.project import Project
from studio.tools.line import actions, mapping, sources
from studio.tools.line.shortcuts import DEFAULT_MAPPINGS, KeyboardMappingsDialog, load_mappings, save_mappings, validate_mappings
from studio.tools.line.ui import LineWidget


def review_state():
    state = mapping.new_state()
    state["script"] = {"source": {"path": "Script.docx"}, "rows": [
        {"id": "s1", "line": 1, "text": "Hello world."},
        {"id": "s2", "line": 2, "text": "Second sentence."},
    ]}
    state["transcript"] = {"source": {"path": "Transcript.json"}, "words": [], "rows": [
        {"id": f"t{i}", "line": i + 1, "text": text, "word_start": i, "word_end": i,
         "start_ms": i * 1000, "end_ms": (i + 1) * 1000}
        for i, text in enumerate(("Hello world.", "Short.", "A longer different sentence."))
    ]}
    state["selected"] = {"script": 0, "transcript": 0}
    return state


class QualityTests(unittest.TestCase):
    def test_literal_comparison_and_character_length(self):
        for script, transcript, expected in (
            ("Hello world.", "Hello world.", "exact"),
            ("Hello world.", "Hello.", "flub"),
            ("Hello world.", "HELLO WORLD.", "partial"),
            ("Hello world.", "Hello world!", "partial"),
            ("Hello world.", "Hello world. Again.", "partial"),
            ("abcd", "a b", "flub"),
        ):
            with self.subTest(transcript=transcript):
                self.assertEqual(actions.infer_classification(script, transcript), expected)

    def test_link_infers_quality_for_every_caller(self):
        state = review_state()
        for index, quality in enumerate(("exact", "flub", "partial")):
            state["selected"]["transcript"] = index
            actions.link(None, state)
            self.assertEqual(state["links"][-1]["classification"], quality)
        self.assertEqual(mapping.coverage(state), {"s1": "exact", "s2": "missing"})

    def test_legacy_mapping_loads_as_partial_and_saves_new_quality(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "script.txt").write_text("Hello world.", encoding="utf-8")
            (root / "transcript.json").write_text(json.dumps({"words": [
                {"word": "Hello", "start": 0, "end": 1},
                {"word": "world.", "start": 1, "end": 2},
            ]}), encoding="utf-8")
            state = mapping.new_state()
            state["script"] = sources.load_script(root, "script.txt")
            state["transcript"] = sources.load_transcript(root, "transcript.json")
            state["selected"] = {"script": 0, "transcript": 0}
            actions.link(None, state)
            state["links"][0]["classification"] = "good"
            path = mapping.save_version(root, state, "legacy")
            loaded = mapping.read_mapping(root, path)
            self.assertEqual(loaded["links"][0]["classification"], "partial")
            new_path = mapping.save_version(root, loaded, "legacy")
            saved = json.loads((root / new_path).read_text())
            self.assertEqual(saved["links"][0]["classification"], "partial")


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])


class KeyboardMappingTests(QtTests):
    def test_defaults_persistence_disabled_keys_and_validation(self):
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "keys.ini"), QSettings.Format.IniFormat)
            self.assertEqual(load_mappings(settings), DEFAULT_MAPPINGS)
            custom = dict(DEFAULT_MAPPINGS, link="Ctrl+J", discard="")
            save_mappings(custom, settings)
            reopened = QSettings(settings.fileName(), QSettings.Format.IniFormat)
            self.assertEqual(load_mappings(reopened), custom)
        with self.assertRaises(ValueError):
            validate_mappings(dict(DEFAULT_MAPPINGS, link="A"))
        with self.assertRaises(ValueError):
            validate_mappings(dict(DEFAULT_MAPPINGS, link="Ctrl+G"))

    def test_dialog_restores_defaults_without_saving_on_cancel(self):
        dialog = KeyboardMappingsDialog(dict(DEFAULT_MAPPINGS, link="Ctrl+J"))
        dialog.restore_defaults()
        self.assertEqual(dialog.editors["link"].keySequence(), QKeySequence("Space"))
        dialog.reject()
        self.assertEqual(dialog.mappings["link"], "Ctrl+J")
        dialog.deleteLater()


class ReviewUiTests(QtTests):
    def setUp(self):
        project = object.__new__(Project)
        object.__setattr__(project, "root", Path("/example/project"))
        self.logging = patch("studio.actions._log").start()
        self.addCleanup(patch.stopall)
        with patch("studio.tools.line.ui.load_mappings", return_value=dict(DEFAULT_MAPPINGS)):
            self.widget = LineWidget(project, None)
        self.widget.state = review_state()
        self.widget._refresh()
        self.widget.resize(1200, 800)
        self.widget.show()
        self.widget.activateWindow()
        self.widget._panels["transcript"]["table"].setFocus()
        self.app.processEvents()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def key(self, key, modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.widget._panels["transcript"]["table"], key, modifiers)
        self.app.processEvents()

    def test_link_toggle_and_unlink_with_different_or_closed_script(self):
        self.widget.link_button.click()
        self.assertEqual(self.widget.state["links"][0]["classification"], "exact")
        self.assertEqual(self.widget.state["selected"], {"script": 0, "transcript": 1})
        self.assertEqual(self.logging.call_args.args[2], "Linked script sentence 1 to transcript sentence 1 as exact")
        self.widget.navigate("transcript", -1)
        self.widget.navigate("script", 1)
        self.assertEqual(self.widget.link_button.text(), "Unlink")
        self.widget._dispatch("line.close_script")
        self.assertTrue(self.widget.link_button.isEnabled())
        self.widget.link_button.click()
        self.assertEqual(self.widget.state["links"], [])
        self.assertEqual(self.widget.state["selected"]["transcript"], 0)

    def test_discard_restore_and_last_sentence(self):
        self.widget.discard_button.click()
        self.assertEqual(self.widget.state["discarded"], ["t0"])
        self.assertEqual(self.widget.state["selected"]["transcript"], 1)
        self.widget.navigate("transcript", -1)
        self.assertEqual(self.widget.discard_button.text(), "Restore")
        self.assertFalse(self.widget.link_button.isEnabled())
        self.widget.discard_button.click()
        self.assertEqual(self.widget.state["discarded"], [])
        self.assertEqual(self.widget.state["selected"]["transcript"], 0)
        self.widget.navigate("transcript", 100)
        self.widget.discard_button.click()
        self.assertEqual(self.widget.state["selected"]["transcript"], 2)
        self.assertEqual(self.widget.discard_button.text(), "Restore")

    def test_default_keys_execute_all_six_controls(self):
        self.key(Qt.Key.Key_Z)
        self.assertEqual(self.widget.state["selected"]["script"], 1)
        self.key(Qt.Key.Key_A)
        self.assertEqual(self.widget.state["selected"]["script"], 0)
        self.key(Qt.Key.Key_M)
        self.assertEqual(self.widget.state["selected"]["transcript"], 1)
        self.key(Qt.Key.Key_K)
        self.key(Qt.Key.Key_Space)
        self.assertEqual(len(self.widget.state["links"]), 1)
        self.assertEqual(self.widget.state["selected"]["transcript"], 1)
        self.key(Qt.Key.Key_Backspace)
        self.assertEqual(self.widget.state["discarded"], ["t1"])
        self.assertEqual(self.widget.state["selected"]["transcript"], 2)
        self.key(Qt.Key.Key_K)
        self.key(Qt.Key.Key_Backspace)
        self.assertEqual(self.widget.state["discarded"], [])
        self.assertEqual(self.widget.state["selected"]["transcript"], 1)

    def test_rebinding_removes_old_key_and_activates_new_key(self):
        self.widget.keyboard_mappings["link"] = "Ctrl+J"
        self.widget._apply_review_shortcuts()
        self.app.processEvents()
        self.key(Qt.Key.Key_Space)
        self.assertEqual(self.widget.state["links"], [])
        self.key(Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.widget.state["links"]), 1)

    def test_partial_link_on_last_line_stays_selected_and_can_unlink(self):
        self.widget.navigate("transcript", 100)
        self.widget.link_button.click()
        self.assertEqual(self.widget.state["links"][0]["classification"], "partial")
        self.assertEqual(self.widget.state["selected"]["transcript"], 2)
        self.assertEqual(self.widget.link_button.text(), "Unlink")
        self.assertIn("1 partial", self.widget.coverage_label.text())
        self.widget.link_button.click()
        self.assertEqual(self.widget.state["links"], [])
        self.assertEqual(self.widget.state["selected"]["transcript"], 2)

    def test_keyboard_dialog_captures_keys_without_triggering_review(self):
        dialog = KeyboardMappingsDialog(self.widget.keyboard_mappings, self.widget)
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
        editor = dialog.editors["script_up"]
        editor.setFocus()
        self.app.processEvents()
        QTest.keyClick(editor, Qt.Key.Key_Z)
        self.app.processEvents()
        self.assertEqual(editor.keySequence(), QKeySequence("Z"))
        self.assertEqual(self.widget.state["selected"]["script"], 0)
        dialog.reject()
        dialog.deleteLater()

    def test_failed_action_does_not_advance(self):
        with patch.object(self.widget, "_dispatch", return_value=(False, None)):
            self.widget.link_selected()
            self.widget.discard_selected()
        self.assertEqual(self.widget.state["selected"]["transcript"], 0)


if __name__ == "__main__":
    unittest.main()
