"""Resolve transcript audio and play one bounded range using Qt Multimedia."""

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from . import combined
from .sources import _audio_reference, project_path


def audio_clip(root, source, group):
    """Return a recording-local path and times, including for combined reviews."""
    match = combined.recording_for(source, group)
    if match is None:
        raise ValueError("Select a group within one recording.")
    _, recording, offset, _ = match
    words = recording["words"]
    start = words[group["word_start"] - offset].get("start_ms")
    end = words[group["word_end"] - offset].get("end_ms")
    if start is None or end is None or end <= start:
        raise ValueError("This line has no usable start/end timestamps.")
    root = Path(root).resolve()
    audio = recording.get("audio")
    if not audio:
        transcript = project_path(root, recording["source"]["path"])
        audio = _audio_reference(root, transcript, {})
    if not audio:
        raise ValueError("No associated audio found. Add source_audio to the transcript or a matching WAV to Footage Intake.")
    return project_path(root, audio), start, end


class LinePlayback(QObject):
    changed = Signal(bool)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.active = False
        self._pending = False
        self._start = self._end = 0
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._check_end)
        self.player.positionChanged.connect(self._check_end)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.errorOccurred.connect(self._error)

    def play(self, path, start, end):
        self.stop()
        self._start, self._end = start, end
        self.active = self._pending = True
        self.changed.emit(True)
        if QMediaDevices.defaultAudioOutput().isNull():
            self._error(None, "No audio output device is available. Connect or select a speaker or headphones.")
            return
        url = QUrl.fromLocalFile(str(path))
        if self.player.source() != url:
            self.player.setSource(url)
        self._start_if_ready()

    def _start_if_ready(self):
        if not self._pending or self.player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        ):
            return
        duration = self.player.duration()
        if duration and self._start >= duration:
            self._error(None, "The line starts beyond the end of its audio file.")
            return
        if not self.player.isSeekable():
            self._error(None, "This audio file does not support seeking to a line.")
            return
        self._pending = False
        self.player.setPosition(self._start)
        self.player.play()
        self.timer.start()

    def _status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._pending:
            self.stop()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia and self.active:
            self._error(None, self.player.errorString() or "Cannot open this audio file.")
        else:
            self._start_if_ready()

    def _check_end(self, *_):
        if self.active and not self._pending and self.player.position() >= self._end:
            self.stop()

    def _error(self, _code, message):
        if self.active:
            self.stop()
            self.failed.emit(message or "Cannot play this audio file.")

    def stop(self):
        was_active = self.active
        self.active = self._pending = False
        self.timer.stop()
        self.player.stop()
        if was_active:
            self.changed.emit(False)
