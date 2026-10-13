"""Playback ranges, recording resolution, and native media lifecycle checks."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from studio.tools.line import combined
from studio.tools.line.playback import audio_clip, LinePlayback
import test_line_review as review_tests
review_state = review_tests.review_state


class ClipTests(unittest.TestCase):
    def test_combined_recordings_use_local_timestamps_and_audio(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = review_state()["transcript"]
            second = deepcopy(first)
            for index, recording in enumerate((first, second)):
                recording["audio"] = f"{index}.wav"
                (root / recording["audio"]).touch()
            source = combined.combine([first, second])
            self.assertEqual(audio_clip(root, source, {"word_start": 9, "word_end": 9}),
                             (root / "1.wav", 2000, 3000))
            with self.assertRaises(ValueError):
                audio_clip(root, source, {"word_start": 6, "word_end": 7})
            second["words"][2]["start_ms"] = None
            with self.assertRaisesRegex(ValueError, "timestamps"):
                audio_clip(root, second, {"word_start": 2, "word_end": 2})
            with self.assertRaises((ValueError, OSError)):
                audio_clip(root, dict(first, audio="../outside.wav"), {"word_start": 0, "word_end": 1})

    def test_legacy_audio_fallback_and_missing_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = review_state()["transcript"]
            (root / "Transcript.json").touch()
            intake = root / "2-Production/1-Footage Intake"
            intake.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "No associated audio"):
                audio_clip(root, source, {"word_start": 0, "word_end": 1})
            wav = intake / "Transcript.wav"
            wav.touch()
            self.assertEqual(audio_clip(root, source, {"word_start": 0, "word_end": 1}), (wav, 0, 2000))


class PlaybackUiTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: setattr(cls, "app", QApplication.instance() or QApplication([])))
    setUp = review_tests.ReviewUiTests.setUp
    tearDown = review_tests.ReviewUiTests.tearDown
    def test_play_stop_selection_and_hide(self):
        with patch("studio.tools.line.ui.audio_clip", return_value=(Path("/test.wav"), 1000, 2000)):
            self.widget._refresh()
            self.assertTrue(self.widget.play_button.isEnabled())
            with patch("studio.tools.line.ui.LinePlayback") as factory:
                player = factory.return_value
                player.active = False
                self.widget.play_selected()
                player.play.assert_called_once_with(Path("/test.wav"), 1000, 2000)
                player.active = True
                self.widget.play_selected()
                self.assertEqual(player.stop.call_count, 1)
                with patch("studio.tools.line.ui.audio_clip", return_value=(Path("/other.wav"), 0, 4000)):
                    self.widget._refresh()
                self.assertEqual(player.stop.call_count, 2)
                self.widget.hide()
                self.assertEqual(player.stop.call_count, 3)


class PlaybackLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_load_seek_auto_stop_replay_cancel_and_error(self):
        with patch("studio.tools.line.playback.QMediaPlayer") as factory, patch("studio.tools.line.playback.QAudioOutput"), patch("studio.tools.line.playback.QMediaDevices") as devices:
            devices.defaultAudioOutput.return_value.isNull.return_value = False
            # Keep real enum values while replacing the device-dependent backend.
            factory.MediaStatus = QMediaPlayer.MediaStatus
            backend = factory.return_value
            backend.source.return_value = QUrl()
            backend.mediaStatus.return_value = QMediaPlayer.MediaStatus.LoadingMedia
            backend.duration.return_value = 5000
            backend.isSeekable.return_value = True
            player = LinePlayback()
            errors = []
            changes = []
            player.failed.connect(errors.append)
            player.changed.connect(changes.append)
            player.play(Path("/one.wav"), 700, 1000)
            backend.play.assert_not_called()
            backend.mediaStatus.return_value = QMediaPlayer.MediaStatus.LoadedMedia
            player._status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
            backend.setPosition.assert_called_with(700)
            backend.play.assert_called_once()
            backend.position.return_value = 999
            player._check_end()
            self.assertTrue(player.active)
            backend.position.return_value = 1000
            player._check_end()
            self.assertFalse(player.active)
            self.assertEqual(changes, [True, False])
            player.play(Path("/one.wav"), 1200, 1500)
            backend.setPosition.assert_called_with(1200)
            player.stop()
            backend.mediaStatus.return_value = QMediaPlayer.MediaStatus.LoadingMedia
            player.play(Path("/two.wav"), 0, 1000)
            player.stop()
            count = backend.play.call_count
            backend.mediaStatus.return_value = QMediaPlayer.MediaStatus.LoadedMedia
            player._status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
            self.assertEqual(count, backend.play.call_count)
            player.play(Path("/one.wav"), 6000, 7000)
            self.assertIn("beyond", errors[-1])
            player.play(Path("/one.wav"), 0, 1000)
            player._error(None, "Unreadable audio")
            self.assertFalse(player.active)
            self.assertEqual(errors[-1], "Unreadable audio")
            devices.defaultAudioOutput.return_value.isNull.return_value = True
            player.play(Path("/one.wav"), 0, 1000)
            self.assertFalse(player.active)
            self.assertIn("No audio output", errors[-1])
            player.deleteLater()
