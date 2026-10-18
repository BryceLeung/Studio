"""Shared stage definitions, portable fingerprints, and completion-marker archiving."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    # Invalidation imports this module again; nested guards must share ownership.
    sys.modules["scripts.stage_completion"] = sys.modules[__name__]

from studio import fingerprints


_mutation_state = threading.local()


@contextmanager
def mutation_guard(project):
    """Serialize local project mutations, allowing nested transitions in one thread."""
    project = Path(project).resolve(strict=True)
    key = (os.getpid(), str(project))
    held = getattr(_mutation_state, "held", None)
    if held is None:
        held = _mutation_state.held = set()
    if key in held:
        yield
        return
    path = project / ".studio-mutations.lock"
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked mutation lock: {path}")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"Mutation lock must be a regular file: {path}")
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        held.add(key)
        yield
    finally:
        held.discard(key)
        if locked:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"Invalid stage path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..") for part in value.split("/")):
        raise ValueError(f"Stage path must be root-relative: {value!r}")
    return Path(*path.parts)


def require_idle_jobs(directory):
    """Job exclusion is a lifecycle gate, including during manual overrides."""
    jobs = Path(directory) / ".SYNC-JOBS"
    if jobs.is_symlink() or (jobs.exists() and (not jobs.is_dir() or any(jobs.iterdir()))):
        raise ValueError(f"Sync jobs are active or interrupted; inspect {jobs} before completion")


def load_definition(file):
    definition = json.loads(Path(file).read_text(encoding="utf-8"))
    if definition.get("schema") != "studio.stage" or definition.get("schema_version") != 1:
        raise ValueError(f"Unsupported stage definition: {file}")
    if not re.fullmatch(r"\d+\.\d+", definition["step"]):
        raise ValueError(f"Invalid stage ID: {definition['step']}")
    if "requires_stage_check" in definition and not isinstance(definition["requires_stage_check"], bool):
        raise ValueError(f"Invalid stage-check flag: {file}")
    if "validation" in definition:
        policy = definition["validation"]
        if not isinstance(policy, dict) or set(policy) not in ({"script"}, {"skip_reason"}):
            raise ValueError(f"Invalid validation policy: {file}")
        if "script" in policy:
            relative_path(policy["script"])
        elif not isinstance(policy["skip_reason"], str) or not policy["skip_reason"].strip():
            raise ValueError(f"Missing validation skip reason: {file}")
    if "completion" in definition and definition["completion"] != {"stage_owned": True}:
        raise ValueError(f"Invalid completion policy: {file}")
    relative_path(definition["folder"])
    if not isinstance(definition.get("workflow_dependencies", []), list):
        raise ValueError(f"Workflow dependencies must be a list: {file}")
    for dependency in definition.get("workflow_dependencies", []):
        relative_path(dependency)
    for item in definition["prerequisites"]:
        if not re.fullmatch(r"\d+\.\d+", item["step"]):
            raise ValueError(f"Invalid prerequisite ID: {item['step']}")
        relative_path(item["folder"])
    for inventory in definition["inputs"] + definition["outputs"]:
        relative_path(inventory["folder"])
        re.compile(inventory["filename_regex"], re.I)
        if not isinstance(inventory["minimum"], int) or inventory["minimum"] < 0:
            raise ValueError(f"Invalid inventory minimum: {file}")
        if "maximum" in inventory and (not isinstance(inventory["maximum"], int)
                                       or inventory["maximum"] < inventory["minimum"]):
            raise ValueError(f"Invalid inventory maximum: {file}")
        if "complete_set" in inventory and not isinstance(inventory["complete_set"], bool):
            raise ValueError(f"Invalid complete_set flag: {file}")
        if "role" in inventory and inventory["role"] != "project":
            raise ValueError(f"Unsupported inventory role: {file}")
    if not isinstance(definition.get("override_watches", []), list):
        raise ValueError(f"Override watches must be a list: {file}")
    for inventory in definition.get("override_watches", []):
        relative_path(inventory["folder"])
        re.compile(inventory["filename_regex"], re.I)
        if type(inventory.get("minimum", 0)) is not int or inventory.get("minimum", 0) < 0:
            raise ValueError(f"Invalid override watch minimum: {file}")
        if "maximum" in inventory and (type(inventory["maximum"]) is not int
                                       or inventory["maximum"] < inventory.get("minimum", 0)):
            raise ValueError(f"Invalid override watch maximum: {file}")
    if not isinstance(definition.get("generated", []), list):
        raise ValueError(f"Generated artifact policies must be a list: {file}")
    for inventory in definition.get("generated", []):
        folder = relative_path(inventory["folder"])
        step_folder = relative_path(definition["folder"])
        if not folder.is_relative_to(step_folder):
            raise ValueError(f"Generated artifacts must belong to their stage: {file}")
        if any(part.casefold() in ("stale", "approved", ".done.lock") for part in folder.relative_to(step_folder).parts):
            raise ValueError(f"Generated policy includes a protected folder: {file}")
        re.compile(inventory["filename_regex"], re.I)
    return definition


def fingerprint(path, root, scope):
    root = Path(root).resolve(strict=True)
    path = Path(path).resolve(strict=True)
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError(f"Fingerprint outside {scope} root: {path}")
    return {"scope": scope, "path": path.relative_to(root).as_posix(),
            **fingerprints.identity(path)}


def matches_entry(entry, project, workflow):
    root = {"project": project, "workflow": workflow}.get(entry.get("scope"))
    if root is None:
        raise ValueError(f"Unknown fingerprint scope: {entry.get('scope')}")
    root = Path(root).resolve(strict=True)
    candidate = (root / relative_path(entry["path"])).resolve()
    return (candidate.is_relative_to(root) and candidate.is_file()
            and fingerprints.matches_identity(candidate, entry))


def same_manifest(previous, current, project, workflow, *, sections=("dependencies", "inputs", "outputs")):
    if not isinstance(previous, dict):
        return False
    normalized = dict(previous)
    for section in sections:
        if not isinstance(previous.get(section), list):
            return False
        entries = previous[section]
        if len(entries) != len(current[section]):
            return False
        if any(not matches_entry(item, project, workflow) for item in entries):
            return False
        normalized[section] = [dict(item, **{key: value for key, value in fresh.items()
                                            if key in ("method", "sha256", "size_bytes", "mtime_s")})
                               for item, fresh in zip(entries, current[section])]
    return normalized == current


def archive_done(directory, *, retain_active=False):
    """Move an active marker to the next free numbered archive; never replace one."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError(f"Refusing symlinked stage folder: {directory}")
    marker = directory / "DONE"
    if marker.is_symlink():
        raise ValueError(f"Refusing symlinked completion marker: {marker}")
    if not marker.is_file():
        return None
    stale = directory / "STALE"
    if stale.is_symlink():
        raise ValueError(f"Refusing symlinked completion archive: {stale}")
    stale.mkdir(exist_ok=True)
    number = max((int(path.name) for path in stale.iterdir() if path.name.isdecimal()), default=0) + 1
    while True:
        slot = stale / str(number)
        try:
            slot.mkdir()
            break
        except FileExistsError:
            number += 1
    if retain_active:
        with marker.open("rb") as source, (slot / "DONE").open("xb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        shutil.copystat(marker, slot / "DONE")
    else:
        os.link(marker, slot / "DONE")
        marker.unlink()
    return slot / "DONE"


def latest_archived_done(directory):
    """Return the most recent archived completion record, if one exists."""
    stale = Path(directory) / "STALE"
    if stale.is_symlink():
        raise ValueError(f"Refusing symlinked completion archive: {stale}")
    if not stale.is_dir():
        return None
    versions = []
    for slot in stale.iterdir():
        if not slot.name.isdecimal():
            continue
        if slot.is_symlink() or (slot / "DONE").is_symlink():
            raise ValueError(f"Refusing symlinked archived completion: {slot}")
        if slot.is_dir() and (slot / "DONE").is_file():
            versions.append((int(slot.name), slot / "DONE"))
    versions.sort()
    return versions[-1][1] if versions else None


def invalidate_from(project, step, reason, append_log):
    """Compatibility delegate; dependency JSON, not numeric order, determines impact."""
    from scripts.stage_invalidation import invalidate
    step = ".".join(map(str, step)) if isinstance(step, tuple) else step
    slots = invalidate(project, ROOT, step, reason, append_log)
    return [(slot / "DONE").relative_to(project).as_posix() for slot in slots.values()
            if (slot / "DONE").is_file()]


def build_manifest(project, workflow, step):
    """Build ordinary inventory evidence; complex stages supply their own conditions."""
    from scripts.validation import load_stages
    project, workflow = Path(project).resolve(strict=True), Path(workflow).resolve(strict=True)
    definition = load_stages(workflow)[step]
    if definition.get("completion", {}).get("stage_owned"):
        raise ValueError(f"Stage {step} requires its normal checker/generator or stage-owned --evidence, not inventory-only recording")
    manifest = {"schema": "studio.done", "schema_version": 1, "step": step,
                "completed_at": datetime.now(timezone.utc).isoformat()}
    for section in ("inputs", "outputs"):
        paths = set()
        for inventory in definition[section]:
            folder = (project / relative_path(inventory["folder"])).resolve()
            if not folder.is_relative_to(project):
                raise ValueError(f"Inventory escapes project: {folder}")
            if folder.is_dir():
                paths.update(path for path in folder.iterdir() if path.is_file()
                             and re.fullmatch(inventory["filename_regex"], path.name, re.I))
        manifest[section] = [fingerprint(path, project, "project") for path in sorted(paths)]
    stage = workflow / ".agents/skills/studio" / relative_path(definition["folder"])
    dependencies = {stage / "SKILL.md", stage / "dependencies.json", workflow / "scripts/validation.py",
                    workflow / "scripts/stage_completion.py", workflow / "scripts/stage_invalidation.py",
                    workflow / "studio/fingerprints.py"}
    dependencies.update(path for path in (stage / "scripts").glob("*.py") if not path.name.startswith("test_"))
    dependencies.update((stage / "scripts").glob("requirements*.txt"))
    dependencies.update(workflow / relative_path(path) for path in definition.get("workflow_dependencies", []))
    manifest["dependencies"] = [fingerprint(path, workflow, "workflow") for path in sorted(dependencies)]
    return manifest


def record_done(project, workflow, step, manifest):
    """Validate evidence before atomically committing completion; never generate outputs."""
    if isinstance(manifest, dict) and manifest.get("completion_mode") == "manual-override":
        raise ValueError("Manual override evidence requires the explicit preview/confirmation flow")
    return _record_done(project, workflow, step, manifest)


def _record_manual_override(project, workflow, step, manifest):
    """Internal publication path used only after override confirmation and recheck."""
    if not isinstance(manifest, dict) or manifest.get("completion_mode") != "manual-override":
        raise ValueError("Expected explicit manual override evidence")
    return _record_done(project, workflow, step, manifest, manual_override=True)


def _record_done(project, workflow, step, manifest, *, manual_override=False):
    with mutation_guard(project):
        return _record_done_locked(project, workflow, step, manifest, manual_override=manual_override)


def _record_done_locked(project, workflow, step, manifest, *, manual_override=False):
    from scripts.validation import load_stages, recorded_fingerprints, require_prerequisites, validate_evidence
    from scripts.stage_invalidation import clear_pending, invalidate, read_all_pending
    project, workflow = Path(project).resolve(strict=True), Path(workflow).resolve(strict=True)
    definition = load_stages(workflow)[step]
    require_idle_jobs(project / relative_path(definition["folder"]))
    require_prerequisites(project, workflow, step)
    if not isinstance(manifest, dict):
        raise ValueError("Completion evidence must be a JSON object")
    if manifest.get("step") != step:
        raise ValueError(f"Completion evidence must identify stage {step}")
    if any(not isinstance(manifest.get(section), list) for section in ("inputs", "outputs", "dependencies")):
        raise ValueError("New completion evidence requires input, output, and dependency lists")
    stage = Path(".agents/skills/studio") / relative_path(definition["folder"])
    required = {stage / "SKILL.md", stage / "dependencies.json", Path("scripts/validation.py"),
                Path("scripts/stage_completion.py"), Path("scripts/stage_invalidation.py"), Path("studio/fingerprints.py")}
    if "script" in definition.get("validation", {}):
        required.add(stage / relative_path(definition["validation"]["script"]))
    required.update(relative_path(path) for path in definition.get("workflow_dependencies", []))
    saved = {item["path"] for item in recorded_fingerprints({"dependencies": manifest.get("dependencies")})
             if item.get("scope") == "workflow"}
    missing = {path.as_posix() for path in required} - saved
    if missing:
        raise ValueError(f"Completion evidence lacks workflow dependencies: {sorted(missing)}")
    status, reason = validate_evidence(project, workflow, definition, manifest)
    if status != "current":
        raise ValueError(f"Cannot complete {step}: {status}: {reason}")
    directory = project / relative_path(definition["folder"])
    marker = directory / "DONE"
    if not directory.resolve().is_relative_to(project) or directory.is_symlink() or marker.is_symlink():
        raise ValueError(f"Refusing redirected completion marker: {marker}")
    lock = directory / ".DONE.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ValueError(f"Completion recording is already active or interrupted: inspect {lock}") from error
    acquired_lock = lock.lstat()
    temporary = None
    published = False
    try:
        read_all_pending(directory, step)
        status, reason = validate_evidence(project, workflow, definition, manifest)
        if status != "current":
            raise ValueError(f"Cannot complete {step}: {status}: {reason}")
        previous_bytes = marker.read_bytes() if marker.exists() else None
        if manual_override:
            prior = manifest["override"]["prior_done"]
            prior_path = project / relative_path(prior["path"])
            from scripts.stage_invalidation import _safe
            _safe(prior_path, project)
            if fingerprints.raw_sha256(prior_path).lower() != prior["sha256"].lower():
                raise ValueError("Prior completion changed during override recording")
            if previous_bytes is not None and hashlib.sha256(previous_bytes).hexdigest() != prior["sha256"].lower():
                raise ValueError("Active completion changed during override recording")
        if previous_bytes is not None:
            try:
                previous = json.loads(previous_bytes.decode("utf-8-sig"))
                old = {key: value for key, value in previous.items() if key != "completed_at"}
                new = {key: value for key, value in manifest.items() if key != "completed_at"}
                if not manual_override and (old == new or same_manifest(old, new, project, workflow)):
                    clear_pending(directory)
                    return previous
            except (ValueError, UnicodeError, AttributeError):
                pass
        if manual_override and previous_bytes is not None:
            saved_prior = archive_done(directory, retain_active=True)
            if fingerprints.raw_sha256(saved_prior).lower() != manifest["override"]["prior_done"]["sha256"].lower():
                raise ValueError("Archived completion differs from the confirmed prior record")
            manifest["override"]["prior_done"]["path"] = saved_prior.relative_to(project).as_posix()
        payload = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        change_reason = (f"Manual override of {step} by {manifest['override']['actor']}: {manifest['override']['reason']}"
                         if manual_override else f"Stage {step} completion evidence changed or was newly established.")
        invalidate(project, workflow, step, change_reason, include_self=False)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".DONE-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        actual = marker.read_bytes() if marker.exists() else None
        if actual != previous_bytes:
            raise ValueError("DONE changed during completion recording; retry validation")
        if previous_bytes is not None and not manual_override:
            archive_done(directory, retain_active=True)
        current_lock = lock.lstat()
        if (current_lock.st_dev, current_lock.st_ino) != (acquired_lock.st_dev, acquired_lock.st_ino):
            raise ValueError("Completion lock ownership changed; marker publication refused")
        os.replace(temporary, marker)
        published = True
        clear_pending(directory)
    except Exception as error:
        error.completion_published = published
        raise
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            try:
                current_lock = lock.lstat()
                if (current_lock.st_dev, current_lock.st_ino) == (acquired_lock.st_dev, acquired_lock.st_ino):
                    lock.rmdir()
            except FileNotFoundError:
                pass
        except Exception as error:
            error.completion_published = published
            raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Shared Studio completion, invalidation, and deliberate regeneration transitions.")
    parser.add_argument("action", choices=["record", "invalidate", "prepare-regeneration"])
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--evidence", type=Path, help="Stage-owned manifest JSON; omit for ordinary inventory stages")
    parser.add_argument("--reason", help="Required explanation for invalidation or regeneration")
    parser.add_argument("--file", action="append", type=Path, default=[], help="Exact generated artifact to replace; repeat as needed")
    args = parser.parse_args()
    if args.action != "record" and (not args.reason or not args.reason.strip()):
        parser.error("--reason is required for invalidation and regeneration")
    if args.action != "record" and args.evidence:
        parser.error("--evidence is only valid for recording")
    if args.action != "prepare-regeneration" and args.file:
        parser.error("--file is only valid for prepare-regeneration")
    try:
        if args.action == "record":
            manifest = (json.loads(args.evidence.read_text(encoding="utf-8-sig")) if args.evidence
                        else build_manifest(args.project_root, ROOT, args.stage))
            saved = record_done(args.project_root, ROOT, args.stage, manifest)
            result = {"status": "complete", "step": args.stage, "manifest": saved}
        else:
            from scripts.stage_invalidation import invalidate, prepare_regeneration
            project = args.project_root.resolve(strict=True)
            if args.action == "invalidate":
                slots = invalidate(project, ROOT, args.stage, args.reason)
                result = {"status": "invalidated", "step": args.stage,
                          "archives": {step: slot.relative_to(project).as_posix() for step, slot in slots.items()}}
            else:
                paths = [path if path.is_absolute() else project / relative_path(path.as_posix()) for path in args.file]
                slot = prepare_regeneration(project, ROOT, args.stage, paths, args.reason)
                result = {"status": "prepared", "step": args.stage, "archive": slot.relative_to(project).as_posix()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f"Stage transition failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
