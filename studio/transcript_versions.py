"""Resolve explicitly committed transcript repairs without changing exact-path loads."""
from __future__ import annotations

import json
from pathlib import Path
import re

from studio.fingerprints import raw_sha256

TRANSCRIBED = Path("3-Post Production/2-Transcribe")
COMMIT = "COMMITTED.json"


def resolve_transcripts(project: Path) -> list[dict]:
    """Return one effective source per canonical lav, with immutable evidence.

    Only a final publication marker makes a repair visible. A broken committed
    version is an error, never permission to silently revert to older words.
    """
    project = Path(project).resolve(strict=True)
    directory = project / TRANSCRIBED

    def confined(path: Path) -> Path:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(project) or resolved != path.absolute():
            raise ValueError(f"Redirected transcript repair path: {path}")
        return resolved

    if not directory.is_dir():
        return []
    confined(directory)
    originals = sorted(p for p in directory.iterdir() if p.is_file()
                       and re.fullmatch(r".+__C\d{2,}__lav\.json", p.name, re.I))
    records = []
    for original in originals:
        confined(original)
        effective = original
        evidence = [original]
        known = {original: raw_sha256(original)}
        repairs = directory / "REPAIRS" / original.stem
        if repairs.exists():
            confined(repairs)
            versions = []
            for slot in repairs.iterdir():
                match = re.fullmatch(r"v([1-9]\d*)", slot.name)
                if match and ((slot / COMMIT).exists() or (slot / COMMIT).is_symlink()):
                    versions.append((int(match[1]), slot))
            for _, slot in sorted(versions):
                confined(slot)
                marker = confined(slot / COMMIT)
                candidate = confined(slot / original.name)
                try:
                    commit = json.loads(marker.read_text(encoding="utf-8"))
                    source = commit["source"]
                    reference = source["path"]
                    if (not isinstance(reference, str) or not reference or "\\" in reference
                            or Path(reference).is_absolute() or Path(reference).as_posix() != reference
                            or ".." in Path(reference).parts):
                        raise ValueError("Source path must be project-relative POSIX")
                    source_path = confined(project / reference)
                    if (commit["status"] != "success" or commit["transcript"] != original.name
                            or source_path not in known or source["sha256"] != known[source_path]
                            or commit["sha256"] != raw_sha256(candidate)):
                        raise ValueError("Publication fingerprint or provenance differs")
                    document = json.loads(candidate.read_text(encoding="utf-8-sig"))
                    if not isinstance(document, dict):
                        raise ValueError("Transcript JSON must be an object")
                except (KeyError, TypeError, UnicodeError, ValueError) as exc:
                    raise ValueError(f"Invalid committed transcript repair: {marker}: {exc}") from exc
                known[candidate] = commit["sha256"]
                effective = candidate
                evidence.extend((candidate, marker))
        records.append({"canonical": original, "effective": effective, "evidence": evidence})
    return records


def transcript_evidence(project: Path) -> list[Path]:
    return sorted({path for item in resolve_transcripts(project) for path in item["evidence"]})


def transcript_selection(project: Path) -> list[str]:
    project = Path(project).resolve(strict=True)
    return [item["effective"].relative_to(project).as_posix() for item in resolve_transcripts(project)]


def is_repair_evidence(path: Path) -> bool:
    """Recognize historical evidence layout, not arbitrary nested JSON files."""
    parts = path.parts
    base = TRANSCRIBED.parts
    return (parts[:len(base)] == base and len(parts) == len(base) + 4
            and parts[-4] == "REPAIRS" and re.fullmatch(r"v[1-9]\d*", parts[-2]) is not None
            and re.fullmatch(r".+__C\d{2,}__lav", parts[-3], re.I) is not None
            and parts[-1] in (parts[-3] + ".json", COMMIT))
