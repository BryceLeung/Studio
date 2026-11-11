"""Line's repair handoff must never silently replace the current review."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMessageBox
from studio.tools.line.repair_ui import _RepairProgress

import test_line_review as review_tests


class RepairUiTests(review_tests.QtTests):
    setUp = review_tests.ReviewUiTests.setUp
    tearDown = review_tests.ReviewUiTests.tearDown

    def test_no_change_does_not_reload(self):
        with patch("studio.tools.line.repair_ui.QMessageBox.information") as information, \
                patch.object(self.widget, "_dispatch") as dispatch:
            self.widget._transcription_repair.show_result({
                "status": "unchanged", "summary": "No significant change", "report_path": "report.json",
            })
        information.assert_called_once()
        dispatch.assert_not_called()

    def test_changed_result_requires_explicit_load(self):
        repair = self.widget._transcription_repair
        result = {"status": "changed", "summary": "Timings moved", "mapping_path": "new.v2.json"}
        for choose_load in (False, True):
            with self.subTest(choose_load=choose_load), \
                    patch("studio.tools.line.repair_ui.QMessageBox") as factory, \
                    patch.object(self.widget, "_dispatch") as dispatch:
                box = factory.return_value
                load, keep = object(), object()
                box.addButton.side_effect = [load, keep]
                box.clickedButton.return_value = load if choose_load else keep
                repair.show_result(result)
                box.setDefaultButton.assert_called_once_with(keep)
                if choose_load:
                    dispatch.assert_called_once_with("line.load_mapping", path="new.v2.json")
                else:
                    dispatch.assert_not_called()

    def test_missing_environment_does_not_save_or_start(self):
        with patch("studio.tools.line.repair_ui.Path.is_file", return_value=False), \
                patch.object(self.widget, "save_mapping") as save:
            with self.assertRaisesRegex(ValueError, "environment is missing"):
                self.widget._transcription_repair.start()
        save.assert_not_called()

    def test_save_cancel_does_not_launch(self):
        self.widget.state["dirty"] = True
        with patch("studio.tools.line.repair_ui.Path.is_file", return_value=True), \
                patch("studio.tools.line.repair_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Ok), \
                patch("studio.tools.line.repair_ui.QProcess") as process, \
                patch.object(self.widget, "save_mapping", return_value=False):
            self.widget._transcription_repair.start()
        process.assert_not_called()

    def test_worker_uses_saved_mapping_and_selected_display_group(self):
        self.widget.state["dirty"] = True
        def save():
            self.widget.state["mapping_path"] = "review.v2.json"
            return True
        with patch("studio.tools.line.repair_ui.Path.is_file", return_value=True), \
                patch("studio.tools.line.repair_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Ok), \
                patch("studio.tools.line.repair_ui.QProcess") as factory, \
                patch.object(self.widget, "save_mapping", side_effect=save):
            factory.ProcessChannelMode = QProcess.ProcessChannelMode
            self.widget._transcription_repair.start()
            try:
                program, args = factory.return_value.start.call_args.args
                self.assertIn("2-Transcribe/.venv-tools", program.replace("\\", "/"))
                self.assertIn("review.v2.json", args)
                self.assertEqual(args[args.index("--group") + 1], "1")
                self.assertEqual(args[args.index("--project-root") + 1], str(self.widget.project.root))
                self.assertIn("--result", args)
                self.assertTrue(self.widget._transcription_repair.busy)
                repair = self.widget._transcription_repair
                self.assertEqual((repair.activity_bar.minimum(), repair.activity_bar.maximum()), (0, 0))
                self.assertTrue(repair._elapsed_timer.isActive())
                self.assertIn("Elapsed 00:00", repair.elapsed_label.text())
            finally:
                self.widget._transcription_repair._cleanup()

    def test_failed_worker_keeps_current_review(self):
        repair = self.widget._transcription_repair
        repair.process = Mock()
        repair.process.readAllStandardOutput.return_value = b"Recognition failed"
        repair.log = Mock()
        with patch("studio.tools.line.repair_ui.QMessageBox.warning") as warning, \
                patch.object(self.widget, "_dispatch") as dispatch:
            repair._finished(1, QProcess.ExitStatus.NormalExit, Path("missing.json"))
        warning.assert_called_once()
        dispatch.assert_not_called()
        self.assertFalse(repair.busy)

    def test_process_result_file_is_read_before_cleanup(self):
        repair = self.widget._transcription_repair
        repair.temporary = TemporaryDirectory()
        result = Path(repair.temporary.name) / "result.json"
        result.write_text(json.dumps({"status": "unchanged", "summary": "No change"}), encoding="utf-8")
        repair.process = Mock()
        repair.process.readAllStandardOutput.return_value = b""
        repair.log = Mock()
        with patch.object(repair, "show_result") as show:
            repair._finished(0, QProcess.ExitStatus.NormalExit, result)
        show.assert_called_once_with({"status": "unchanged", "summary": "No change"})
        self.assertFalse(result.exists())

    def test_project_close_is_refused_while_worker_active(self):
        repair = self.widget._transcription_repair
        repair.process = Mock()
        try:
            with patch("studio.tools.line.ui.QMessageBox.information"):
                self.assertFalse(self.widget.confirm_close())
        finally:
            repair._cleanup()

    def test_cancel_terminates_then_escalates_and_restores_current_review(self):
        repair = self.widget._transcription_repair
        process = Mock()
        repair.process = process
        process.readAllStandardOutput.return_value = b""
        repair.status_label = Mock()
        repair.cancel_button = Mock()
        repair.log = Mock()
        repair.cancel()
        repair.cancel()
        process.terminate.assert_called_once()
        repair.cancel_button.setEnabled.assert_called_once_with(False)
        self.assertTrue(repair._kill_timer.isActive())
        repair._kill_cancelled_worker()
        process.kill.assert_called_once()
        with patch("studio.tools.line.repair_ui.QMessageBox.information") as information, \
                patch("studio.tools.line.repair_ui.QMessageBox.warning") as warning, \
                patch.object(self.widget, "_dispatch") as dispatch:
            repair._finished(-1, QProcess.ExitStatus.CrashExit, Path("missing.json"))
        information.assert_called_once()
        warning.assert_not_called()
        dispatch.assert_not_called()
        self.assertFalse(repair.busy)
        self.assertFalse(repair._kill_timer.isActive())

    def test_success_winning_cancel_race_still_offers_generated_version(self):
        repair = self.widget._transcription_repair
        repair.cancelled = True
        repair.process = Mock()
        repair.process.readAllStandardOutput.return_value = b""
        repair.log = Mock()
        with TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result = {"status": "changed", "mapping_path": "new.v2.json"}
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with patch.object(repair, "show_result") as show:
                repair._finished(0, QProcess.ExitStatus.NormalExit, result_path)
        show.assert_called_once_with(result)

    def test_escape_and_window_close_request_cancellation(self):
        dialog = _RepairProgress(self.widget)
        cancel = Mock()
        dialog.cancel_requested.connect(cancel)
        dialog.reject()
        event = Mock()
        dialog.closeEvent(event)
        self.assertEqual(cancel.call_count, 2)
        event.ignore.assert_called_once()
        dialog.deleteLater()

    def test_elapsed_and_quiet_time_do_not_invent_percentage_progress(self):
        repair = self.widget._transcription_repair
        repair._started_at = 100.0
        repair._last_update = 160.0
        repair.elapsed_label = Mock()
        with patch("studio.tools.line.repair_ui.monotonic", return_value=225.0):
            repair._refresh_elapsed()
        text = repair.elapsed_label.setText.call_args.args[0]
        self.assertIn("Elapsed 02:05", text)
        self.assertIn("Last worker update 65s ago", text)
        self.assertIn("does not guarantee worker progress", text)
        repair._cleanup()
        self.assertFalse(repair._elapsed_timer.isActive())

    def test_split_worker_output_updates_phase_and_elapsed(self):
        repair = self.widget._transcription_repair
        repair.process = Mock()
        repair.log = Mock()
        repair.status_label = Mock()
        repair.phase_label = Mock()
        repair.process.readAllStandardOutput.side_effect = [
            b"Loading large-", b"v3 recognition model\n", b"Comparing repaired transcript\n",
        ]
        try:
            repair._read_output()
            repair.phase_label.setText.assert_not_called()
            repair._read_output()
            self.assertIn("Phase 3/9", repair.phase_label.setText.call_args.args[0])
            repair._read_output()
            self.assertIn("Phase 7/9", repair.phase_label.setText.call_args.args[0])
            repair.cancelled = True
            repair.process.readAllStandardOutput.side_effect = None
            repair.process.readAllStandardOutput.return_value = b"Generating fresh Takes\n"
            repair._read_output()
            self.assertIn("Phase 7/9", repair.phase_label.setText.call_args.args[0])
        finally:
            repair._cleanup()


if __name__ == "__main__":
    unittest.main()
