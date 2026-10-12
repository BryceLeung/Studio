#!/usr/bin/env python3
"""Generate one word-range take file per approved script (Python 3.9+, RapidFuzz)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata


def workflow_root() -> Path:
    for candidate in (*Path(__file__).resolve().parents, Path.cwd()):
        if (candidate / "studio/tools/line/sources.py").is_file():
            return candidate
    raise RuntimeError("Run from the Studio repository or install this stage in its workflow tree.")


ROOT = workflow_root()
sys.path.insert(0, str(ROOT))
from studio.tools.line import boundaries, mapping, sources, quality  # noqa: E402
from studio.tools.line.actions import infer_classification  # noqa: E402

SCHEMA = "studio.find-takes"
VERSION = 1
APPROVED = Path("1-Pre Production/1-Script Review/Approved")
TRANSCRIBED = Path("3-Post Production/2-Transcribe")
OUTPUT = Path("3-Post Production/3-Find Takes")
SETTINGS = {
    "lookback": 3, "lookahead": 3, "min_similarity": 0.72,
    "max_length_ratio": 1.4, "extra_words": 4,
    "cursor_distance_penalty": 0.012,
    "min_partial_words": 3, "recovery_similarity": 0.90,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def boundary_provenance() -> dict:
    return {"name": "local-transcript-regrouping", "version": 1,
            "settings": dict(boundaries.SETTINGS), "code_sha256": sha256(Path(boundaries.__file__))}


def alignment_provenance() -> dict:
    return {
        "name": "cursor-word-alignment", "version": 3, "settings": dict(SETTINGS),
        "quality": quality.provenance(),
        "code_sha256": {
            "find_takes.py": sha256(Path(__file__)),
            "studio/tools/line/sources.py": sha256(Path(sources.__file__)),
            "studio/tools/line/mapping.py": sha256(Path(mapping.__file__)),
            "studio/tools/line/actions.py": sha256(ROOT / "studio/tools/line/actions.py"),
            "studio/tools/line/quality.py": sha256(Path(quality.__file__)),
            "studio/tools/line/combined.py": sha256(ROOT / "studio/tools/line/combined.py"),
        },
    }


def provenance() -> dict:
    algorithm = alignment_provenance()
    algorithm["version"] = 4
    algorithm["boundary_refinement"] = boundary_provenance()
    return algorithm


def normalize(word: str) -> str:
    # Keep source word indexes intact; normalization is only for scoring.
    return "".join(char for char in unicodedata.normalize("NFKC", word).casefold() if char.isalnum())


def prefix_distances(script: list[str], transcript: list[str]) -> list[int]:
    """Levenshtein distance to every transcript prefix using two DP rows."""
    previous = list(range(len(transcript) + 1))
    for i, expected in enumerate(script, 1):
        current = [i]
        for j, spoken in enumerate(transcript, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (expected != spoken)))
        previous = current
    return previous


def best_candidate(script_tokens: list[list[str]], spoken: list[str], start: int,
                   expected: int, indexes: range, original_ends: set[int],
                   settings: dict) -> dict | None:
    best = None
    for index in indexes:
        tokens = script_tokens[index]
        size = len(tokens)
        limit = min(len(spoken) - start, math.ceil(size * settings["max_length_ratio"]) + settings["extra_words"])
        distances = prefix_distances(tokens, spoken[start:start + limit])
        for length in range(1, limit + 1):
            similarity = 1 - distances[length] / max(size, length)
            partial = False
            # An abandoned prefix must finish at an original grouping boundary.
            # A complete match across that boundary still outranks this option.
            if (settings["min_partial_words"] <= length < size
                    and start + length in original_ends
                    and spoken[start:start + length] == tokens[:length]):
                prefix_score = 0.70 + 0.20 * length / size
                if prefix_score > similarity:
                    similarity, partial = prefix_score, True
            if similarity < settings["min_similarity"]:
                continue
            score = similarity - settings["cursor_distance_penalty"] * abs(index - expected)
            candidate = {"index": index, "end": start + length, "similarity": similarity,
                         "score": score, "partial_prefix": partial}
            if best is None or (score, similarity, -abs(index - expected), -length) > (
                    best["score"], best["similarity"], -abs(best["index"] - expected), -(best["end"] - start)):
                best = candidate
    return best


def align_recording(script: dict, transcript: dict, settings: dict | None = None) -> dict:
    settings = dict(SETTINGS if settings is None else settings)
    script_groups = mapping.initial_groupings(script)
    script_tokens = [[normalize(word["text"]) for word in script["words"][g["word_start"]:g["word_end"] + 1]]
                     for g in script_groups]
    spoken = [normalize(word["text"]) for word in transcript["words"]]
    original_ends = {row["word_end"] + 1 for row in transcript["rows"]}
    links, scores = [], []
    script_cursor, transcript_cursor = 0, 0
    while transcript_cursor < len(spoken):
        low = max(0, script_cursor - settings["lookback"])
        high = min(len(script_groups), script_cursor + settings["lookahead"] + 1)
        candidate = best_candidate(script_tokens, spoken, transcript_cursor, script_cursor,
                                   range(low, high), original_ends, settings)
        if candidate is None:
            # Strong whole-script recovery handles a restart beyond the local window.
            recovery = best_candidate(script_tokens, spoken, transcript_cursor, script_cursor,
                                      range(len(script_groups)), original_ends, settings)
            if recovery and recovery["similarity"] >= settings["recovery_similarity"] and not recovery["partial_prefix"]:
                candidate = recovery
        if candidate is None:
            transcript_cursor += 1
            continue
        script_range = script_groups[candidate["index"]]
        transcript_range = {"word_start": transcript_cursor, "word_end": candidate["end"] - 1}
        links.append({"script": dict(script_range), "transcript": transcript_range,
                      "classification": infer_classification(mapping.range_text(script, script_range),
                                                               mapping.range_text(transcript, transcript_range))})
        scores.append({"transcript": dict(transcript_range),
                       "similarity": round(candidate["similarity"], 6),
                       "quality_score": round(quality.similarity(mapping.range_text(script, script_range),
                                                                  mapping.range_text(transcript, transcript_range)), 6),
                       "partial_prefix": candidate["partial_prefix"]})
        script_cursor = candidate["index"] + (0 if candidate["partial_prefix"] else 1)
        transcript_cursor = candidate["end"]

    # Regroup around accepted word spans. Boundaries inside a take disappear;
    # boundaries inside unmatched passages retain their original layout.
    boundaries = {0, len(spoken), *original_ends}
    for link in links:
        start, end = link["transcript"]["word_start"], link["transcript"]["word_end"] + 1
        boundaries = {boundary for boundary in boundaries if not start < boundary < end}
        boundaries.update((start, end))
    points = sorted(boundaries)
    groups = [{"word_start": start, "word_end": end - 1} for start, end in zip(points, points[1:])]
    linked = {mapping.range_key(link["transcript"]) for link in links}
    recording = {"transcript": transcript, "groupings": groups, "links": links,
                 "discarded": [dict(group) for group in groups if mapping.range_key(group) not in linked],
                 "match_scores": scores}
    return recording


def match_recording(script: dict, transcript: dict, settings: dict | None = None) -> dict:
    recording = align_recording(script, transcript, settings)
    analysis, recording = boundaries.propose(script, recording)
    recording["boundary_analysis"] = analysis
    return recording


def discover(project: Path) -> list[tuple[Path, list[Path]]]:
    approved = project / APPROVED
    transcribed = project / TRANSCRIBED
    scripts = sorted(p for p in approved.iterdir() if p.is_file() and p.suffix.lower() in (".docx", ".txt", ".json"))
    if not scripts:
        raise ValueError("No approved scripts found in 1.1 Approved/.")
    by_name = {}
    for script in scripts:
        key = script.stem.casefold()
        if key in by_name:
            raise ValueError(f"Approved scripts have conflicting output names: {script.name}")
        by_name[key] = (script, [])
    for transcript in sorted(transcribed.iterdir()):
        if not transcript.is_file() or transcript.suffix.lower() != ".json":
            continue
        match = re.fullmatch(r"(.+)__C\d{2,}__lav", transcript.stem, re.IGNORECASE)
        if match is None:
            continue
        key = match.group(1).casefold()
        if key not in by_name:
            raise ValueError(f"Transcript has no approved script: {transcript.name}")
        by_name[key][1].append(transcript)
    for script, transcripts in by_name.values():
        if not transcripts:
            raise ValueError(f"No 3.2 transcript JSON found for approved script: {script.name}")
    return list(by_name.values())


def validate_takes(project: Path, document: dict, script: dict, transcripts: list[dict], algorithm: dict) -> None:
    if document.get("schema") != SCHEMA or type(document.get("schema_version")) is not int or document["schema_version"] != VERSION:
        raise ValueError("Unsupported take file schema.")
    if document.get("algorithm") != algorithm or document.get("script") != script:
        raise ValueError("Take file has stale script inputs or algorithm metadata.")
    recordings = document.get("recordings")
    if not isinstance(recordings, list) or len(recordings) != len(transcripts):
        raise ValueError("Take file must include every matching transcript.")
    if document.get("script_groupings") != mapping.initial_groupings(script):
        raise ValueError("Take file script groupings must match the approved script.")
    for recording, transcript in zip(recordings, transcripts):
        if recording.get("transcript") != transcript:
            raise ValueError("Take file contains a changed or missing transcript source.")
        line_document = {
            "schema": mapping.SCHEMA, "schema_version": mapping.SCHEMA_VERSION,
            "mapping_name": "find-takes", "version": 1,
            "sources": {"script": script, "transcript": transcript},
            "groupings": {"script": document["script_groupings"], "transcript": recording.get("groupings")},
            "links": recording.get("links"), "discarded": recording.get("discarded"),
        }
        # Source adapters already loaded and fingerprinted the actual inputs.
        # Audio is not a Find Takes dependency, including in text-only benchmarks.
        mapping.validate_document(project, line_document, verify_inputs=False)
        linked = {mapping.range_key(link["transcript"]) for link in recording["links"]}
        discarded = {mapping.range_key(group) for group in recording["discarded"]}
        if linked | discarded != {mapping.range_key(group) for group in recording["groupings"]}:
            raise ValueError("Every transcript word group must be linked or discarded.")


def write_json(path: Path, document: dict, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if not overwrite:
        with path.open("x", encoding="utf-8") as stream:
            try:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                path.unlink()
                raise
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".takes-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def generate(project: Path, output_dir: Path, *, overwrite: bool = False) -> dict:
    project = project.resolve(strict=True)
    output_dir = output_dir.resolve()
    for upstream in (project / APPROVED, project / TRANSCRIBED):
        if output_dir == upstream or upstream in output_dir.parents:
            raise ValueError("The output directory cannot be inside an upstream input folder.")
    algorithm = provenance()
    inventory = discover(project)
    prepared = []
    for script_path, transcript_paths in inventory:
        prepared.append((script_path, sources.load_script(project, script_path),
                         [sources.load_transcript(project, path) for path in transcript_paths]))
    results = []
    for script_path, script, transcripts in prepared:
        target = output_dir / f"{script_path.stem}__Takes.json"
        if target.exists() and not overwrite:
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
                validate_takes(project, existing, script, transcripts, algorithm)
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f"Preserved stale/invalid output {target.name}: {error} Inspect it before using --overwrite.") from error
            results.append({"path": str(target), "status": "reused", "takes": sum(len(r["links"]) for r in existing["recordings"])})
            continue
        document = {
            "schema": SCHEMA, "schema_version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "algorithm": algorithm, "script": script,
            "script_groupings": mapping.initial_groupings(script),
            "recordings": [match_recording(script, transcript) for transcript in transcripts],
        }
        validate_takes(project, document, script, transcripts, algorithm)
        write_json(target, document, overwrite=overwrite)
        results.append({"path": str(target), "status": "generated", "takes": sum(len(r["links"]) for r in document["recordings"])})
    return {"scripts": len(results), "transcripts": sum(len(paths) for _, paths in inventory), "outputs": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Default: project's 3-Post Production/3-Find Takes/")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace stale or edited take files.")
    args = parser.parse_args()
    try:
        result = generate(args.project, args.output_dir or args.project / OUTPUT, overwrite=args.overwrite)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Find Takes: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
