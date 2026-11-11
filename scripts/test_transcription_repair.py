"""Synthetic, headless repair tests. Never downloads or imports inference models."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.tools.line import combined, mapping, repair, sources  # noqa: E402
from studio.transcript_versions import resolve_transcripts  # noqa: E402


def transcript():
    sentences = ["Opening anchor here.", "First nearby sentence.", "Target spoken passage.",
                 "Last nearby sentence.", "Closing anchor there."]
    segments, index = [], 0
    for text in sentences:
        words = []
        for token in text.split():
            words.append({"word": token, "start": float(index), "end": index + 0.25, "score": 0.99})
            index += 1
        segments.append({"text": text, "start": words[0]["start"], "end": words[-1]["end"], "words": words})
    return {"language": "en", "segments": segments,
            "word_segments": [deepcopy(w) for s in segments for w in s["words"]]}


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.transcribed = self.root / "3-Post Production/2-Transcribe"
        self.takes = self.root / "3-Post Production/3-Find Takes"
        self.intake = self.root / "2-Production/1-Footage Intake"
        for directory in (self.transcribed, self.takes, self.intake):
            directory.mkdir(parents=True)
        self.original = transcript()
        self.path = self.transcribed / "Script__C01__lav.json"
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        (self.intake / "Script__C01__lav.wav").write_bytes(b"mock lav audio")
        self.script_path = self.root / "Script.txt"
        self.script_path.write_text(" ".join(s["text"] for s in self.original["segments"]), encoding="utf-8")
        self.state = mapping.new_state()
        self.state["script"] = sources.load_script(self.root, self.script_path)
        self.state["transcript"] = sources.load_transcript(self.root, self.path)
        self.state["groupings"] = {panel: mapping.initial_groupings(self.state[panel]) for panel in mapping.PANELS}
        self.mapping_path = self.takes / "Script__Takes.v1.json"
        self.mapping_path.write_text(json.dumps(mapping._document(self.state, "Script__Takes", 1)), encoding="utf-8")

    def mock_recognizer(self, document):
        def recognize(audio, start, end, **kwargs):
            self.assertEqual(audio, self.intake / "Script__C01__lav.wav")
            aligned = deepcopy(document)
            duration = max(segment["end"] for segment in aligned["segments"]) + 1.75
            stop = duration if end is None else min(end, duration)
            # Return only the requested padded window and use window-local timestamps.
            aligned["segments"] = [segment for segment in aligned["segments"]
                                   if segment["start"] >= start and segment["end"] <= stop]
            for segment in aligned["segments"]:
                for item in [segment, *segment.get("words", [])]:
                    for field in ("start", "end"):
                        if field in item:
                            item[field] -= start
            return {"raw_asr": {"segments": deepcopy(aligned["segments"])}, "alignment": aligned,
                    "window_start": start, "window_end": stop, "audio_duration": duration}
        return recognize

    def run_repair(self, document=None, **kwargs):
        return repair.repair_transcription(self.root, self.mapping_path, kwargs.pop("group", 3),
                                            recognizer=self.mock_recognizer(document or self.original), **kwargs)

    def test_restart_insertion_fresh_valid_mapping_and_immutable_originals(self):
        before_transcript, before_mapping = self.path.read_bytes(), self.mapping_path.read_bytes()
        changed = deepcopy(self.original)
        changed["segments"][2]["words"][0:0] = [
            {"word": "Wait.", "start": 5.4, "end": 5.5},
            {"word": "Restart.", "start": 5.6, "end": 5.7}]
        changed["segments"][2]["start"] = 5.4
        changed["segments"][2]["text"] = "Wait. Restart. Target spoken passage."
        result = self.run_repair(changed)
        self.assertEqual(result["status"], "changed")
        self.assertTrue(result["summary"]["wording_changed"])
        candidate = Path(result["transcript_path"])
        self.assertEqual(candidate, self.transcribed / "REPAIRS/Script__C01__lav/v1/Script__C01__lav.json")
        self.assertEqual(Path(result["mapping_path"]), self.takes / "Script__Takes.v2.json")
        loaded = mapping.read_mapping(self.root, result["mapping_path"])
        self.assertEqual(loaded["transcript"]["source"]["path"], candidate.relative_to(self.root).as_posix())
        native = json.loads(candidate.read_text())
        self.assertEqual(native["word_segments"], [w for s in native["segments"] for w in s["words"]])
        self.assertIn("Restart.", [w["word"] for w in native["word_segments"]])
        self.assertGreater(len(loaded["transcript"]["rows"]), len(self.state["transcript"]["rows"]))
        self.assertEqual(self.path.read_bytes(), before_transcript)
        self.assertEqual(self.mapping_path.read_bytes(), before_mapping)
        self.assertFalse(list(self.root.rglob("DONE*")))
        self.assertTrue((candidate.parent / "raw_asr.json").is_file())
        self.assertTrue((candidate.parent / "alignment_source_time.json").is_file())
        marker = json.loads((candidate.parent / "COMMITTED.json").read_text())
        self.assertEqual(marker["status"], "success")
        self.assertEqual(marker["transcript"], candidate.name)
        self.assertEqual(marker["source"]["path"], self.path.relative_to(self.root).as_posix())
        self.assertEqual(resolve_transcripts(self.root)[0]["effective"], candidate)

    def test_repairing_a_repaired_mapping_preserves_history_and_selection(self):
        changed = deepcopy(self.original)
        changed["segments"][2]["words"][0]["start"] += 0.5
        changed["segments"][2]["words"][0]["end"] += 0.5
        changed["segments"][2]["start"] += 0.5
        first = self.run_repair(changed)
        second_text = deepcopy(changed)
        second_text["segments"][2]["words"][0]["word"] = "Corrected"
        second_text["segments"][2]["text"] = "Corrected spoken passage."
        second = repair.repair_transcription(
            self.root, first["mapping_path"], 3,
            recognizer=self.mock_recognizer(second_text),
        )
        self.assertEqual(Path(second["mapping_path"]).name, "Script__Takes.v3.json")
        self.assertEqual(resolve_transcripts(self.root)[0]["effective"], Path(second["transcript_path"]))
        for saved in (self.mapping_path, first["mapping_path"], second["mapping_path"]):
            mapping.read_mapping(self.root, saved)

    def test_timing_only_at_threshold(self):
        changed = deepcopy(self.original)
        word = changed["segments"][2]["words"][0]
        word["start"] += 0.5
        word["end"] += 0.5
        changed["segments"][2]["start"] += 0.5
        result = self.run_repair(changed)
        self.assertEqual(result["status"], "changed")
        self.assertFalse(result["summary"]["wording_changed"])
        self.assertEqual(len(result["summary"]["timing_shifts"]), 1)

    def test_unchanged_diagnostics_only(self):
        result = self.run_repair()
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["transcript_path"], str(self.path))
        self.assertEqual(result["mapping_path"], str(self.mapping_path))
        self.assertFalse((Path(result["report_path"]).parent / self.path.name).exists())
        self.assertFalse((self.takes / "Script__Takes.v2.json").exists())
        self.assertFalse((Path(result["report_path"]).parent / "COMMITTED.json").exists())

    def test_small_timing_or_punctuation_change_is_unchanged(self):
        changed = deepcopy(self.original)
        changed["segments"][2]["words"][0].update(word="target,", start=6.2, end=6.45)
        self.assertEqual(self.run_repair(changed)["status"], "unchanged")

    def test_neighbors_do_not_cross_recordings(self):
        other = self.transcribed / "Script__C02__lav.json"
        other.write_text(json.dumps(self.original), encoding="utf-8")
        (self.intake / "Script__C02__lav.wav").write_bytes(b"other audio")
        self.state["transcript"] = combined.combine([self.state["transcript"], sources.load_transcript(self.root, other)])
        self.state["groupings"]["transcript"] = mapping.initial_groupings(self.state["transcript"])
        self.mapping_path.write_text(json.dumps(mapping._document(self.state, "Script__Takes", 1)), encoding="utf-8")
        # Last group of C01 must not include the first group of C02.
        result = self.run_repair(group=5)
        report = json.loads(Path(result["report_path"]).read_text())
        self.assertEqual(report["display_groups"], [4, 5])
        changed = deepcopy(self.original)
        changed["segments"][3]["words"][0]["word"] = "Different"
        result = self.run_repair(changed, group=5)
        fresh = mapping.read_mapping(self.root, result["mapping_path"])
        recordings = combined.recordings(fresh["transcript"])
        self.assertEqual(len(recordings), 2)
        self.assertEqual(recordings[1], sources.load_transcript(self.root, other))

    def test_ambiguous_anchor_refused_with_diagnostics(self):
        changed = deepcopy(self.original)
        duplicate = deepcopy(changed["segments"][0])
        for item in [duplicate, *duplicate["words"]]:
            item["start"] += 15
            item["end"] += 15
        changed["segments"].append(duplicate)
        # Use a full-window synthetic recognizer so both anchor occurrences reach splice.
        def recognize(*args, **kwargs):
            return {"raw_asr": {}, "alignment": changed, "window_start": 0}
        with self.assertRaisesRegex(repair.RepairError, "ambiguous") as caught:
            repair.repair_transcription(self.root, self.mapping_path, 3, recognizer=recognize)
        self.assertTrue(Path(caught.exception.report_path).exists())
        self.assertEqual(json.loads(Path(caught.exception.report_path).read_text())["status"], "failed")
        self.assertFalse((self.takes / "Script__Takes.v2.json").exists())

    def test_unknown_nonmonotonic_and_unstable_anchors_refused(self):
        for change in ("unknown", "nonmonotonic", "unstable"):
            with self.subTest(change=change):
                changed = deepcopy(self.original)
                if change == "unknown":
                    changed["segments"][2]["words"][0].pop("start")
                elif change == "nonmonotonic":
                    changed["segments"][2]["words"][0]["start"] = 4.0
                else:
                    changed["segments"][0]["words"][0]["end"] = 0.8
                with self.assertRaises(repair.RepairError):
                    self.run_repair(changed)

    def test_version_collisions_and_regeneration_failure(self):
        existing = self.transcribed / "REPAIRS/Script__C01__lav/v1"
        existing.mkdir(parents=True)
        (existing / "sentinel").write_text("do not touch")
        changed = deepcopy(self.original)
        changed["segments"][2]["words"][0]["word"] = "Altered"
        with patch.object(repair, "_fresh_mapping", side_effect=ValueError("synthetic generation failure")):
            with self.assertRaisesRegex(repair.RepairError, "synthetic") as caught:
                self.run_repair(changed)
        report = Path(caught.exception.report_path)
        self.assertEqual(report.parent.name, "v2")
        self.assertFalse((report.parent / self.path.name).exists())
        self.assertEqual((existing / "sentinel").read_text(), "do not touch")
        self.assertTrue(self.path.is_file())
        self.assertFalse((report.parent / "COMMITTED.json").exists())

    def test_input_changed_during_inference_refuses_publication(self):
        def recognize(*args, **kwargs):
            result = self.mock_recognizer(self.original)(*args, **kwargs)
            self.script_path.write_text("Edited script.", encoding="utf-8")
            return result
        with self.assertRaisesRegex(repair.RepairError, "Input changed"):
            repair.repair_transcription(self.root, self.mapping_path, 3, recognizer=recognize)
        self.assertFalse((self.takes / "Script__Takes.v2.json").exists())

    def test_takes_version_collision_is_not_overwritten(self):
        occupied = self.takes / "Script__Takes.v2.json"
        occupied.write_text("occupied version", encoding="utf-8")
        changed = deepcopy(self.original)
        changed["segments"][2]["words"][0]["word"] = "Altered"
        result = self.run_repair(changed)
        self.assertEqual(Path(result["mapping_path"]).name, "Script__Takes.v3.json")
        self.assertEqual(occupied.read_text(), "occupied version")

    def test_leading_silence_uses_recording_start_for_edge_groups(self):
        shifted = deepcopy(self.original)
        for segment in shifted["segments"]:
            for item in [segment, *segment["words"]]:
                item["start"] += 20
                item["end"] += 20
        self.path.write_text(json.dumps(shifted), encoding="utf-8")
        for group in (1, 2):
            with self.subTest(group=group):
                result = repair.repair_transcription(self.root, self.path, group,
                                                     recognizer=self.mock_recognizer(shifted))
                self.assertEqual(result["status"], "unchanged")
                self.assertEqual(json.loads(Path(result["report_path"]).read_text())["window"]["start"], 0)

    def test_final_group_decodes_through_audio_end(self):
        observed = {}
        recognize = self.mock_recognizer(self.original)
        def wrapped(audio, start, end, **kwargs):
            observed["end"] = end
            return recognize(audio, start, end, **kwargs)
        result = repair.repair_transcription(self.root, self.mapping_path, 5, recognizer=wrapped)
        self.assertEqual(result["status"], "unchanged")
        self.assertIsNone(observed["end"])
        def truncated(audio, start, end, **kwargs):
            output = recognize(audio, start, end, **kwargs)
            output["window_end"] -= 1
            return output
        with self.assertRaisesRegex(repair.RepairError, "full recording duration"):
            repair.repair_transcription(self.root, self.mapping_path, 5, recognizer=truncated)

    def test_unrelated_unaligned_words_and_failed_segments_are_preserved(self):
        original = deepcopy(self.original)
        exterior = [
            {"start": 15.0, "end": 16.0, "text": "$100 remains.",
             "words": [{"word": "$100"}, {"word": "remains.", "start": 15.5, "end": 16.0}]},
            {"start": 17.0, "end": 18.0, "text": "Unaligned tail."}]
        original["segments"].extend(exterior)
        self.path.write_text(json.dumps(original), encoding="utf-8")
        self.state["transcript"] = sources.load_transcript(self.root, self.path)
        self.state["groupings"]["transcript"] = mapping.initial_groupings(self.state["transcript"])
        self.mapping_path.write_text(json.dumps(mapping._document(self.state, "Script__Takes", 1)), encoding="utf-8")
        changed = deepcopy(original)
        changed["segments"][2]["words"][0]["word"] = "Altered"
        result = self.run_repair(changed)
        self.assertEqual(result["status"], "changed")
        native = json.loads(Path(result["transcript_path"]).read_text())
        self.assertEqual(native["segments"][-2:], exterior)
        loaded = mapping.read_mapping(self.root, result["mapping_path"])
        self.assertEqual(loaded["transcript"]["words"][-4]["text"], "$100")
        self.assertIsNone(loaded["transcript"]["words"][-4]["start_ms"])

    def test_transcript_only_and_environment_model_cache(self):
        recognizer = self.mock_recognizer(self.original)
        observed = {}
        def wrapped(*args, **kwargs):
            observed.update(kwargs)
            return recognizer(*args, **kwargs)
        with patch.dict("os.environ", {"STUDIO_WHISPERX_MODEL_DIR": "/synthetic/model-cache"}):
            result = repair.repair_transcription(self.root, self.path, 3, recognizer=wrapped)
        self.assertIsNone(result["mapping_path"])
        self.assertEqual(observed["model_dir"], "/synthetic/model-cache")

    def test_cli_json_and_no_inference_imports(self):
        modules_before = set(sys.modules)
        path = Path(__file__).with_name("repair_transcription.py")
        spec = importlib.util.spec_from_file_location("repair_cli_test", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        output = self.root / "result.json"
        result = {"status": "unchanged", "transcript_path": str(self.path), "mapping_path": str(self.mapping_path),
                  "report_path": "diagnostic.json", "summary": {}}
        from io import StringIO
        with patch.object(cli, "repair_transcription", return_value=result), patch("sys.stdout", new_callable=StringIO) as stdout:
            code = cli.main([str(self.mapping_path), "--project-root", str(self.root), "--group", "3", "--result", str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(json.loads(output.read_text()), result)
        added = set(sys.modules) - modules_before
        self.assertFalse(any(name == "whisperx" or name.startswith("whisperx.") for name in added))
        self.assertFalse(any(name == "PySide6" or name.startswith("PySide6.") for name in added))

    def test_interrupt_during_publication_removes_candidate_and_preserves_original(self):
        before = self.path.read_bytes()
        changed = deepcopy(self.original)
        changed["segments"][2]["words"][0]["word"] = "Corrected"
        with patch.object(repair, "_fresh_mapping", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_repair(changed)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.takes.glob("*.json")), [self.mapping_path])
        self.assertFalse(list((self.transcribed / "REPAIRS").rglob("COMMITTED.json")))
        self.assertFalse(list((self.transcribed / "REPAIRS").rglob(self.path.name)))
        report = next((self.transcribed / "REPAIRS").rglob("report.json"))
        self.assertEqual(json.loads(report.read_text())["status"], "cancelled")

    def test_cli_interrupt_returns_cancelled_result_without_traceback(self):
        path = Path(__file__).with_name("repair_transcription.py")
        spec = importlib.util.spec_from_file_location("repair_cancel_cli_test", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        from io import StringIO
        with patch.object(cli, "repair_transcription", side_effect=KeyboardInterrupt), \
                patch.object(cli.signal, "signal") as install_signal, \
                patch("sys.stdout", new_callable=StringIO) as stdout:
            code = cli.main([str(self.mapping_path), "--project-root", str(self.root), "--group", "3"])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "cancelled")
        install_signal.assert_called_once_with(cli.signal.SIGTERM, cli._interrupt)

    def test_unused_decoder_warning_is_filtered_but_other_warnings_survive(self):
        class Audio:
            def __len__(self):
                return 160000
            def __getitem__(self, key):
                return self
            def clip(self, *args):
                return self
            def __mul__(self, value):
                return self
            def astype(self, value):
                return self
            def tobytes(self):
                return b"\x00\x00" * 160000
        model = SimpleNamespace(transcribe=lambda *args, **kwargs: {"segments": []})
        def load_model(*args, **kwargs):
            warnings.warn_explicit(
                "\ntorchcodec is not installed correctly so built-in audio decoding will fail.",
                UserWarning, "io.py", 48, module="pyannote.audio.core.io",
            )
            warnings.warn("Unrelated model warning", UserWarning)
            return model
        provider = SimpleNamespace(
            load_audio=lambda *args: Audio(), load_model=load_model,
            load_align_model=lambda **kwargs: (None, {}), align=lambda *args, **kwargs: {"segments": []},
        )
        messages = []
        with patch.dict(sys.modules, {"whisperx": provider}), warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            repair.recognize_window(self.intake / "Script__C01__lav.wav", 0, 10,
                                    model_dir="cache", progress=messages.append, diagnostics_dir=self.root)
        self.assertEqual([str(item.message) for item in captured], ["Unrelated model warning"])
        self.assertTrue(any("uncached models download to cache" in text for text in messages))


if __name__ == "__main__":
    unittest.main()
