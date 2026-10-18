#!/usr/bin/env python3
"""Score explicitly supplied latest Takes mappings; never modify source mappings."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "studio/tools/line/mapping.py").is_file())
STAGE = Path("3-Post Production/4-Take Selection")
FIND_SCRIPT = ROOT / ".agents/skills/studio/3-Post Production/3-Find Takes/scripts/find_takes.py"
spec = importlib.util.spec_from_file_location("studio_find_takes_for_selection", FIND_SCRIPT)
find = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find)
from scripts.stage_completion import record_done, same_manifest  # noqa: E402
from scripts.validation import require_prerequisites  # noqa: E402


def fingerprint(path: Path, root: Path, scope: str) -> dict:
    return {"scope": scope, "path": path.relative_to(root).as_posix(),
            "sha256": find.sha256(path)}


def checked_reference(project: Path, path: Path, reference: dict) -> dict:
    """Retain a valid persisted digest because it participates in take identities."""
    current = fingerprint(path, project, "project")
    if (dict(reference, sha256=current["sha256"]) != current
            or not find.fingerprints.matches(path, reference["sha256"])):
        raise ValueError(f"Stale mapping fingerprint: {path}")
    return reference


def provenance() -> dict:
    return {"name": "continuity-then-recency", "version": 1,
            "code_sha256": find.sha256(Path(__file__)),
            "formula": "score = continuity_sentences * (take_count + 1) + recency_rank",
            "recency": "one-based rank of (numeric recording number, transcript.word_start)",
            "quality_weight": 0}


def load_mapping(project: Path, path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected a mapping object: {path}")
    if document.get("schema") == find.SCHEMA:
        if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
            raise ValueError("Unsupported Find Takes schema version.")
        document = dict(document, schema=find.mapping.SCHEMA, schema_version=3,
                        mapping_name=path.stem, version=1)
    # Validate saved snapshots and ranges, retaining reviewed quality labels.
    # Actual source snapshots are checked by check_mapping in the stage runner.
    document = find.mapping.validate_document(project, document, verify_inputs=False)
    if document["schema_version"] == 2:
        document = dict(document, script=document["sources"]["script"],
                        script_groupings=document["groupings"]["script"],
                        recordings=[{"transcript": document["sources"]["transcript"],
                                     "links": document["links"]}])
    return document


def score_document(document: dict, mapping_reference: dict) -> dict:
    """Pure scoring over validated, recording-local ranges; labels are not rescored."""
    positions = {find.mapping.range_key(group): i
                 for i, group in enumerate(document["script_groupings"])}
    takes, recording_numbers = [], set()
    for recording in document["recordings"]:
        source = recording["transcript"]["source"]
        match = re.search(r"__C([0-9]+)__lav\.json$", source["path"], re.IGNORECASE)
        if not match:
            raise ValueError(f"Cannot determine recording number: {source['path']}")
        number = int(match[1])
        if number in recording_numbers:
            raise ValueError(f"Ambiguous recording chronology: duplicate C{number}.")
        recording_numbers.add(number)
        run = []

        def finish_run() -> None:
            for take in run:
                take["continuity_sentences"] = len(run)
            run.clear()

        for link in sorted(recording["links"], key=lambda item: item["transcript"]["word_start"]):
            take = {"source_mapping": deepcopy(mapping_reference),
                    "script_source": deepcopy(document["script"]["source"]),
                    "transcript_source": deepcopy(source),
                    "recording_number": number,
                    "script": deepcopy(link["script"]),
                    "transcript": deepcopy(link["transcript"]),
                    "classification": link["classification"], "continuity_sentences": 0}
            # Identity includes mapping version/path/hash, source, and both ranges.
            identity = {key: take[key] for key in
                        ("source_mapping", "script_source", "transcript_source", "script", "transcript")}
            take["take_key"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            takes.append(take)
            if link["classification"] == "flub":
                finish_run()
                continue
            if link["classification"] not in ("exact", "partial"):
                raise ValueError(f"Unsupported classification: {link['classification']}")
            if run:
                previous = run[-1]
                adjacent_script = (positions[find.mapping.range_key(take["script"])] ==
                                   positions[find.mapping.range_key(previous["script"])] + 1)
                adjacent_transcript = (take["transcript"]["word_start"] ==
                                       previous["transcript"]["word_end"] + 1)
                if not (adjacent_script and adjacent_transcript):
                    finish_run()
            run.append(take)
        finish_run()
    if not takes:
        raise ValueError("An empty take set cannot complete Take Selection.")
    takes.sort(key=lambda take: (take["recording_number"], take["transcript"]["word_start"]))
    count = len(takes)
    for rank, take in enumerate(takes, 1):
        take["recency_rank"] = rank
        take["score"] = take["continuity_sentences"] * (count + 1) + rank
    result = {"schema": "studio.take-selection", "schema_version": 1,
              "algorithm": provenance(), "source_mapping": deepcopy(mapping_reference),
              "take_count": count, "score_stride": count + 1, "takes": takes}
    validate_scores(document, result)
    return result


def validate_scores(document: dict, result: dict) -> None:
    expected = {(r["transcript"]["source"]["path"],
                 *find.mapping.range_key(link["script"]), *find.mapping.range_key(link["transcript"]))
                for r in document["recordings"] for link in r["links"]}
    actual = [(t["transcript_source"]["path"], *find.mapping.range_key(t["script"]),
               *find.mapping.range_key(t["transcript"])) for t in result["takes"]]
    if (not expected or len(expected) != len(actual) or set(actual) != expected
            or result["take_count"] != len(actual)):
        raise ValueError("Every input take must have exactly one scored output.")
    if any(type(t.get("score")) is not int for t in result["takes"]):
        raise ValueError("Every input take must have a finite integer score.")


def validate_manifest(project: Path, path: Path) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    for section in ("inputs", "outputs", "dependencies"):
        for item in manifest.get(section, []):
            root = ROOT if item.get("scope") == "workflow" else project
            # The shared root skill is deliberately not a completion dependency.
            if item["path"].replace("\\", "/") == ".agents/skills/studio/SKILL.md":
                continue
            target = (root / item["path"]).resolve()
            if not target.is_relative_to(root) or not find.fingerprints.matches(target, item["sha256"]):
                raise ValueError(f"Stale completion dependency: {target}")


def run_stage(project: Path, inputs: list[Path], output_dir: Path | None = None, regenerate=False) -> dict:
    project = project.resolve(strict=True)
    directory = output_dir.resolve() if output_dir else project / STAGE
    isolated = directory != project / STAGE
    if regenerate and isolated:
        raise ValueError("Regeneration is only supported for project stage outputs, not isolated trials.")
    for upstream in (project / find.APPROVED.parent, project / find.TRANSCRIBED,
                     project / find.OUTPUT):
        if directory == upstream or upstream in directory.parents:
            raise ValueError("Output directory cannot be inside an upstream input folder.")
    if not isolated:
        require_prerequisites(project, ROOT, "3.4")
    find.append_log(directory, "Take Selection started", [
        f"UTC: {datetime.now(timezone.utc).isoformat()}; isolated: {isolated}.",
        "Rules: " + json.dumps(provenance()),
    ])
    failed = (3, 4)
    try:
        inventory = find.discover(project)
        expected, sources = [], []
        for script_path, transcript_paths in inventory:
            versions = find.mapping.takes_versions(project, script_path.stem + "__Takes")
            if not versions or sum(v == versions[-1][0] for v, _ in versions) != 1:
                raise ValueError(f"Missing or ambiguous latest Takes version: {script_path.stem}")
            path = versions[-1][1].resolve()
            expected.append(path)
            sources.extend((script_path, *transcript_paths))
        supplied = [((project / p) if not p.is_absolute() else p).resolve(strict=True) for p in inputs]
        failed = (3, 4)
        if len(set(supplied)) != len(supplied) or set(supplied) != set(expected):
            raise ValueError("Pass exactly the latest numbered mapping for every approved script.")
        planned = []
        for (script_path, transcript_paths), path in zip(inventory, expected):
            failed = (3, 3)
            check = find.check_mapping(project, path, find.sources.load_script(project, script_path),
                                       [find.sources.load_transcript(project, p) for p in transcript_paths])
            if not check["coverage"]["complete"]:
                raise ValueError(f"Find Takes mapping no longer satisfies 3.3 coverage: {path}")
            failed = (3, 4)
            document = load_mapping(project, path)
            reference = fingerprint(path, project, "project")
            target = directory / (script_path.stem + "__Scores.v1.json")
            if target.exists() and not regenerate:
                previous = json.loads(target.read_text(encoding="utf-8-sig"))
                reference = checked_reference(project, path, previous["source_mapping"])
            result = score_document(document, reference)
            planned.append((target, result))
        failed = (3, 4)
        # Check all existing results before writing any. Preserve edits/stale results.
        changed = False
        for target, result in planned:
            try:
                differs = not target.exists() or json.loads(target.read_text(encoding="utf-8-sig")) != result
            except (ValueError, TypeError):
                if not regenerate:
                    raise
                differs = True
            changed = changed or differs
            if not regenerate and target.exists() and differs:
                raise ValueError(f"Existing scores differ; inspect before replacing: {target}")
        dependencies = [Path(__file__), Path(__file__).parents[1] / "SKILL.md", FIND_SCRIPT,
                         Path(__file__).parents[1] / "dependencies.json",
                         Path(__file__).with_name("validation.py"), ROOT / "scripts/validation.py",
                         ROOT / "scripts/stage_completion.py", ROOT / "scripts/stage_invalidation.py",
                         ROOT / "scripts/append-log.py", ROOT / "studio/fingerprints.py"]
        dependencies.extend(ROOT / "studio/tools/line" / name for name in
                            ("mapping.py", "sources.py", "combined.py", "quality.py", "actions.py", "boundaries.py"))
        manifest = {"project": project.name, "step": "3.4", "algorithm": provenance(),
                    "inputs": [fingerprint(p, project, "project") for p in (*expected, *sources)],
                    "dependencies": [fingerprint(p, ROOT, "workflow") for p in dependencies],
                    "conditions": {"latest_mapping_paths": [p.relative_to(project).as_posix() for p in expected],
                                   "every_take_scored": True,
                                   "take_count": sum(r["take_count"] for _, r in planned)}}
        marker = directory / "DONE"
        if not isolated and marker.exists() and not (regenerate and changed):
            old = json.loads(marker.read_text(encoding="utf-8-sig"))
            comparable = {k: v for k, v in old.items() if k not in ("outputs", "completed_at")}
            if comparable != manifest and not same_manifest(comparable, manifest, project, ROOT,
                                                             sections=("dependencies", "inputs")):
                find.invalidate(project, (3, 4), "Take Selection inputs or scoring rules changed.")
            else:
                validate_manifest(project, marker)
        if regenerate and changed:
            from scripts.stage_invalidation import prepare_regeneration
            prepare_regeneration(project, ROOT, "3.4", [p for p, _ in planned if p.exists()],
                                 "Explicit Take Selection regeneration.", append_log=find.append_log)
        for target, result in planned:
            if not target.exists():
                find.write_json(target, result)
            # Verify the persisted artifact, not just the in-memory calculation.
            if json.loads(target.read_text(encoding="utf-8-sig")) != result:
                raise ValueError(f"Score output verification failed: {target}")
        if not isolated:
            manifest["outputs"] = [fingerprint(p, project, "project") for p, _ in planned]
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            record_done(project, ROOT, "3.4", manifest)
        report = {"complete": True, "done_written": not isolated,
                  "take_count": manifest["conditions"]["take_count"],
                  "outputs": [str(p) for p, _ in planned]}
        find.append_log(directory, "Take Selection passed", [json.dumps(report),
            "Input fingerprints: " + json.dumps(manifest["inputs"]),
            "Every input take has one verified finite numerical score."])
        return report
    except (OSError, ValueError, TypeError, KeyError) as error:
        if not isolated:
            find.invalidate(project, failed, f"Take Selection validation/run failed: {error}")
        find.append_log(directory, "Take Selection failed", [str(error), "Work files preserved."])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Latest Takes JSON files, relative to project or absolute")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, help="Isolated trial; no project DONE changes")
    parser.add_argument("--regenerate", action="store_true", help="Archive selected generated scores before replacing")
    args = parser.parse_args()
    try:
        print(json.dumps(run_stage(args.project_root, args.inputs, args.output_dir, args.regenerate), indent=2))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Take Selection failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
