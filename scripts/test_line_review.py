"""Regression checks for automatic quality, review controls, and keyboard mappings."""

import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QPoint, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from studio.project import Project
from studio.tools.line import actions, combined, mapping, sources
from studio.tools.line.shortcuts import DEFAULT_MAPPINGS, KeyboardMappingsDialog, load_mappings, save_mappings, validate_mappings
from studio.tools.line.ui import LineWidget


def review_state():
    state = mapping.new_state()
    state["script"] = sources.script_words({"source": {"path": "Script.docx"}, "rows": [
        {"id": "s1", "line": 1, "text": "Hello world."},
        {"id": "s2", "line": 2, "text": "Second sentence."},
    ]})
    transcript_words = ["Hello", "world.", "Short.", "A", "longer", "different", "sentence."]
    state["transcript"] = {"source": {"path": "Transcript.json"}, "words": [
        {"index": i, "text": text, "start_ms": i * 1000, "end_ms": (i + 1) * 1000}
        for i, text in enumerate(transcript_words)
    ], "rows": [
        {"id": f"t{i}", "line": i + 1, "text": text, "word_start": start, "word_end": end,
         "start_ms": start * 1000, "end_ms": (end + 1) * 1000}
        for i, (text, start, end) in enumerate((("Hello world.", 0, 1), ("Short.", 2, 2), ("A longer different sentence.", 3, 6)))
    ]}
    state["groupings"] = {panel: mapping.initial_groupings(state[panel]) for panel in mapping.PANELS}
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
        self.assertEqual(mapping.coverage(state), {(0, 1): "exact", (2, 3): "missing"})

    def test_good_quality_loads_as_partial_and_saves_new_quality(self):
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
            state["groupings"] = {panel: mapping.initial_groupings(state[panel]) for panel in mapping.PANELS}
            state["selected"] = {"script": 0, "transcript": 0}
            actions.link(None, state)
            state["links"][0]["classification"] = "good"
            path = mapping.save_version(root, state, "legacy")
            loaded = mapping.read_mapping(root, path)
            self.assertEqual(loaded["links"][0]["classification"], "partial")
            new_path = mapping.save_version(root, loaded, "legacy")
            saved = json.loads((root / new_path).read_text())
            self.assertEqual(saved["links"][0]["classification"], "partial")


class WordGroupingTests(unittest.TestCase):
    def test_split_merge_link_and_save_reopen_without_changing_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            script_path = root / "script.txt"
            transcript_path = root / "transcript.json"
            script_path.write_text("One two. Three four.", encoding="utf-8")
            transcript_path.write_text(json.dumps({"words": [
                {"text": text, "start_ms": i * 100, "end_ms": (i + 1) * 100}
                for i, text in enumerate(("One", "two.", "Three", "four."))
            ]}), encoding="utf-8")
            original_script = script_path.read_bytes()
            original_transcript = transcript_path.read_bytes()
            state = mapping.new_state()
            project = SimpleNamespace(root=root)
            actions.load_script(project, state, "script.txt")
            actions.load_transcript(project, state, "transcript.json")

            actions.merge(None, state, "transcript", [0, 1])
            self.assertEqual(state["groupings"]["transcript"], [{"word_start": 0, "word_end": 3}])
            actions.merge(None, state, "script", [0, 1])
            self.assertEqual(mapping.group_rows(state, "script")[0]["text"], "One two. Three four.")
            actions.split(None, state, "transcript", 1)
            actions.split(None, state, "script", 1)
            state["selected"]["script"] = 1
            actions.split(None, state, "script", 2)
            state["selected"]["script"] = 0
            self.assertEqual([row["text"] for row in mapping.group_rows(state, "transcript")],
                             ["One", "two. Three four."])
            actions.link(None, state)
            state["selected"]["script"] = 1
            state["selected"]["transcript"] = 1
            actions.link(None, state)
            self.assertEqual(state["links"], [
                {"script": {"word_start": 0, "word_end": 0},
                 "transcript": {"word_start": 0, "word_end": 0}, "classification": "exact"},
                {"script": {"word_start": 1, "word_end": 1},
                 "transcript": {"word_start": 1, "word_end": 3}, "classification": "partial"},
            ])
            with self.assertRaisesRegex(ValueError, "Unlink"):
                actions.merge(None, state, "transcript", [0, 1])
            with self.assertRaisesRegex(ValueError, "Unlink"):
                actions.split(None, state, "script", 3)
            self.assertEqual(mapping.coverage(state), {(0, 0): "exact", (1, 1): "partial", (2, 3): "missing"})
            saved = mapping.save_version(root, state, "review")
            reopened = mapping.read_mapping(root, saved)
            self.assertEqual(reopened["groupings"], state["groupings"])
            self.assertEqual(reopened["links"], state["links"])
            self.assertEqual(script_path.read_bytes(), original_script)
            self.assertEqual(transcript_path.read_bytes(), original_transcript)

    def test_discarded_groups_regroup_without_losing_review_state(self):
        state = review_state()
        actions.discard(None, state)
        actions.split(None, state, "transcript", 1)
        self.assertEqual(state["discarded"], [
            {"word_start": 0, "word_end": 0}, {"word_start": 1, "word_end": 1},
        ])
        actions.merge(None, state, "transcript", [0, 1])
        self.assertEqual(state["discarded"], [{"word_start": 0, "word_end": 1}])
        with self.assertRaisesRegex(ValueError, "Restore"):
            actions.merge(None, state, "transcript", [0, 1])

    def test_document_rejects_gap_and_non_group_link_range(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "script.txt").write_text("One two.", encoding="utf-8")
            (root / "transcript.json").write_text(json.dumps({"words": [
                {"text": "One", "start_ms": 0, "end_ms": 100},
                {"text": "two.", "start_ms": 100, "end_ms": 200},
            ]}), encoding="utf-8")
            state = mapping.new_state()
            state["script"] = sources.load_script(root, "script.txt")
            state["transcript"] = sources.load_transcript(root, "transcript.json")
            state["groupings"] = {panel: mapping.initial_groupings(state[panel]) for panel in mapping.PANELS}
            state["selected"] = {"script": 0, "transcript": 0}
            actions.link(None, state)
            path = mapping.save_version(root, state, "review")
            document = json.loads((root / path).read_text())
            changed = deepcopy(document)
            changed["groupings"]["transcript"] = [{"word_start": 1, "word_end": 1}]
            with self.assertRaisesRegex(ValueError, "cover source words"):
                mapping.validate_document(root, changed)
            changed = deepcopy(document)
            changed["links"][0]["transcript"]["word_end"] = 0
            with self.assertRaisesRegex(ValueError, "match word groupings"):
                mapping.validate_document(root, changed)


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

    def test_visuals_preserve_repeated_take_quality_and_review_states(self):
        for index in range(3):
            self.widget.state["selected"]["transcript"] = index
            actions.link(None, self.widget.state)
        self.widget._refresh()
        self.assertEqual(self.widget.bands.links, [(0, 0, "exact"), (0, 1, "flub"), (0, 2, "partial")])
        self.assertEqual(self.widget._panels["script"]["overview"].statuses, ["exact", "missing"])
        self.assertEqual(self.widget._panels["transcript"]["overview"].statuses, ["exact", "flub", "partial"])
        actions.discard(None, self.widget.state)
        self.widget._refresh()
        self.assertEqual(len(self.widget.bands.links), 2)
        self.assertEqual(self.widget._panels["transcript"]["overview"].statuses[-1], "discarded")
        actions.restore(None, self.widget.state)
        self.widget._refresh()
        self.assertEqual(self.widget._panels["transcript"]["overview"].statuses[-1], "unreviewed")

    def test_overview_navigation_uses_real_row_heights_and_shared_action(self):
        table = self.widget._panels["transcript"]["table"]
        strip = self.widget._panels["transcript"]["overview"]
        table._resize_timer.stop()
        for row, height in enumerate((45, 170, 80)):
            table.setRowHeight(row, height)
        track = strip.track()
        y = track.top() + track.height() * 130 / 295
        self.assertEqual(strip.row_at(y), 1)
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPoint(8, round(y)))
        self.assertEqual(self.widget.state["selected"]["transcript"], 1)
        self.assertEqual(table.currentRow(), 1)

    def test_band_geometry_follows_scrolling_resizing_and_selection(self):
        state = self.widget.state
        state["selected"]["script"] = 1
        actions.link(None, state)
        state["selected"]["script"] = 0
        state["selected"]["transcript"] = 2
        actions.link(None, state)
        self.widget._refresh()
        bands = self.widget.bands
        self.assertEqual(bands.links, [(1, 0, "flub"), (0, 2, "partial")])
        bands.focus_panel = 0
        self.assertFalse(bands.emphasized(bands.links[0]))
        self.assertTrue(bands.emphasized(bands.links[1]))
        table = self.widget._panels["transcript"]["table"]
        table._resize_timer.stop()
        table.setRowHeight(0, 80)
        top, bottom, offscreen = bands.endpoint(1, 0)
        self.assertEqual(bottom - top, 80)
        self.assertEqual(bands.mapToGlobal(QPoint(0, round(top))).y(),
                         table.viewport().mapToGlobal(QPoint(0, 0)).y())
        strip = self.widget._panels["transcript"]["overview"]
        self.assertEqual(strip.mapToGlobal(QPoint(0, round(strip.track().top()))).y(),
                         table.viewport().mapToGlobal(QPoint(0, 0)).y())
        table.setRowHeight(0, 1200)
        self.app.processEvents()
        table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertEqual(bands.endpoint(1, 2)[2], 0)
        self.widget.resize(1000, 700)
        QTest.qWait(150)
        self.assertFalse(self.widget.grab().isNull())

    def test_link_toggle_and_unlink_with_different_or_closed_script(self):
        self.assertFalse(hasattr(self.widget, "advance_script_after_link"))
        self.widget.link_button.click()
        self.assertEqual(self.widget.state["links"][0]["classification"], "exact")
        self.assertEqual(self.widget.state["selected"], {"script": 1, "transcript": 1})
        self.assertEqual(self.logging.call_args.args[2], "Linked script group 1 to transcript group 1 as exact")
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
        self.assertEqual(self.widget.state["discarded"], [{"word_start": 0, "word_end": 1}])
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
        self.assertEqual(self.widget.state["discarded"], [{"word_start": 2, "word_end": 2}])
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

    def test_split_picker_and_merge_context_menu(self):
        table = self.widget._panels["transcript"]["table"]
        with patch("studio.tools.line.ui.QMessageBox.warning", side_effect=RuntimeError("Unexpected dialog")):
            self.widget._begin_split("transcript", 0)
            picker = table.cellWidget(0, 1)
            self.assertIsNotNone(picker)
            self.assertIn("Hello world.", picker.toPlainText())
            QTest.keyClick(picker, Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertEqual(self.widget.state["groupings"]["transcript"][:2], [
                {"word_start": 0, "word_end": 0}, {"word_start": 1, "word_end": 1},
            ])
            self.assertIsNone(self.widget._split_mode)
            table.selectionModel().select(table.model().index(1, 0),
                                          QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            self.app.processEvents()
            self.assertEqual(self.widget._selected_indexes("transcript"), [0, 1])
            with patch("studio.tools.line.ui.QMenu") as menu_class:
                menu_class.return_value.isEmpty.return_value = False
                point = table.visualRect(table.model().index(0, 1)).center()
                self.widget._group_menu("transcript", point)
                menu_class.return_value.addAction.assert_called_once_with("Merge groups")
                menu_class.return_value.addAction.return_value.setEnabled.assert_called_once_with(True)
            self.widget._merge_groups("transcript", [0, 1])
            self.assertEqual(self.widget.state["groupings"]["transcript"][0], {"word_start": 0, "word_end": 1})

    def test_long_split_group_expands_to_show_wrapped_words(self):
        words = ["many"] * 70
        transcript = self.widget.state["transcript"]
        transcript["words"] = [
            {"index": i, "text": word, "start_ms": i * 100, "end_ms": (i + 1) * 100}
            for i, word in enumerate(words)
        ]
        transcript["rows"] = [{"id": "t0", "line": 1, "text": " ".join(words),
                               "word_start": 0, "word_end": len(words) - 1,
                               "start_ms": 0, "end_ms": 7000}]
        self.widget.state["groupings"]["transcript"] = mapping.initial_groupings(transcript)
        self.widget._refresh()
        self.widget._begin_split("transcript", 0)
        self.app.processEvents()
        self.app.processEvents()
        table = self.widget._panels["transcript"]["table"]
        self.assertGreater(table.rowHeight(0), 42)


class MultiTranscriptUiTests(QtTests):
    setUp = ReviewUiTests.setUp
    tearDown = ReviewUiTests.tearDown

    def add_second(self):
        second = deepcopy(self.widget.state["transcript"])
        second["source"]["path"] = "Second.json"
        with patch("studio.tools.line.actions.sources.load_transcript", return_value=second):
            self.widget._dispatch("line.add_transcripts", paths=["Second.json"])
        self.app.processEvents()

    def test_headers_collapse_without_losing_coverage_and_readings_expand(self):
        self.add_second()
        state = self.widget.state
        state["selected"] = {"script": 1, "transcript": 3}
        actions.link(None, state)
        self.widget._refresh()
        table = self.widget._panels["transcript"]["table"]
        self.assertEqual(table.rowCount(), 8)
        self.assertEqual(self.widget._table_rows["transcript"], {0: 1, 1: 2, 2: 3, 3: 5, 4: 6, 5: 7})
        self.assertEqual(self.widget.bands.links, [(1, 5, "flub")])
        before = mapping.coverage(state)
        self.widget._headers["Second.json"][1].click()
        self.app.processEvents()
        self.assertTrue(table.isRowHidden(5))
        self.assertFalse(table.isRowHidden(4))
        self.assertTrue(self.widget._headers["Second.json"][1].isVisible())
        self.assertIsNone(state["selected"]["transcript"])
        self.assertEqual(mapping.coverage(state), before)
        self.assertEqual(len(state["links"]), 1)
        self.assertIn("Second.json", self.widget.readings.item(0).text())
        self.widget._reading_clicked(self.widget.readings.item(0))
        self.assertFalse(table.isRowHidden(5))
        self.assertEqual(state["selected"]["transcript"], 3)
        self.assertEqual(table.currentRow(), 5)

    def test_overview_and_keyboard_navigation_handle_collapsed_sections(self):
        self.add_second()
        self.widget._toggle_recording("Transcript.json")
        self.widget.navigate("transcript", 1)
        self.assertEqual(self.widget.state["selected"]["transcript"], 3)
        self.assertIn("Transcript.json", self.widget._collapsed)
        strip = self.widget._panels["transcript"]["overview"]
        self.assertEqual(len(strip.statuses), 6)
        self.assertGreater(strip.boundaries()[3], 0)
        self.widget._overview_selected("transcript", 1)
        self.assertEqual(self.widget.state["selected"]["transcript"], 0)
        self.assertNotIn("Transcript.json", self.widget._collapsed)

    def test_regrouping_and_row_selection_use_group_indexes_after_headers(self):
        self.add_second()
        table = self.widget._panels["transcript"]["table"]
        table.setCurrentCell(7, 1)
        self.assertEqual(self.widget.state["selected"]["transcript"], 5)
        self.widget._begin_split("transcript", 5)
        self.assertIsNotNone(table.cellWidget(7, 1))
        self.widget._split_at("transcript", 11)
        self.assertEqual(len(self.widget.state["groupings"]["transcript"]), 7)
        self.assertEqual(self.widget._selected_indexes("transcript"), [5, 6])
        self.assertIn("different transcript files", actions.grouping_edit_error(self.widget.state, "transcript", [2, 3]))
        self.widget._merge_groups("transcript", [5, 6])
        self.assertEqual(len(self.widget.state["groupings"]["transcript"]), 6)

    def test_reloading_same_project_resets_collapsed_geometry(self):
        self.add_second()
        state = deepcopy(self.widget.state)
        state["dirty"] = False
        self.widget._toggle_recording("Second.json")
        with patch("studio.tools.line.mapping.read_mapping", return_value=state):
            self.widget.state["dirty"] = False
            self.widget._dispatch("line.load_mapping", path="review.json")
        self.assertFalse(self.widget._collapsed)
        self.assertFalse(self.widget._panels["transcript"]["table"].isRowHidden(5))


class MultiTranscriptFileTests(unittest.TestCase):
    def test_save_reopen_find_takes_import_and_source_verification(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "script.txt").write_text("Hello world. Second sentence.", encoding="utf-8")
            for name in ("C01.json", "C02.json"):
                (root / name).write_text(json.dumps({"words": [
                    {"text": "Hello", "start_ms": 0, "end_ms": 100},
                    {"text": "world.", "start_ms": 100, "end_ms": 200},
                ]}), encoding="utf-8")
            project = SimpleNamespace(root=root)
            state = mapping.new_state()
            actions.load_script(project, state, "script.txt")
            actions.add_transcripts(project, state, ["C01.json", "C02.json"])
            state["selected"]["transcript"] = 1
            actions.link(project, state)
            path = mapping.save_version(root, state, "Combined")
            document = json.loads((root / path).read_text())
            self.assertEqual(document["schema_version"], 3)
            self.assertEqual(document["recordings"][1]["links"][0]["transcript"], {"word_start": 0, "word_end": 1})
            reopened = mapping.read_mapping(root, path)
            for key in ("groupings", "links", "discarded"):
                self.assertEqual(reopened[key], state[key])
            self.assertEqual(mapping.group_rows(reopened, "transcript")[1]["start_ms"], 0)
            # Re-adding a file is harmless, including after closing the panel.
            actions.close_transcript(project, reopened)
            actions.add_transcripts(project, reopened, ["C02.json"])
            self.assertEqual(len(combined.recordings(reopened["transcript"])), 2)
            takes = dict(document, schema="studio.find-takes", schema_version=1)
            (root / "Takes.json").write_text(json.dumps(takes), encoding="utf-8")
            imported = mapping.read_mapping(root, "Takes.json")
            self.assertEqual(imported["links"], state["links"])
            (root / "C02.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input has changed"):
                mapping.read_mapping(root, path)

    def test_addition_is_atomic_and_cross_file_merge_is_rejected(self):
        state = review_state()
        original = deepcopy(state)
        with patch("studio.tools.line.actions.sources.load_transcript", side_effect=[state["transcript"], ValueError("bad file")]):
            with self.assertRaisesRegex(ValueError, "bad file"):
                actions.add_transcripts(SimpleNamespace(root=Path.cwd()), state, ["one.json", "bad.json"])
        self.assertEqual(state, original)


if __name__ == "__main__":
    unittest.main()
