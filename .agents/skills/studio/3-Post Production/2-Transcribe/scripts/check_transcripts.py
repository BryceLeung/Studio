#!/usr/bin/env python3
"""Complete 3.2 from existing JSON transcripts without running WhisperX."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "studio/tools/line/mapping.py").is_file())
STAGE = Path("3-Post Production/2-Transcribe")
INTAKE = Path("2-Production/1-Footage Intake")
WAV = re.compile(r"^.+__C\d{2}__lav\.wav$", re.I)
LOG_HELPER = ROOT / "scripts/append-log.py"
FOOTAGE_VALIDATOR = ROOT / ".agents/skills/studio/2-Production/1-Footage Intake/scripts/validate-footage.py"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(path, root, scope):
    return {"scope": scope, "path": path.relative_to(root).as_posix(), "sha256": sha256(path)}


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
    """Invalidate 3.2 and later completion markers, preserving every work file."""
    deleted = []
    for stage in sorted(project.iterdir()):
        major = re.match(r"^(\d+)-", stage.name)
        if not stage.is_dir() or not major or int(major[1]) < 3:
            continue
        for folder in sorted(stage.iterdir()):
            minor = re.match(r"^(\d+)-", folder.name)
            if not folder.is_dir() or not minor or (int(major[1]), int(minor[1])) < (3, 2):
                continue
            marker = folder / "DONE"
            if marker.is_file():
                marker.unlink()
                deleted.append(marker.relative_to(project).as_posix())
                append_log(folder, "Completion invalidated", [reason, "Work files preserved; deleted DONE only."])
        marker = stage / "DONE"
        if marker.is_file():
            marker.unlink()
            deleted.append(marker.relative_to(project).as_posix())
            append_log(stage, "Completion invalidated", [reason, "Work files preserved; deleted DONE only."])
    return deleted


def inventory(project):
    if not (project / INTAKE / "DONE").is_file():
        raise ValueError("Footage Intake 2.1 DONE is missing; validate intake before 3.2.")
    wavs = sorted(path for path in (project / INTAKE).iterdir() if path.is_file() and WAV.fullmatch(path.name))
    if not wavs or any(path.stat().st_size == 0 for path in wavs):
        raise ValueError("No complete non-empty intake lav WAV inventory.")
    directory = project / STAGE
    expected = {path.stem + ".json": path for path in wavs}
    if len(expected) != len(wavs):
        raise ValueError("Multiple intake lav WAV files map to the same transcript name.")
    extras = sorted(path.name for path in directory.glob("*.json")
                    if path.name not in expected and path.name != "Stale Transcripts.json") if directory.is_dir() else []
    missing = [name for name in expected if not (directory / name).is_file()]
    if missing or extras:
        raise ValueError(f"Transcript inventory differs from intake: missing={missing}, extra={extras}")
    for name in expected:
        output = directory / name
        if output.stat().st_size == 0:
            raise ValueError(f"Empty transcript JSON: {output}")
        try:
            document = json.loads(output.read_text(encoding="utf-8-sig"))
        except (UnicodeError, ValueError) as error:
            raise ValueError(f"Invalid transcript JSON: {output}") from error
        if not isinstance(document, dict):
            raise ValueError(f"Transcript JSON must be an object: {output}")
    return wavs, [directory / name for name in sorted(expected)]


def manifest(project, wavs, outputs):
    dependencies = [Path(__file__), Path(__file__).parents[1] / "SKILL.md", FOOTAGE_VALIDATOR, LOG_HELPER]
    return {"step": "3.2", "schema": "studio.transcribe.done", "schema_version": 1,
            "dependencies": [fingerprint(p, ROOT, "workflow") for p in dependencies],
            "inputs": [fingerprint(p, project, "project") for p in wavs],
            "outputs": [fingerprint(p, project, "project") for p in outputs],
            "conditions": {"lav_wavs": [p.relative_to(project).as_posix() for p in wavs],
                           "json_transcripts": [p.relative_to(project).as_posix() for p in outputs]}}


def run(project):
    project = project.resolve(strict=True)
    directory = project / STAGE
    append_log(directory, "Transcribe completion check started", ["Checking existing JSON; WhisperX will not run."])
    marker = directory / "DONE"
    stale_path = directory / "Stale Transcripts.json"
    try:
        wavs, outputs = inventory(project)
        current = manifest(project, wavs, outputs)
        previous = json.loads(marker.read_text(encoding="utf-8-sig")) if marker.exists() else None
        stale = json.loads(stale_path.read_text(encoding="utf-8-sig")) if stale_path.exists() else {}
        still_stale = [path for path, digest in stale.items()
                       if any(item["path"] == path and item["sha256"] == digest for item in current["outputs"])]
        if still_stale:
            raise ValueError(f"Audio changed; regenerate unchanged transcript JSON before adoption: {still_stale}")
        if previous == current:
            append_log(directory, "Transcribe completion reused", [
                f"{len(outputs)} current JSON transcripts; audio and output fingerprints match DONE."])
            return {"status": "reused", "manifest": current}
        if previous is not None:
            invalidated = invalidate(project, "3.2 audio, transcript, or workflow fingerprints changed.")
            old_audio = {item["path"]: item["sha256"] for item in previous.get("inputs", [])}
            old_json = {item["path"]: item["sha256"] for item in previous.get("outputs", [])}
            unchanged_stale = {}
            for audio, output in zip(wavs, outputs):
                source = audio.relative_to(project).as_posix()
                dest = output.relative_to(project).as_posix()
                if old_audio.get(source) != sha256(audio) and old_json.get(dest) == sha256(output):
                    unchanged_stale[dest] = sha256(output)
            if unchanged_stale:
                stale_path.write_text(json.dumps(unchanged_stale, indent=2) + "\n", encoding="utf-8")
                raise ValueError(f"Intake audio changed but transcript was not regenerated: {sorted(unchanged_stale)}")
        else:
            invalidated = []
        marker.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        if stale_path.exists():
            stale_path.unlink()
        append_log(directory, "Transcribe completion recorded", [
            f"{len(wavs)} non-empty lav WAV files each have a valid JSON transcript.",
            "Recorded current WAV/JSON hashes without running WhisperX; existing files preserved.",
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
