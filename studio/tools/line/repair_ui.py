"""Run isolated transcription repair without blocking Line's event loop."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout


_PHASES = (
    ("Repairing display groups", "Preparing audio window"),
    ("Loading lav audio", "Decoding audio"),
    ("Loading large-v3", "Loading/downloading recognition model"),
    ("Recognizing padded", "Transcribing audio"),
    ("Loading word-alignment", "Loading/downloading alignment model"),
    ("Aligning recognized", "Aligning words to audio"),
    ("Comparing repaired", "Comparing transcript and checking boundaries"),
    ("Generating fresh Takes", "Generating fresh Takes"),
    ("Saving repair", "Saving repair outputs"),
)


class _RepairProgress(QDialog):
    cancel_requested = Signal()

    def reject(self):
        self.cancel_requested.emit()

    def closeEvent(self, event):
        event.ignore()
        self.cancel_requested.emit()


class TranscriptionRepair(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.widget = widget
        self.process = None
        self.dialog = None
        self.temporary = None
        self.output = ""
        self.cancelled = False
        self._started_at = None
        self._last_update = None
        self._progress_buffer = ""
        self.phase_label = None
        self.elapsed_label = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill_cancelled_worker)

    @property
    def busy(self):
        return self.process is not None

    def start(self):
        widget = self.widget
        indexes = widget._selected_indexes("transcript")
        if self.busy or widget._split_mode or len(indexes) != 1:
            return
        if not widget.state.get("script") or not widget.state.get("transcript"):
            raise ValueError("Load a script and transcript before repairing transcription.")
        if widget.lock is not None:
            widget.lock.assert_owned()
        workflow = Path(__file__).resolve().parents[3]
        stage = workflow / ".agents/skills/studio/3-Post Production/2-Transcribe"
        interpreter = stage / ".venv-tools" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not interpreter.is_file():
            raise ValueError(
                f"The transcription environment is missing: {interpreter}\n"
                "Set up stage 3.2 using its install_dependencies.py helper first."
            )
        choice = QMessageBox.question(
            widget, "Repair transcription",
            f"Retranscribe group {indexes[0] + 1} and its immediate neighbors?\n\n"
            "Your current review will be saved first. A significant change creates a new "
            "transcript and a fresh Takes version without your manual mapping edits. "
            "You can choose whether to load it.\n\n"
            "Uses WhisperX large-v3 on CPU; uncached models may be downloaded.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Ok:
            return
        if widget.has_unsaved_changes() or not widget.state.get("mapping_path"):
            if not widget.save_mapping():
                return
        if widget._playback:
            widget._playback.stop()
        self.temporary = TemporaryDirectory(prefix="studio-repair-")
        result_path = Path(self.temporary.name) / "result.json"
        self.output = ""
        self.cancelled = False
        self._progress_buffer = ""
        self._started_at = self._last_update = monotonic()
        self.dialog = _RepairProgress(widget)
        self.dialog.setWindowTitle("Repairing transcription")
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.cancel_requested.connect(self.cancel)
        layout = QVBoxLayout(self.dialog)
        self.status_label = QLabel("Starting repair. Model loading or downloading may take several minutes.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.phase_label = QLabel("Starting worker")
        self.phase_label.setWordWrap(True)
        layout.addWidget(self.phase_label)
        self.activity_bar = QProgressBar()
        self.activity_bar.setRange(0, 0)
        self.activity_bar.setTextVisible(False)
        self.activity_bar.setToolTip("Activity indicator, not a percentage. Some model operations do not report measurable progress.")
        layout.addWidget(self.activity_bar)
        self.elapsed_label = QLabel()
        self.elapsed_label.setWordWrap(True)
        layout.addWidget(self.elapsed_label)
        self._refresh_elapsed()
        self._elapsed_timer.start()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(100)
        layout.addWidget(self.log)
        self.cancel_button = QPushButton("Cancel Repair")
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self.cancel_button)
        self.dialog.resize(660, 280)
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(workflow))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(lambda code, status: self._finished(code, status, result_path))
        process.start(str(interpreter), [
            "-u", str(workflow / "scripts/repair_transcription.py"),
            str(widget.state["mapping_path"]), "--group", str(indexes[0] + 1),
            "--project-root", str(widget.project.root), "--result", str(result_path),
        ])
        self.dialog.open()

    def _refresh_elapsed(self):
        if self._started_at is None or self.elapsed_label is None:
            return
        now = monotonic()
        elapsed = max(0, int(now - self._started_at))
        quiet = max(0, int(now - self._last_update))
        self.elapsed_label.setText(
            f"Elapsed {elapsed // 60:02d}:{elapsed % 60:02d} | Last worker update {quiet}s ago\n"
            "The activity indicator does not guarantee worker progress; Cancel Repair remains available."
        )

    def cancel(self):
        if not self.busy or self.cancelled:
            return
        self.cancelled = True
        self.status_label.setText("Cancelling repair. Your current review will stay open.")
        self.cancel_button.setEnabled(False)
        self.process.terminate()
        # Native model loading may not return to Python's signal handler promptly.
        self._kill_timer.start(5000)

    def _kill_cancelled_worker(self):
        if self.cancelled and self.process is not None:
            self.process.kill()

    def _read_output(self):
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not text:
            return
        self._last_update = monotonic()
        self.output = (self.output + text)[-16000:]
        self.log.appendPlainText(text.rstrip())
        lines = (self._progress_buffer + text.replace("\r", "\n")).split("\n")
        self._progress_buffer = lines.pop()[-8000:]
        for line in lines:
            if self.cancelled:
                break
            for number, (prefix, title) in enumerate(_PHASES, 1):
                if line.startswith(prefix):
                    self.status_label.setText(line)
                    if self.phase_label is not None:
                        self.phase_label.setText(f"Phase {number}/{len(_PHASES)}: {title}")
                    break
        self._refresh_elapsed()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            message = self.process.errorString()
            self._cleanup()
            QMessageBox.warning(self.widget, "Transcription repair failed", message)

    def _cleanup(self):
        self._kill_timer.stop()
        self._elapsed_timer.stop()
        self._started_at = None
        if self.dialog:
            self.dialog.accept()
            self.dialog.deleteLater()
            self.dialog = None
        if self.process:
            self.process.deleteLater()
            self.process = None
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None

    def _finished(self, code, status, result_path):
        self._read_output()
        cancelled = self.cancelled
        try:
            if code != 0 or status != QProcess.ExitStatus.NormalExit:
                raise ValueError(self.output or f"Repair process exited with code {code}.")
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._cleanup()
            if cancelled:
                QMessageBox.information(self.widget, "Transcription repair cancelled", "Your current review is still open. Existing transcript and Takes versions were not overwritten.")
            else:
                QMessageBox.warning(self.widget, "Transcription repair failed", str(error))
            return
        self._cleanup()
        self.show_result(result)

    def show_result(self, result):
        widget = self.widget
        summary = result.get("summary", "")
        details = ""
        if isinstance(summary, dict):
            details = (
                f"Previous transcript:\n{summary.get('old_text', '')}\n\n"
                f"New transcript:\n{summary.get('new_text', '')}\n\n"
                + json.dumps(summary, indent=2)
            )
            summary = (
                f"Words: {summary.get('old_word_count', '?')} → {summary.get('new_word_count', '?')}\n"
                f"Wording changed: {'yes' if summary.get('wording_changed') else 'no'}\n"
                f"Words with significant timing changes: {len(summary.get('timing_shifts', []))}"
            )
        elif not isinstance(summary, str):
            summary = str(summary)
        report = result.get("report_path", "")
        if result.get("status") != "changed":
            QMessageBox.information(widget, "Transcription repair", f"{summary}\n\nReport: {report}")
            return
        box = QMessageBox(widget)
        box.setWindowTitle("Transcription repair ready")
        box.setText(summary or "A significant transcription discrepancy was found.")
        if details:
            box.setDetailedText(details)
        box.setInformativeText(
            "A new transcript and fresh Takes version have been saved. "
            "Manual mapping edits remain in your previous version, not the new mapping.\n\n"
            "Keep Current leaves this review open; future stage runs use the newest saved Takes version.\n\n"
            f"Takes: {result['mapping_path']}\nReport: {report}"
        )
        load = box.addButton("Load New Version", QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton("Keep Current", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() == load:
            widget._run_ui(lambda: widget._dispatch("line.load_mapping", path=result["mapping_path"]))
