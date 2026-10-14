#!/usr/bin/env python3
"""Generate missing Takes v1 files and check each script's latest numbered review."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
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
from studio.tools.line import boundaries, combined, mapping, sources, quality  # noqa: E402
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
    return {"name": "local-transcript-regrouping", "version": 2,
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
    algorithm["version"] = 5
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
            "script_discarded": document.get("script_discarded", []),
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


REVIEW_INSTRUCTIONS = (
    "Open the reported mapping with Line's Open Line project command. For each blocking script line, "
    "select it and find a usable transcript reading, then link the two groups; split or merge unlinked "
    "groups if needed. Exact or Partial readings satisfy coverage. If the line was deliberately skipped "
    "during filming, right-click it on the script side and choose Discard script. "
    "Save with the suggested <script name>__Takes name to create the next numbered version, "
    "then rerun 3.3. If no usable reading exists and the line is still required, film a pickup."
)


def check_mapping(project: Path, path: Path, script: dict, transcripts: list[dict]) -> dict:
    """Check a saved review against actual inputs, independent of generation provenance."""
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Expected a Takes or Line mapping object.")
    if document.get("schema") == SCHEMA:
        if type(document.get("schema_version")) is not int or document["schema_version"] != VERSION:
            raise ValueError("Unsupported take file schema.")
        document = dict(document, schema=mapping.SCHEMA, schema_version=3,
                        mapping_name=script["source"]["path"], version=1)
    # Actual script/transcript snapshots are loaded below and compared in full.
    # Find Takes checks do not depend on the presence of audio files.
    document = mapping.validate_document(project, document, verify_inputs=False)
    if document["schema_version"] == 2:
        document = dict(document, script=document["sources"]["script"],
                        script_groupings=document["groupings"]["script"],
                        recordings=[{"transcript": document["sources"]["transcript"],
                                     "groupings": document["groupings"]["transcript"],
                                     "links": document["links"], "discarded": document["discarded"]}])
    def without_audio(source: dict) -> dict:
        return {key: value for key, value in source.items() if key != "audio"}
    actual = sorted((without_audio(item["transcript"]) for item in document["recordings"]),
                    key=lambda source: source["source"]["path"])
    expected = sorted((without_audio(source) for source in transcripts),
                      key=lambda source: source["source"]["path"])
    if document["script"] != script or actual != expected:
        raise ValueError("Mapping must contain the current script and every matching transcript; sources or inventory have changed.")
    state = combined.unpack(document)
    mapping.refresh_quality(state)
    coverage = mapping.script_completion(state)
    missing = set(coverage["uncovered_words"])
    blockers = []
    for row in mapping.group_rows(state, "script"):
        words = sorted(missing.intersection(range(row["word_start"], row["word_end"] + 1)))
        if words:
            readings = [link for link in state["links"] if link["script"] == mapping.word_range(row)]
            blockers.append({"line": row["line"], "text": row["text"],
                             "script": mapping.word_range(row), "uncovered_words": words,
                             "reason": "flub_only" if readings else "missing"})
    return {"coverage": coverage, "blocking_lines": blockers}


def generate(project: Path, output_dir: Path) -> dict:
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
        versions = mapping.takes_versions(project, f"{script_path.stem}__Takes", output_dir)
        target = versions[-1][1] if versions else output_dir / f"{script_path.stem}__Takes.v1.json"
        result = {"path": str(target), "status": "checked" if versions else "generated",
                  "script": script["source"]["path"], "version": versions[-1][0] if versions else 1}
        try:
            if versions and sum(number == versions[-1][0] for number, _path in versions) > 1:
                raise ValueError("Multiple Takes files have the latest version number; resolve the ambiguity before checking.")
            if not versions:
                document = {
                    "schema": SCHEMA, "schema_version": VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "algorithm": algorithm, "script": script,
                    "script_groupings": mapping.initial_groupings(script),
                    "recordings": [match_recording(script, transcript) for transcript in transcripts],
                }
                validate_takes(project, document, script, transcripts, algorithm)
                write_json(target, document)
            result.update(check_mapping(project, target, script, transcripts))
        except (OSError, ValueError, TypeError, KeyError) as error:
            result.update(error=str(error), coverage={"complete": False}, blocking_lines=[])
        results.append(result)
    return {"scripts": len(results), "transcripts": sum(len(paths) for _, paths in inventory), "outputs": results,
            "complete": all(item["coverage"]["complete"] for item in results),
            "review_instructions": REVIEW_INSTRUCTIONS if any(not item["coverage"]["complete"] for item in results) else None}


def check_coverage(project: Path, paths: list[Path]) -> dict:
    """Validate one saved mapping per approved script, then check word coverage."""
    project = project.resolve(strict=True)
    expected = {}
    for script_path, transcript_paths in discover(project):
        script = sources.load_script(project, script_path)
        expected[script["source"]["path"]] = (script, [sources.load_transcript(project, path) for path in transcript_paths])
    results, seen = [], set()
    for path in paths:
        path = sources.project_path(project, path)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Expected a Takes or Line mapping object.")
        script = raw.get("script") or raw.get("sources", {}).get("script")
        if not isinstance(script, dict) or not isinstance(script.get("source"), dict):
            raise ValueError("Mapping must contain a script source.")
        key = script["source"]["path"]
        if key not in expected or key in seen:
            raise ValueError("Choose exactly one current mapping for each approved script.")
        expected_script, transcripts = expected[key]
        seen.add(key)
        results.append({"path": str(path), "script": key, **check_mapping(project, path, expected_script, transcripts)})
    if seen != set(expected):
        raise ValueError("Choose exactly one current mapping for each approved script.")
    return {"complete": all(item["coverage"]["complete"] for item in results), "outputs": results,
            "review_instructions": REVIEW_INSTRUCTIONS if any(not item["coverage"]["complete"] for item in results) else None}


def append_log(directory: Path, title: str, items: list[str]) -> None:
    """Initialize once, then append through Studio's existing log helper."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "LOG.md"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write("# Stage log\n")
    except FileExistsError:
        pass
    spec = importlib.util.spec_from_file_location("studio_append_log", ROOT / "scripts/append-log.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.append_entry(path, title, items, datetime.now(timezone.utc).date())


def invalidate(project: Path, failed: tuple[int, int], reason: str) -> list[str]:
    """Invalidate dependent steps conservatively, retaining declared independent work."""
    deleted, folders = [], []
    for stage in sorted(project.iterdir()):
        match = re.match(r"^(\d+)-", stage.name)
        if not stage.is_dir() or not match:
            continue
        major = int(match[1])
        folders.append((stage, (major, 0)))
        for substage in sorted(stage.iterdir()):
            match = re.match(r"^(\d+)-", substage.name)
            if substage.is_dir() and match:
                folders.append((substage, (major, int(match[1]))))
    affected = [directory.relative_to(project).as_posix() for directory, step in folders if step == failed]
    # Visit substages before their containing stage marker, and propagate transitively.
    for directory, step in sorted(folders, key=lambda item: (item[1][0], item[1][1] or float("inf"))):
        if step < failed and step != (failed[0], 0):
            continue
        marker = directory / "DONE"
        relative = directory.relative_to(project).as_posix()
        references = []
        try:
            manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
            for entry in manifest.get("inputs", []) + manifest.get("dependencies", []):
                path = entry.get("path", "").replace("\\", "/")
                if f"/{project.name}/" in path:
                    path = path.split(f"/{project.name}/", 1)[1]
                if entry.get("scope") == "project" or re.match(r"^\d+-", path):
                    references.append(path)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        depends = any(path == parent or path.startswith(parent + "/")
                      for path in references for parent in affected)
        contains_failed = step[1] == 0 and any(parent.startswith(relative + "/") for parent in affected)
        if step != failed and not contains_failed and references and not depends:
            continue
        affected.append(relative if step[1] else relative + "/DONE")
        if marker.exists():
            marker.unlink()
            deleted.append(marker.relative_to(project).as_posix())
            append_log(directory, "Completion invalidated", [reason, "Deleted DONE outright; work files preserved."])
    return deleted


def validate_approved(project: Path) -> None:
    """Check the approved-output gate declared by 1.1, including legacy path spelling."""
    review = project / APPROVED.parent
    marker = review / "DONE"
    if not marker.is_file():
        raise ValueError("Script Review 1.1 DONE is missing; approve the scripts before running 3.3.")
    manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
    conditions = manifest["conditions"]
    approved_names = sorted(path.name for path in (project / APPROVED).iterdir()
                            if path.is_file() and path.suffix.lower() in (".docx", ".txt", ".json"))
    if not approved_names or approved_names != sorted(conditions["approved_docx_names"]):
        raise ValueError("Approved script inventory differs from 1.1 DONE.")
    candidates = sum(path.is_file() for path in review.glob("*.docx"))
    if candidates != conditions["root_candidate_docx_count"] or candidates:
        raise ValueError("Script Review 1.1 still has unapproved candidate scripts.")
    seen = set()
    for entry in manifest["outputs"]:
        name = entry["path"].replace("\\", "/").rsplit("/", 1)[-1]
        path = project / APPROVED / name
        if name in seen or name not in approved_names or sha256(path) != entry["sha256"].lower():
            raise ValueError(f"Approved script fingerprint differs from 1.1 DONE: {name}")
        seen.add(name)
    if seen != set(approved_names):
        raise ValueError("Script Review 1.1 DONE must fingerprint every approved script.")


def completion_manifest(project: Path, result: dict) -> dict:
    inventory = discover(project)
    dependencies = [Path(__file__).relative_to(ROOT),
                    Path(__file__).parents[1].relative_to(ROOT) / "SKILL.md",
                    Path("scripts/append-log.py")]
    dependencies.extend(Path("studio/tools/line") / name for name in
                        ("sources.py", "mapping.py", "actions.py", "quality.py", "combined.py", "boundaries.py"))
    def fingerprint(path: Path, root: Path, scope: str) -> dict:
        return {"scope": scope, "path": path.relative_to(root).as_posix(), "sha256": sha256(path)}
    return {
        "project": project.name, "step": "3.3",
        "dependencies": [fingerprint(ROOT / path, ROOT, "workflow") for path in dependencies],
        "inputs": [fingerprint(path, project, "project") for script, transcripts in inventory for path in (script, *transcripts)],
        "outputs": [fingerprint(Path(item["path"]), project, "project") for item in result["outputs"]],
        "conditions": {
            "approved_scripts": [path.relative_to(project).as_posix() for path, _ in inventory],
            "matching_transcripts": [path.relative_to(project).as_posix() for _, paths in inventory for path in paths],
            "latest_versions": [{"script": item["script"], "version": item["version"],
                                 "path": Path(item["path"]).relative_to(project).as_posix()} for item in result["outputs"]],
            "non_flub_coverage_or_explicit_script_discard": True,
        },
        "algorithm": provenance(),
    }


def run_stage(project: Path, output_dir: Path | None = None) -> dict:
    """Run the complete 3.3 lifecycle with one command."""
    project = project.resolve(strict=True)
    directory = output_dir.resolve() if output_dir is not None else project / OUTPUT
    for upstream in (project / APPROVED, project / TRANSCRIBED):
        if directory == upstream or upstream in directory.parents:
            raise ValueError("The output directory cannot be inside an upstream input folder.")
    isolated = directory != project / OUTPUT
    append_log(directory, "Find Takes run started", [
        f"UTC: {datetime.now(timezone.utc).isoformat()}; project: {project.name}; isolated: {isolated}.",
        "Validate 1.1 approved outputs, generate missing v1 files, then check latest numbered mappings.",
    ])
    failed = (1, 1)
    try:
        validate_approved(project)
        failed = (3, 3)
        inventory = discover(project)
        append_log(directory, "Find Takes inputs validated", [
            "Approved inventory and contents match 1.1 DONE; no unapproved candidates remain.",
            "Input fingerprints: " + json.dumps([
                {"path": path.relative_to(project).as_posix(), "sha256": sha256(path)}
                for script, transcripts in inventory for path in (script, *transcripts)]),
            "Generation/check implementation: " + json.dumps(provenance()),
        ])
        result = generate(project, directory)
        result["isolated"] = isolated
        result["invalidated_markers"] = []
        entries = [f"UTC: {datetime.now(timezone.utc).isoformat()}; success: {result['complete']}."]
        entries.extend(json.dumps(item, ensure_ascii=False) for item in result["outputs"])
        entries.append("Output fingerprints: " + json.dumps([
            {"path": item["path"], "sha256": sha256(Path(item["path"]))}
            for item in result["outputs"] if Path(item["path"]).is_file()]))
        if not result["complete"]:
            if not isolated:
                result["invalidated_markers"] = invalidate(project, failed, "Find Takes 3.3 failed validation or script coverage.")
            entries.append(result["review_instructions"])
        elif not isolated:
            manifest = completion_manifest(project, result)
            marker = directory / "DONE"
            if marker.exists():
                try:
                    previous = json.loads(marker.read_text(encoding="utf-8-sig"))
                    previous.pop("completed_at", None)
                    dependencies = previous.get("dependencies", [])
                    previous["dependencies"] = [entry for entry in dependencies
                                                if not entry["path"].replace("\\", "/").endswith(".agents/skills/studio/SKILL.md")]
                    if previous["dependencies"] != dependencies:
                        entries.append("Migrated legacy Studio root skill dependency out of DONE validation.")
                except (ValueError, TypeError, KeyError, AttributeError):
                    previous = None
                if previous != manifest:
                    result["invalidated_markers"] = invalidate(project, failed, "Find Takes completion dependencies, outputs, or latest version selection changed.")
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            write_json(marker, manifest, overwrite=True)
            entries.append("Wrote DONE with exact input/output fingerprints and latest version selections.")
        entries.append("Invalidated markers: " + json.dumps(result["invalidated_markers"]))
        entries.append("Find Takes does not replace the independent 3.2 transcript inventory completion check.")
        append_log(directory, "Find Takes passed" if result["complete"] else "Find Takes failed", entries)
        result["done_written"] = result["complete"] and not isolated
        result["log"] = str(directory / "LOG.md")
        return result
    except (OSError, ValueError, TypeError, KeyError) as error:
        deleted = [] if isolated else invalidate(project, failed, f"Find Takes prerequisite/run failure: {error}")
        append_log(directory, "Find Takes run failed", [str(error), "Invalidated markers: " + json.dumps(deleted)])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Default: project's 3-Post Production/3-Find Takes/")
    parser.add_argument("--check-coverage", nargs="+", type=Path,
                        help="Check saved Takes/Line mappings instead of generating; one per approved script.")
    args = parser.parse_args()
    try:
        result = (check_coverage(args.project, args.check_coverage) if args.check_coverage else
                  run_stage(args.project, args.output_dir))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["complete"]:
            return 1
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Find Takes: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
