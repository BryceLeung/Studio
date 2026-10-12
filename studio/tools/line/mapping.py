"""The shared mapping contract for manual review and matching algorithms."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .sources import SEGMENTATION_VERSION, load_script, load_transcript, project_path, row_id


SCHEMA = "studio.line.mapping"
SCHEMA_VERSION = 1
CLASSIFICATIONS = ("exact", "good", "flub")
PANELS = ("script", "transcript")


def new_state() -> dict:
    return {
        "script": None, "transcript": None, "links": [], "discarded": [],
        "selected": {"script": None, "transcript": None}, "dirty": False,
        "mapping_path": None, "mapping_name": None,
        "_closed_sources": {"script": None, "transcript": None},
    }


def active_source(state: dict, panel: str) -> dict | None:
    """Return retained source data even if its panel has been closed."""
    return state.get(panel) or state.get("_closed_sources", {}).get(panel)


def coverage(state: dict) -> dict[str, str]:
    script = active_source(state, "script")
    if not script:
        return {}
    statuses = {row["id"]: "missing" for row in script["rows"]}
    rank = {"missing": 0, "flub": 1, "good": 2, "exact": 3}
    for link in state["links"]:
        script_id = link["script_id"]
        classification = link["classification"]
        if script_id in statuses and rank[classification] > rank[statuses[script_id]]:
            statuses[script_id] = classification
    return statuses


def review_status(state: dict) -> dict[str, str]:
    transcript = active_source(state, "transcript")
    if not transcript:
        return {}
    linked = {link["transcript_id"] for link in state["links"]}
    discarded = set(state["discarded"])
    return {
        row["id"]: "discarded" if row["id"] in discarded else "linked" if row["id"] in linked else "unreviewed"
        for row in transcript["rows"]
    }


def _integer(value: Any, description: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{description} must be a nonnegative integer{' or null' if nullable else ''}.")


def _validate_source(root: Path, source_data: Any, kind: str, verify_inputs: bool) -> set[str]:
    if not isinstance(source_data, dict):
        raise ValueError(f"Mapping must contain a {kind} source object.")
    reference = source_data.get("source")
    if not isinstance(reference, dict):
        raise ValueError(f"Mapping {kind} must contain a source reference.")
    if reference.get("kind") != kind:
        raise ValueError(f"Mapping {kind} source kind is invalid.")
    if reference.get("segmentation_version") != SEGMENTATION_VERSION:
        raise ValueError(f"Unsupported {kind} sentence segmentation version.")
    path = reference.get("path")
    if not isinstance(path, str) or not path or Path(path).is_absolute() or "\\" in path:
        raise ValueError(f"Mapping {kind} path must be relative to the project with forward slashes.")
    fingerprint = reference.get("sha256")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ValueError(f"Mapping {kind} fingerprint must be a lowercase SHA-256 digest.")
    source_path = project_path(root, path, must_exist=verify_inputs)
    if path != source_path.relative_to(root).as_posix():
        raise ValueError(f"Mapping {kind} path must be a normalized project-relative path.")
    if verify_inputs and hashlib.sha256(source_path.read_bytes()).hexdigest() != fingerprint:
        raise ValueError(f"The {kind} input has changed since this mapping was saved: {path}")
    rows = source_data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"Mapping {kind} rows must be a nonempty list.")
    ids = set()
    words = source_data.get("words") if kind == "transcript" else None
    if kind == "transcript":
        if not isinstance(words, list) or not words:
            raise ValueError("Mapping transcript must preserve its ordered words.")
        for index, word in enumerate(words):
            if not isinstance(word, dict) or word.get("index") != index or isinstance(word.get("index"), bool):
                raise ValueError("Mapping transcript word indexes must be consecutive from zero.")
            if not isinstance(word.get("text"), str) or not word["text"].strip():
                raise ValueError("Mapping transcript words must have text.")
            _integer(word.get("start_ms"), "Word start_ms", nullable=True)
            _integer(word.get("end_ms"), "Word end_ms", nullable=True)
            if word.get("start_ms") is not None and word.get("end_ms") is not None and word["end_ms"] < word["start_ms"]:
                raise ValueError("A transcript word ends before it starts.")
        audio = source_data.get("audio")
        if audio is not None:
            if not isinstance(audio, str) or Path(audio).is_absolute() or "\\" in audio:
                raise ValueError("Mapping source audio must be project-relative.")
            project_path(root, audio, must_exist=verify_inputs)
    next_word = 0
    for line, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("line") != line or isinstance(row.get("line"), bool):
            raise ValueError(f"Mapping {kind} line numbers must be consecutive from one.")
        if row.get("id") != row_id(reference, line):
            raise ValueError(f"Mapping {kind} row ID does not match its source and line.")
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError(f"Mapping {kind} rows must have sentence text.")
        ids.add(row["id"])
        if kind == "transcript":
            _integer(row.get("word_start"), "Transcript word_start")
            _integer(row.get("word_end"), "Transcript word_end")
            if row["word_start"] != next_word or row["word_end"] < row["word_start"] or row["word_end"] >= len(words):
                raise ValueError("Transcript sentence word ranges must cover ordered words once, without gaps.")
            next_word = row["word_end"] + 1
            _integer(row.get("start_ms"), "Sentence start_ms", nullable=True)
            _integer(row.get("end_ms"), "Sentence end_ms", nullable=True)
            if row.get("start_ms") != words[row["word_start"]].get("start_ms") or row.get("end_ms") != words[row["word_end"]].get("end_ms"):
                raise ValueError("Transcript row timing must match its first and last source words.")
    if kind == "transcript" and next_word != len(words):
        raise ValueError("Transcript sentence rows must retain every source word.")
    if verify_inputs:
        fresh = (load_script if kind == "script" else load_transcript)(root, source_path)
        if fresh["source"]["sha256"] != fingerprint:
            raise ValueError(f"The {kind} input changed while this mapping was being loaded.")
        if rows != fresh["rows"]:
            raise ValueError(f"Mapping {kind} sentences differ from their immutable source segmentation.")
        if kind == "transcript":
            fields = ("index", "text", "start_ms", "end_ms")
            if len(words) != len(fresh["words"]) or any(
                any(word.get(field) != actual.get(field) for field in fields)
                for word, actual in zip(words, fresh["words"])
            ):
                raise ValueError("Mapping transcript words differ from their source transcript.")
    return ids


def validate_document(project_root: Any, document: Any, *, verify_inputs: bool = True) -> dict:
    root = Path(project_root).resolve(strict=True)
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError(f"Expected a {SCHEMA} mapping document.")
    if document.get("schema_version") != SCHEMA_VERSION or isinstance(document.get("schema_version"), bool):
        raise ValueError("Unsupported Line! mapping schema version.")
    sources = document.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("Mapping must contain script and transcript sources.")
    script_ids = _validate_source(root, sources.get("script"), "script", verify_inputs)
    transcript_ids = _validate_source(root, sources.get("transcript"), "transcript", verify_inputs)
    links = document.get("links")
    discarded = document.get("discarded")
    if not isinstance(links, list) or not isinstance(discarded, list):
        raise ValueError("Mapping links and discarded must be lists.")
    if not all(isinstance(row_id_value, str) and row_id_value in transcript_ids for row_id_value in discarded):
        raise ValueError("Discarded entries must refer to transcript sentence IDs.")
    if len(discarded) != len(set(discarded)):
        raise ValueError("Discarded transcript sentence IDs must be unique.")
    linked = set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {"script_id", "transcript_id", "classification"}:
            raise ValueError("Every link must contain script_id, transcript_id, and classification.")
        if not isinstance(link.get("script_id"), str) or not isinstance(link.get("transcript_id"), str):
            raise ValueError("Mapping link sentence IDs must be strings.")
        if link["script_id"] not in script_ids or link["transcript_id"] not in transcript_ids:
            raise ValueError("A mapping link refers to an unknown sentence.")
        if not isinstance(link.get("classification"), str) or link["classification"] not in CLASSIFICATIONS:
            raise ValueError("Link classification must be exact, good, or flub.")
        if link["transcript_id"] in linked:
            raise ValueError("Each transcript sentence can link to only one script sentence.")
        if link["transcript_id"] in discarded:
            raise ValueError("A discarded transcript sentence cannot also be linked.")
        linked.add(link["transcript_id"])
    if not isinstance(document.get("mapping_name"), str) or not document["mapping_name"]:
        raise ValueError("Mapping must contain its name.")
    _integer(document.get("version"), "Mapping version")
    if document["version"] < 1:
        raise ValueError("Mapping version must start at one.")
    return document


def _document(state: dict, name: str, version: int) -> dict:
    script = active_source(state, "script")
    transcript = active_source(state, "transcript")
    if not script or not transcript:
        raise ValueError("Load a script and transcript before saving a mapping.")
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "mapping_name": name, "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parent": state.get("mapping_path"),
        "sources": {"script": deepcopy(script), "transcript": deepcopy(transcript)},
        "links": deepcopy(state["links"]), "discarded": list(state["discarded"]),
    }


def save_version(project_root: Any, state: dict, name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Enter a mapping name.")
    name = name.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,119}", name) is None or name.endswith(".") or ".." in name:
        raise ValueError("Mapping names may use letters, numbers, spaces, underscores, hyphens, and dots.")
    root = Path(project_root).resolve(strict=True)
    directory = project_path(root, "studio/line", must_exist=False)
    # Validate before creating any output directory or file.
    document = _document(state, name, 1)
    validate_document(root, document)
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(re.escape(name) + r"\.v(\d+)\.json$")
    versions = [int(match.group(1)) for path in directory.iterdir() if (match := pattern.fullmatch(path.name))]
    version = max(versions, default=0) + 1
    while True:
        target = directory / f"{name}.v{version:03d}.json"
        document["version"] = version
        encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            version += 1
            continue
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            # Do not leave a partial version which could be offered for loading.
            target.unlink(missing_ok=True)
            raise
        break
    relative = target.relative_to(root).as_posix()
    state["mapping_path"] = relative
    state["mapping_name"] = name
    state["dirty"] = False
    return relative


def read_mapping(project_root: Any, path: Any) -> dict:
    root = Path(project_root).resolve(strict=True)
    source_path = project_path(root, path)
    document = json.loads(source_path.read_text(encoding="utf-8-sig"))
    validate_document(root, document)
    state = new_state()
    state.update({
        "script": deepcopy(document["sources"]["script"]),
        "transcript": deepcopy(document["sources"]["transcript"]),
        "links": deepcopy(document["links"]), "discarded": list(document["discarded"]),
        "mapping_path": source_path.relative_to(root).as_posix(),
        "mapping_name": document["mapping_name"],
        "selected": {"script": 0, "transcript": 0},
    })
    return state
