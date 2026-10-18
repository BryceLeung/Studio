#!/usr/bin/env python3
"""Complete 3.2 from existing JSON transcripts without running WhisperX."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "studio/tools/line/mapping.py").is_file())
sys.path.insert(0, str(ROOT))
from studio.fingerprints import matches, matches_identity, sha256  # noqa: E402
from scripts import stage_completion as completion  # noqa: E402
from scripts.validation import require_prerequisites  # noqa: E402
DEFINITION_FILE = Path(__file__).parents[1] / "dependencies.json"
DEFINITION = completion.load_definition(DEFINITION_FILE)
STAGE = completion.relative_path(DEFINITION["folder"])
INTAKE = completion.relative_path(DEFINITION["inputs"][0]["folder"])
LOG_HELPER = ROOT / "scripts/append-log.py"
FOOTAGE_VALIDATOR = ROOT / ".agents/skills/studio/2-Production/1-Footage Intake/scripts/validate-footage.py"


def append_log(directory, title, items):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "LOG.md"
    if not log.exists():
        try:
            with log.open("x", encoding="utf-8") as stream:
                stream.write("# Stage log\n")
        except FileExistsError:
            pass
    spec = importlib.util.spec_from_file_location("studio_transcribe_log", LOG_HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.append_entry(log, title, items, datetime.now(timezone.utc).date())


def invalidate(project, reason):
    return completion.invalidate_from(project, (3, 2), reason, append_log)


def inventory(project):
    spec = importlib.util.spec_from_file_location("studio_transcribe_validation", Path(__file__).with_name("validation.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.Validation(project, ROOT, DEFINITION).inventory()


def manifest(project, wavs, outputs):
    dependencies = [Path(__file__), Path(__file__).parents[1] / "SKILL.md", FOOTAGE_VALIDATOR, LOG_HELPER,
                    ROOT / "studio/fingerprints.py", ROOT / "scripts/stage_completion.py", DEFINITION_FILE,
                    Path(__file__).with_name("validation.py"), ROOT / "scripts/validation.py",
                    ROOT / "scripts/stage_invalidation.py"]
    return {"step": "3.2", "schema": "studio.transcribe.done", "schema_version": 1,
            "dependencies": [completion.fingerprint(p, ROOT, "workflow") for p in dependencies],
            "inputs": [completion.fingerprint(p, project, "project") for p in wavs],
            "outputs": [completion.fingerprint(p, project, "project") for p in outputs],
            "conditions": {"lav_wavs": [p.relative_to(project).as_posix() for p in wavs],
                           "json_transcripts": [p.relative_to(project).as_posix() for p in outputs]}}


def run(project):
    project = project.resolve(strict=True)
    directory = project / STAGE
    if directory.is_symlink() or not directory.resolve().is_relative_to(project):
        raise ValueError(f"Refusing stage folder outside project: {directory}")
    require_prerequisites(project, ROOT, "3.2")
    append_log(directory, "Transcribe completion check started", ["Checking existing JSON; WhisperX will not run."])
    marker = directory / "DONE"
    stale_path = directory / "Stale Transcripts.json"
    try:
        wavs, outputs = inventory(project)
        current = manifest(project, wavs, outputs)
        previous = json.loads(marker.read_text(encoding="utf-8-sig")) if marker.exists() else None
        archived = completion.latest_archived_done(directory) if previous is None else None
        baseline = previous if previous is not None else (json.loads(archived.read_text(encoding="utf-8-sig"))
                                                       if archived is not None else None)
        if baseline is not None and (not isinstance(baseline, dict)
                                     or not isinstance(baseline.get("inputs"), list)
                                     or not isinstance(baseline.get("outputs"), list)):
            raise ValueError("Previous transcript DONE lacks usable provenance; review before adoption.")
        stale = json.loads(stale_path.read_text(encoding="utf-8-sig")) if stale_path.exists() else {}
        still_stale = [path for path, digest in stale.items()
                       if any(item["path"] == path and matches(project / path, digest) for item in current["outputs"])]
        if still_stale:
            raise ValueError(f"Audio changed; regenerate unchanged transcript JSON before adoption: {still_stale}")
        if completion.same_manifest(previous, current, project, ROOT):
            current = completion.record_done(project, ROOT, "3.2", current)
            append_log(directory, "Transcribe completion reused", [
                f"{len(outputs)} current JSON transcripts; audio and output fingerprints match DONE."])
            return {"status": "reused", "manifest": current}
        if previous is not None:
            invalidated = invalidate(project, "3.2 audio, transcript, or workflow fingerprints changed.")
        else:
            invalidated = []
        if baseline is not None:
            old_audio = {item["path"]: item for item in baseline.get("inputs", [])}
            old_json = {item["path"]: item["sha256"] for item in baseline.get("outputs", [])}
            unchanged_stale = {}
            for audio, output in zip(wavs, outputs):
                source = audio.relative_to(project).as_posix()
                dest = output.relative_to(project).as_posix()
                if (source not in old_audio or not matches_identity(audio, old_audio[source])) and dest in old_json and matches(output, old_json[dest]):
                    unchanged_stale[dest] = sha256(output)
            if unchanged_stale:
                stale_path.write_text(json.dumps(unchanged_stale, indent=2) + "\n", encoding="utf-8")
                raise ValueError(f"Intake audio changed but transcript was not regenerated: {sorted(unchanged_stale)}")
        current = completion.record_done(project, ROOT, "3.2", current)
        if stale_path.exists():
            stale_path.unlink()
        append_log(directory, "Transcribe completion recorded", [
            f"{len(wavs)} non-empty lav WAV files each have a valid JSON transcript.",
            "Recorded current WAV media identity and JSON hash without running WhisperX; existing files preserved.",
            f"Invalidated markers: {invalidated}."])
        return {"status": "adopted", "manifest": current}
    except (OSError, ValueError, TypeError, KeyError) as error:
        deleted = invalidate(project, f"Transcribe 3.2 failed: {error}")
        append_log(directory, "Transcribe completion blocked", [str(error), f"Invalidated markers: {deleted}."])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.project_root)
        print(json.dumps({"status": result["status"], "transcripts": len(result["manifest"]["outputs"])}, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Transcribe completion: {error}\n")


if __name__ == "__main__":
    main()
