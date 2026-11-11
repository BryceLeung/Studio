"""Headless, opt-in local transcript repairs. Never edits a source or stage marker."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import warnings
from typing import Callable

from . import combined, mapping, sources


class RepairError(ValueError):
    """A refused repair, optionally with persisted inference diagnostics."""

    def __init__(self, message: str, report_path: Path | None = None):
        super().__init__(message)
        self.report_path = str(report_path) if report_path else None


def _write_json(path: Path, data: dict) -> None:
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        try:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            path.unlink(missing_ok=True)
            raise


def _norm(text: str) -> str:
    return "".join(re.findall(r"\w+", text.casefold()))


def _segment_words(segment: dict) -> list[dict]:
    words = segment.get("words")
    if words is None or words == []:
        return [{"word": token} for token in str(segment.get("text", "")).split()]
    if not isinstance(words, list):
        raise RepairError("Native segment words must be a list.")
    return words


def _check_timing(words: list[dict]) -> None:
    previous_end = -1.0
    for word in words:
        start, end = word.get("start"), word.get("end")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (start, end)):
            raise RepairError("Unknown word boundary timing; repair refused.")
        if start < 0 or end < start or start + 0.001 < previous_end:
            raise RepairError("Nonmonotonic word boundary timing; repair refused.")
        previous_end = end


def _native_words(document: dict, *, validate_timing: bool = True) -> list[dict]:
    if document.get("timestamp_unit", document.get("time_unit")) not in (None, "s", "seconds"):
        raise RepairError("Repair requires native WhisperX timestamps in seconds.")
    segments = document.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RepairError("Repair requires native WhisperX segments with per-word timing.")
    words = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise RepairError("Native segments must be objects.")
        words.extend(_segment_words(segment))
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get("word"), str) or not word["word"].strip():
            raise RepairError("Every splice word must have recognizable text.")
    if validate_timing:
        _check_timing(words)
    return words


def _segments_slice(document: dict, first: int, end: int) -> list[dict]:
    """Keep intact native segments, reconstruct only segments cut by splice edges."""
    result, offset = [], 0
    for segment in document["segments"]:
        count = len(_segment_words(segment))
        low, high = max(0, first - offset), min(count, end - offset)
        if low < high:
            item = deepcopy(segment)
            if low != 0 or high != count:
                item["words"] = deepcopy(_segment_words(segment)[low:high])
                item["text"] = " ".join(word["word"].strip() for word in item["words"])
                item["start"], item["end"] = item["words"][0]["start"], item["words"][-1]["end"]
                item.pop("chars", None)
            result.append(item)
        offset += count
    return result


def _anchor(original: list[dict], recognized: list[dict], first: int, end: int,
            tolerance: float) -> tuple[int, int]:
    pattern = [_norm(word["word"]) for word in original[first:end]]
    if not pattern:
        raise RepairError("Missing outer context anchor.")
    old_tokens = [_norm(word["word"]) for word in original]
    new_tokens = [_norm(word["word"]) for word in recognized]
    def occurrences(tokens):
        return [i for i in range(len(tokens) - len(pattern) + 1) if tokens[i:i + len(pattern)] == pattern]
    if occurrences(old_tokens) != [first]:
        raise RepairError("Ambiguous outer context anchor in original transcript.")
    candidates = occurrences(new_tokens)
    if len(candidates) != 1:
        raise RepairError("Missing or ambiguous outer context anchor in recognition.")
    index = candidates[0]
    _check_timing(recognized[index:index + len(pattern)])
    if any(abs(original[first + j][field] - recognized[index + j][field]) > tolerance
           for j in range(len(pattern)) for field in ("start", "end")):
        raise RepairError("Outer context anchor timing is unstable; repair refused.")
    return index, index + len(pattern)


def splice(original: dict, aligned: dict, first: int, end: int, *,
           timing_threshold: float = 0.5, anchor_words: int = 3,
           anchor_tolerance: float = 0.5) -> tuple[dict, dict]:
    """Replace [first,end) only between unique outer word anchors, in source time."""
    old, new = (_native_words(document, validate_timing=False) for document in (original, aligned))
    if not 0 <= first < end <= len(old):
        raise RepairError("Selected transcript range is invalid.")
    _check_timing(old[max(0, first - anchor_words):min(len(old), end + anchor_words)])
    replacement_first, replacement_end = 0, len(new)
    if first:
        _, replacement_first = _anchor(old, new, max(0, first - anchor_words), first, anchor_tolerance)
    if end < len(old):
        replacement_end, _ = _anchor(old, new, end, min(len(old), end + anchor_words), anchor_tolerance)
    if replacement_first >= replacement_end:
        raise RepairError("Outer anchors overlap or recognition contains no replacement words.")
    replacement = new[replacement_first:replacement_end]
    _check_timing(replacement)
    _check_timing(old[max(0, first - 1):first] + replacement + old[end:min(len(old), end + 1)])
    before, after = old[first:end], replacement
    old_tokens, new_tokens = ([_norm(word["word"]) for word in words] for words in (before, after))
    shifts = []
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    for old_index, new_index, length in matcher.get_matching_blocks():
        for delta in range(length):
            shift = max(abs(before[old_index + delta][field] - after[new_index + delta][field])
                        for field in ("start", "end"))
            if shift + 1e-9 >= timing_threshold:
                shifts.append({"old_word": first + old_index + delta,
                               "word": after[new_index + delta]["word"], "shift_seconds": shift})
    summary = {"wording_changed": old_tokens != new_tokens, "timing_shifts": shifts,
               "old_word_count": len(before), "new_word_count": len(after),
               "original_word_range": [first, end],
               "recognized_word_range": [replacement_first, replacement_end],
               "old_text": " ".join(w["word"] for w in before),
               "new_text": " ".join(w["word"] for w in after)}
    candidate = deepcopy(original)
    candidate["segments"] = (_segments_slice(original, 0, first)
                             + _segments_slice(aligned, replacement_first, replacement_end)
                             + _segments_slice(original, end, len(old)))
    candidate["word_segments"] = [deepcopy(word) for segment in candidate["segments"] for word in _segment_words(segment)]
    if "words" in candidate:
        candidate["words"] = deepcopy(candidate["word_segments"])
    if "text" in candidate:
        candidate["text"] = " ".join(segment["text"].strip() for segment in candidate["segments"])
    return candidate, summary


def recognize_window(audio_path: Path, start: float, end: float | None, *, model_dir: str | None,
                     progress: Callable[[str], None], diagnostics_dir: Path) -> dict:
    """Lazy inference: English large-v3 / CPU int8 / Silero, then forced alignment."""
    # Import neither torch nor WhisperX when inspecting a mapping or importing this module.
    with redirect_stdout(sys.stderr):
        import whisperx

        progress("Loading lav audio with FFmpeg")
        audio = whisperx.load_audio(str(audio_path))
        duration = len(audio) / 16000
        stop = duration if end is None else min(end, duration)
        begin_sample, end_sample = int(start * 16000), int(stop * 16000)
        if end_sample <= begin_sample:
            raise RepairError("Selected audio window is empty or outside the lav recording.")
        chunk = audio[begin_sample:end_sample]
        actual_start = begin_sample / 16000
        import wave

        with (diagnostics_dir / "audio_window.wav").open("xb") as handle:
            with wave.open(handle, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes((chunk.clip(-1, 1) * 32767).astype("<i2").tobytes())
        _write_json(diagnostics_dir / "window.json", {
            "source_audio": str(audio_path), "source_sample_range": [begin_sample, end_sample],
            "sample_rate": 16000, "window_start": actual_start, "window_end": end_sample / 16000,
            "audio_duration": duration})
        progress(f"Loading large-v3 recognition model (uncached models download to {model_dir}; this may take several minutes)")
        with warnings.catch_warnings():
            # Silero receives audio decoded by FFmpeg above, not TorchCodec.
            warnings.filterwarnings("ignore", message=r"\s*torchcodec is not installed correctly.*",
                                    category=UserWarning, module=r"pyannote\.audio\.core\.io")
            model = whisperx.load_model("large-v3", "cpu", compute_type="int8", language="en",
                                        vad_method="silero", download_root=model_dir)
        progress("Recognizing padded audio window")
        # Short chunks reduce collapsed restarts within a local repair window.
        raw = model.transcribe(chunk, batch_size=1, language="en", chunk_size=10)
        _write_json(diagnostics_dir / "raw_asr.json", raw)
        del model
        progress("Loading word-alignment model (may download if not cached)")
        align_model, metadata = whisperx.load_align_model(language_code="en", device="cpu", model_dir=model_dir)
        progress("Aligning recognized words")
        aligned = whisperx.align(raw["segments"], align_model, metadata, chunk, "cpu", return_char_alignments=False)
        _write_json(diagnostics_dir / "alignment_local.json", aligned)
    return {"raw_asr": raw, "alignment": aligned, "window_start": actual_start,
            "window_end": end_sample / 16000, "audio_duration": duration}


def _absolute_alignment(aligned: dict, offset: float) -> dict:
    result = deepcopy(aligned)
    for segment in result.get("segments", []):
        for item in [segment, *segment.get("words", []), *segment.get("chars", [])]:
            for field in ("start", "end"):
                if item.get(field) is not None:
                    item[field] += offset
    result["word_segments"] = [deepcopy(word) for segment in result.get("segments", [])
                               for word in _segment_words(segment)]
    return result


def _find_takes():
    path = Path(__file__).resolve().parents[3] / ".agents/skills/studio/3-Post Production/3-Find Takes/scripts/find_takes.py"
    spec = importlib.util.spec_from_file_location("studio_repair_find_takes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh_mapping(root: Path, input_path: Path, state: dict, transcript_path: Path,
                   recording_index: int) -> Path:
    generator = _find_takes()
    script = sources.load_script(root, state["script"]["source"]["path"])
    transcripts = [sources.load_transcript(root, transcript_path if index == recording_index else item["source"]["path"])
                   for index, item in enumerate(combined.recordings(state["transcript"]))]
    algorithm = generator.provenance()
    document = {"schema": generator.SCHEMA, "schema_version": generator.VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "algorithm": algorithm, "script": script,
                "script_groupings": mapping.initial_groupings(script),
                "recordings": [generator.match_recording(script, transcript) for transcript in transcripts]}
    generator.validate_takes(root, document, script, transcripts, algorithm)
    name = re.sub(r"\.v[0-9]+$", "", input_path.stem)
    version = max((number for number, _ in mapping.takes_versions(root, name, input_path.parent)), default=0) + 1
    while True:
        target = input_path.parent / f"{name}.v{version}.json"
        try:
            generator.write_json(target, document)
            break
        except FileExistsError:
            version += 1
    try:
        # Read with the same public entry point the UI uses, verifying every source.
        fresh = mapping.read_mapping(root, target)
        actual = combined.recordings(fresh["transcript"])[recording_index]["source"]["path"]
        if actual != transcript_path.relative_to(root).as_posix():
            raise RepairError("Regenerated mapping is not bound to the exact candidate transcript.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def repair_transcription(project_root, filename, group: int, *, model_dir=None, padding: float = 5.0,
                         timing_threshold: float = 0.5, anchor_words: int = 3,
                         anchor_tolerance: float = 0.5, progress=None, recognizer=None) -> dict:
    """Generate an immutable candidate for a one-based global Line display group.

    The injectable recognizer follows recognize_window's signature and returns local
    WhisperX alignment plus window_start. No current-project selection or Qt is used.
    """
    progress = progress or (lambda message: None)
    recognizer = recognizer or recognize_window
    model_dir = model_dir or os.environ.get("STUDIO_WHISPERX_MODEL_DIR") or str(
        Path(__file__).resolve().parents[3] / ".agents/skills/studio/3-Post Production/2-Transcribe/.model-cache")
    if isinstance(group, bool) or not isinstance(group, int) or group < 1:
        raise RepairError("--group must be a positive one-based display group.")
    for value, name in ((padding, "padding"), (timing_threshold, "timing threshold"), (anchor_tolerance, "anchor tolerance")):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise RepairError(f"{name} must be finite and nonnegative.")
    if timing_threshold == 0 or isinstance(anchor_words, bool) or not isinstance(anchor_words, int) or anchor_words < 1:
        raise RepairError("Timing threshold and anchor word count must be positive.")
    root = Path(project_root).expanduser().resolve(strict=True)
    input_path = sources.project_path(root, filename)
    input_content = input_path.read_bytes()
    raw_input = json.loads(input_content.decode("utf-8-sig"))
    is_mapping = raw_input.get("schema") in (mapping.SCHEMA, "studio.find-takes")
    if is_mapping:
        state = mapping.read_mapping(root, input_path)
    else:
        state = mapping.new_state()
        state["transcript"] = sources.load_transcript(root, input_path)
        state["groupings"]["transcript"] = mapping.initial_groupings(state["transcript"])
    source_paths = [sources.project_path(root, item["source"]["path"])
                    for item in combined.recordings(state["transcript"])]
    if is_mapping:
        source_paths.append(sources.project_path(root, state["script"]["source"]["path"]))
    source_snapshots = {path: path.read_bytes() for path in source_paths}
    if is_mapping:
        # Close the load/snapshot race against every retained recording, not just the target.
        mapping.read_mapping(root, input_path)
    groups = state["groupings"]["transcript"]
    if group > len(groups):
        raise RepairError("Selected display group is outside the loaded transcript.")
    found = combined.recording_for(state["transcript"], groups[group - 1])
    if found is None:
        raise RepairError("Selected display group crosses recordings.")
    recording_index, recording, offset, limit = found
    selected = [index for index in range(max(0, group - 2), min(len(groups), group + 1))
                if offset <= groups[index]["word_start"] <= groups[index]["word_end"] < limit]
    first, end = groups[selected[0]]["word_start"] - offset, groups[selected[-1]]["word_end"] - offset + 1
    transcript_path = sources.project_path(root, recording["source"]["path"])
    original_content = transcript_path.read_bytes()
    original = json.loads(original_content.decode("utf-8-sig"))
    words = _native_words(original, validate_timing=False)
    _check_timing(words[max(0, first - anchor_words):min(len(words), end + anchor_words)])
    normalized, _ = sources._read_words(original)
    if normalized != recording["words"]:
        raise RepairError("Mapping and native transcript words are inconsistent.")
    if not recording.get("audio"):
        raise RepairError("Cannot locate an unambiguous lav audio source.")
    audio_path = sources.project_path(root, recording["audio"])
    if audio_path.suffix.casefold() != ".wav" or not audio_path.stem.casefold().endswith("__lav"):
        raise RepairError("Repair requires the recording's lav WAV source.")
    audio_stat = audio_path.stat()
    audio_identity = (audio_stat.st_dev, audio_stat.st_ino, audio_stat.st_size, audio_stat.st_mtime_ns)
    transcribe_dir = root / "3-Post Production/2-Transcribe"
    if transcribe_dir not in transcript_path.parents:
        raise RepairError("Transcript must be inside the project's 2-Transcribe stage.")
    window_start = 0.0 if first == 0 else max(0.0, words[max(0, first - anchor_words)]["start"] - padding)
    # At a recording edge there is no outer anchor: decode to the real audio edge.
    window_end = None if end == len(words) else words[min(len(words), end + anchor_words) - 1]["end"] + padding
    versions_dir = sources.project_path(root, transcribe_dir / "REPAIRS" / transcript_path.stem, must_exist=False)
    versions_dir.mkdir(parents=True, exist_ok=True)
    version = 1
    while True:
        directory = versions_dir / f"v{version}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            version += 1
    report_path = directory / "report.json"
    report = {"schema": "studio.transcription-repair", "schema_version": 1,
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "input_path": str(input_path), "original_transcript_path": str(transcript_path),
              "audio_path": str(audio_path), "selected_group": group,
              "display_groups": [index + 1 for index in selected], "recording_index": recording_index,
              "window": {"start": window_start, "end": window_end, "padding": padding},
              "settings": {"model": "large-v3", "language": "en", "device": "cpu", "compute_type": "int8",
                           "vad_method": "silero", "chunk_size": 10, "batch_size": 1,
                           "model_dir": str(model_dir) if model_dir else None,
                           "timing_threshold": timing_threshold, "anchor_words": anchor_words,
                           "anchor_tolerance": anchor_tolerance}}
    candidate_path, new_mapping = None, None
    def verify_unchanged():
        if input_path.read_bytes() != input_content or any(path.read_bytes() != content for path, content in source_snapshots.items()):
            raise RepairError("Input changed during repair; candidate publication refused.")
        current_audio = audio_path.stat()
        if (current_audio.st_dev, current_audio.st_ino, current_audio.st_size, current_audio.st_mtime_ns) != audio_identity:
            raise RepairError("Lav audio changed during repair; candidate publication refused.")
    try:
        progress(f"Repairing display groups {report['display_groups']} in {transcript_path.name}")
        inference = recognizer(audio_path, window_start, window_end, model_dir=str(model_dir) if model_dir else None,
                               progress=progress, diagnostics_dir=directory)
        for name, data in (("raw_asr.json", inference.get("raw_asr", {})),
                           ("alignment_local.json", inference["alignment"])):
            if not (directory / name).exists():
                _write_json(directory / name, data)
        actual_start = inference["window_start"]
        if not isinstance(actual_start, (int, float)) or not math.isfinite(actual_start) or actual_start < 0:
            raise RepairError("Recognizer returned an invalid window start.")
        if first == 0 and actual_start != 0:
            raise RepairError("Recording-start splice requires audio from time zero.")
        if end == len(words):
            duration, actual_end = inference.get("audio_duration"), inference.get("window_end")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                   for value in (duration, actual_end)) or duration <= 0 or abs(duration - actual_end) > 1 / 16000:
                raise RepairError("Recording-end splice requires audio through the full recording duration.")
        alignment = _absolute_alignment(inference["alignment"], actual_start)
        _write_json(directory / "alignment_source_time.json", alignment)
        progress("Comparing repaired transcript and checking splice boundaries")
        candidate, summary = splice(original, alignment, first, end, timing_threshold=timing_threshold,
                                    anchor_words=anchor_words, anchor_tolerance=anchor_tolerance)
        report["summary"] = summary
        report["inference_window"] = {key: inference.get(key) for key in ("window_start", "window_end", "audio_duration")}
        changed = summary["wording_changed"] or bool(summary["timing_shifts"])
        verify_unchanged()
        if changed:
            candidate_path = directory / transcript_path.name
            _write_json(candidate_path, candidate)
            if is_mapping:
                progress("Generating fresh Takes for all retained recordings")
                new_mapping = _fresh_mapping(root, input_path, state, candidate_path, recording_index)
            verify_unchanged()
        result = {"status": "changed" if changed else "unchanged",
                  "transcript_path": str(candidate_path or transcript_path),
                  "mapping_path": str(new_mapping or input_path) if is_mapping else None,
                  "report_path": str(report_path), "summary": summary}
        progress("Saving repair report and publishing validated outputs")
        report.update(result)
        _write_json(report_path, report)
        if changed:
            _write_json(directory / "COMMITTED.json", {
                "status": "success", "transcript": candidate_path.name,
                "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                "source": {"path": transcript_path.relative_to(root).as_posix(),
                           "sha256": hashlib.sha256(original_content).hexdigest()}})
        return result
    except BaseException as error:
        (directory / "COMMITTED.json").unlink(missing_ok=True)
        if new_mapping is not None:
            new_mapping.unlink(missing_ok=True)
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)
        report.update(status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed",
                      error="Repair cancelled" if isinstance(error, KeyboardInterrupt) else str(error))
        report_path.unlink(missing_ok=True)
        _write_json(report_path, report)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise RepairError(str(error), report_path) from error
