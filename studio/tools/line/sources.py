"""Read immutable project inputs and give their sentences stable references."""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


SEGMENTATION_VERSION = "line-sentences-v1"
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
}


def project_path(project_root: Any, path: Any, *, must_exist: bool = True) -> Path:
    """Resolve a file inside the project, including confinement through symlinks."""
    root = Path(project_root).expanduser().resolve(strict=True)
    candidate = Path(path).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(
        strict=must_exist
    )
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Select a file inside this Studio project.") from exc
    if must_exist and not resolved.is_file():
        raise ValueError(f"Expected a file: {resolved.name}")
    return resolved


def source_reference(root: Any, path: Path, kind: str, format_name: str, *, content: bytes | None = None) -> dict:
    return {
        "path": path.relative_to(Path(root).resolve()).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes() if content is None else content).hexdigest(),
        "segmentation_version": SEGMENTATION_VERSION,
        "kind": kind,
        "format": format_name,
    }


def row_id(source: dict, line: int) -> str:
    return f"{source['kind']}:{source['sha256']}:{source['segmentation_version']}:{line}"


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return sentence spans without normalizing punctuation or spoken words.

    A trailing fragment is retained as a row. Decimal numbers, common titles,
    and initials do not introduce a boundary. This rule is versioned because
    saved mappings refer to its exact sentence ordering.
    """
    spans = []
    start = 0
    for boundary in re.finditer(r"[.!?…]+[\"'”’)]*(?=\s|$)", text):
        punctuation = boundary.group()
        if punctuation.startswith(".") and not punctuation.startswith("..."):
            before = text[: boundary.start()]
            token = re.search(r"([\w.]+)$", before)
            if token and (
                token.group(1).lower() in _ABBREVIATIONS
                or (token.group(1) != "I" and re.fullmatch(r"[A-Z](?:\.[A-Z])*", token.group(1)))
            ):
                continue
        end = boundary.end()
        while start < end and text[start].isspace():
            start += 1
        if start < end:
            spans.append((start, end))
        start = end
    while start < len(text) and text[start].isspace():
        start += 1
    end = len(text.rstrip())
    if start < end:
        spans.append((start, end))
    return spans


def _docx_paragraphs(path: Path, content: bytes) -> list[str]:
    try:
        with ZipFile(BytesIO(content)) as package:
            document = ElementTree.fromstring(package.read("word/document.xml"))
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError(f"Cannot read Word script: {path.name}") from exc
    body = document.find(f"{_WORD_NS}body")
    if body is None:
        raise ValueError(f"Word script has no document body: {path.name}")
    paragraphs = []
    for paragraph in body.findall(f"{_WORD_NS}p"):
        text = []
        for element in paragraph.iter():
            if element.tag == f"{_WORD_NS}t":
                text.append(element.text or "")
            elif element.tag in (f"{_WORD_NS}br", f"{_WORD_NS}cr"):
                text.append("\n")
            elif element.tag == f"{_WORD_NS}tab":
                text.append("\t")
        paragraphs.append("".join(text))
    return paragraphs


def _script_paragraphs(path: Path, content: bytes) -> tuple[str, list[str], str]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        paragraphs = _docx_paragraphs(path, content)
        heading = next(
            (i for i, text in enumerate(paragraphs) if text.strip().lower() == "spoken script"),
            None,
        )
        if heading is None:
            raise ValueError(f"Script must contain a ‘Spoken script’ heading: {path.name}")
        return next((p for p in paragraphs if p.strip()), path.stem), paragraphs[heading + 1 :], "docx-spoken-script"
    if suffix == ".txt":
        return path.stem, content.decode("utf-8-sig").splitlines(), "text"
    if suffix == ".json":
        data = json.loads(content.decode("utf-8-sig"))
        if isinstance(data, str):
            return path.stem, data.splitlines(), "script-json"
        if not isinstance(data, dict):
            raise ValueError("Script JSON must be an object containing ‘spoken_script’ or ‘text’.")
        text = data.get("spoken_script", data.get("text"))
        if isinstance(text, str):
            paragraphs = text.splitlines()
        elif isinstance(text, list) and all(isinstance(item, str) for item in text):
            paragraphs = text
        else:
            raise ValueError("Script JSON must contain ‘spoken_script’ or ‘text’ as text or text lines.")
        return str(data.get("title") or path.stem), paragraphs, "script-json"
    raise ValueError("Scripts must be .docx, .txt, or .json files.")


def load_script(project_root: Any, path: Any) -> dict:
    root = Path(project_root).resolve(strict=True)
    source_path = project_path(root, path)
    content = source_path.read_bytes()
    title, paragraphs, format_name = _script_paragraphs(source_path, content)
    source = source_reference(root, source_path, "script", format_name, content=content)
    rows = []
    for paragraph in paragraphs:
        for text in paragraph.splitlines():
            for start, end in sentence_spans(text):
                line = len(rows) + 1
                rows.append({"id": row_id(source, line), "line": line, "text": text[start:end]})
    if not rows:
        raise ValueError(f"Script contains no spoken sentences: {source_path.name}")
    return {"source": source, "title": title, "rows": rows}


def _time_ms(value: Any, scale: int, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Transcript {field} must be a number or null.")
    if value < 0:
        raise ValueError(f"Transcript {field} cannot be negative.")
    if scale == 1 and not float(value).is_integer():
        raise ValueError(f"Transcript {field} must contain integer milliseconds.")
    return int(round(value * scale))


def _normalized_word(word: Any, index: int, scale: int) -> dict:
    if not isinstance(word, dict):
        raise ValueError(f"Transcript word {index} must be an object.")
    text = word.get("word", word.get("text"))
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Transcript word {index} has no text.")
    explicit_ms = "start_ms" in word or "end_ms" in word
    start = _time_ms(word.get("start_ms" if explicit_ms else "start"), 1 if explicit_ms else scale, "start")
    end = _time_ms(word.get("end_ms" if explicit_ms else "end"), 1 if explicit_ms else scale, "end")
    if start is not None and end is not None and end < start:
        raise ValueError(f"Transcript word {index} ends before it starts.")
    normalized = {"index": index, "text": text, "start_ms": start, "end_ms": end}
    if "score" in word:
        normalized["alignment_score"] = word["score"]
    if "confidence" in word:
        normalized["confidence"] = word["confidence"]
    return normalized


def _read_words(data: dict) -> tuple[list[dict], str]:
    """Prefer per-segment words: these retain WhisperX unaligned tokens."""
    segments = data.get("segments")
    if segments is not None and not isinstance(segments, list):
        raise ValueError("Transcript ‘segments’ must be a list.")
    unit = data.get("timestamp_unit", data.get("time_unit"))
    if unit not in (None, "ms", "milliseconds", "s", "seconds"):
        raise ValueError("Transcript timestamp_unit must be ‘milliseconds’ or ‘seconds’.")
    default_scale = 1 if unit in ("ms", "milliseconds") else 1000
    raw_words = []
    if segments and any(isinstance(segment, dict) and "words" in segment for segment in segments):
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("Transcript segments must be objects.")
            words = segment.get("words")
            if words is None or words == []:
                # Keep text from an unaligned segment with absent word timing.
                words = [{"word": token} for token in str(segment.get("text", "")).split()]
            if not isinstance(words, list):
                raise ValueError("Transcript segment words must be a list.")
            raw_words.extend(words)
        format_name = "whisperx-json" if default_scale == 1000 else "word-json"
    elif isinstance(data.get("words"), list):
        raw_words = data["words"]
        # The Studio provider-independent word contract is milliseconds.
        default_scale = 1000 if unit in ("s", "seconds") else 1
        format_name = "word-json"
    elif isinstance(data.get("word_segments"), list):
        raw_words = data["word_segments"]
        format_name = "whisperx-json"
    else:
        raise ValueError("Transcript JSON must contain ordered words or WhisperX word segments.")
    if not raw_words:
        raise ValueError("Transcript contains no words.")
    return [_normalized_word(word, index, default_scale) for index, word in enumerate(raw_words)], format_name


def _audio_reference(root: Path, source_path: Path, data: dict) -> str | None:
    audio = data.get("source_audio", data.get("audio"))
    if isinstance(audio, dict):
        audio = audio.get("path")
    if audio is not None:
        if not isinstance(audio, str) or not audio:
            raise ValueError("Transcript source_audio must contain a project-relative path.")
        audio_path = project_path(root, audio)
        return audio_path.relative_to(root).as_posix()
    intake = root / "2-Production" / "1-Footage Intake"
    if intake.is_dir():
        candidates = [
            candidate for candidate in intake.iterdir()
            if candidate.is_file() and candidate.stem == source_path.stem and candidate.suffix.lower() == ".wav"
        ]
        if len(candidates) == 1:
            return project_path(root, candidates[0]).relative_to(root).as_posix()
    return None


def load_transcript(project_root: Any, path: Any) -> dict:
    root = Path(project_root).resolve(strict=True)
    source_path = project_path(root, path)
    if source_path.suffix.lower() != ".json":
        raise ValueError("Load a word-timestamped .json transcript.")
    content = source_path.read_bytes()
    data = json.loads(content.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Transcript JSON must be an object.")
    words, format_name = _read_words(data)
    source = source_reference(root, source_path, "transcript", format_name, content=content)
    text_parts = []
    word_spans = []
    offset = 0
    for word in words:
        if text_parts:
            offset += 1
        token = word["text"]
        text_parts.append(token)
        word_spans.append((offset, offset + len(token)))
        offset += len(token)
    text = " ".join(text_parts)
    rows = []
    first_word = 0
    for start, end in sentence_spans(text):
        while first_word < len(words) and word_spans[first_word][1] <= start:
            first_word += 1
        last_word = first_word
        while last_word + 1 < len(words) and word_spans[last_word + 1][0] < end:
            last_word += 1
        line = len(rows) + 1
        # A boundary with no alignment remains unknown even if inner words align.
        rows.append({
            "id": row_id(source, line), "line": line, "text": text[start:end],
            "word_start": first_word, "word_end": last_word,
            "start_ms": words[first_word]["start_ms"], "end_ms": words[last_word]["end_ms"],
        })
    return {
        "source": source, "title": str(data.get("title") or source_path.stem),
        "rows": rows, "words": words, "audio": _audio_reference(root, source_path, data),
    }


def discover_sources(project_root: Any) -> dict[str, list[str]]:
    root = Path(project_root).resolve(strict=True)
    approved = root / "1-Pre Production" / "1-Script Review" / "Approved"
    transcripts = root / "3-Post Production" / "2-Transcribe"
    return {
        "scripts": sorted(path.relative_to(root).as_posix() for path in approved.glob("*") if path.is_file() and path.suffix.lower() == ".docx"),
        "transcripts": sorted(path.relative_to(root).as_posix() for path in transcripts.glob("*") if path.is_file() and path.suffix.lower() == ".json"),
    }
