"""Locked DAG invalidation and explicitly owned generated-output archiving."""
from __future__ import annotations

from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from scripts import stage_completion as completion
from scripts.validation import load_stages


def _occupied(path):
    return path.exists() or path.is_symlink()


def _safe(path, root):
    """Check lexical confinement and every component, including broken links."""
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes stage root: {path}")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing symlink: {current}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Redirected stage path: {path}")


def _directory(directory):
    directory = Path(directory).absolute()
    if directory.is_symlink():
        raise ValueError(f"Refusing symlinked stage directory: {directory}")
    return directory.resolve()


def _json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError(f"Invalid archive record: {path}") from error


def read_pending(directory, step=None) -> Path | None:
    """Read only the explicit pending pointer; never guess from archive numbers."""
    directory = _directory(directory)
    stale = directory / "STALE"
    pointer = stale / "PENDING.json"
    _safe(pointer, directory)
    if not pointer.exists():
        return None
    data = _json(pointer)
    number = data.get("slot")
    identifier = data.get("step")
    if (data.get("schema") != "studio.stale.pending" or data.get("schema_version") != 1
            or type(number) is not int or number < 1 or not isinstance(identifier, str)
            or not re.fullmatch(r"(?:\d+\.\d+|stage:\d+)", identifier)
            or (step is not None and identifier != step)):
        raise ValueError(f"Invalid pending archive pointer: {pointer}")
    slot = stale / str(number)
    _safe(slot / "EVENT.json", directory)
    _safe(slot / "DONE", directory)
    if not slot.is_dir():
        raise ValueError(f"Missing pending archive slot: {slot}")
    event = _json(slot / "EVENT.json")
    if (event.get("schema") != "studio.stale.event" or event.get("schema_version") != 1
            or event.get("step") != identifier or type(event.get("had_done")) is not bool):
        raise ValueError(f"Invalid archive event: {slot}")
    artifacts = event.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError(f"Invalid archived artifact inventory: {slot}")
    for artifact in artifacts:
        completion.relative_path(artifact)
    marker = slot / "DONE"
    if (marker.exists() and not marker.is_file()) or (event["had_done"] and not marker.is_file()):
        raise ValueError(f"Invalid archived completion marker: {marker}")
    return slot


def clear_pending(directory):
    """Clear only a validated pointer, leaving every historical archive intact."""
    directory = _directory(directory)
    if read_pending(directory) is not None:
        (directory / "STALE/PENDING.json").unlink()


def _replace_json(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".archive-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_pointer(directory, step, slot):
    _replace_json(directory / "STALE/PENDING.json",
                  {"schema": "studio.stale.pending", "schema_version": 1,
                   "step": step, "slot": int(slot.name)})


def _targets(project, stages, step, include_self):
    if step not in stages:
        raise ValueError(f"Unknown stage: {step}")
    affected = {step}
    while True:
        expanded = affected | {name for name, definition in stages.items()
                               if any(item["step"] in affected
                                      for item in definition["prerequisites"])}
        if expanded == affected:
            break
        affected = expanded
    known = {project / completion.relative_path(value["folder"]): name
             for name, value in stages.items()}
    known_parents = {path.parent for path in known}
    unknown_parents = set()
    targets = {name: path for path, name in known.items()
               if name in affected and (include_self or name != step)}
    start = tuple(map(int, step.split(".")))
    for major in project.iterdir():
        match = re.match(r"^(\d+)-", major.name)
        if not match or int(match[1]) < start[0]:
            continue
        _safe(major, project)
        if not major.is_dir():
            continue
        if major not in known_parents:
            unknown_parents.add(major)
        for directory in major.iterdir():
            minor = re.match(r"^(\d+)-", directory.name)
            if not minor or directory in known:
                continue
            if int(minor[1]) == 0:
                continue  # 0-Tmp is independent preview scratch, not a workflow step.
            number = (int(match[1]), int(minor[1]))
            if number <= start:
                continue
            _safe(directory, project)
            if directory.is_dir():
                identifier = f"{number[0]}.{number[1]}"
                if identifier in stages or identifier in targets:
                    raise ValueError(f"Ambiguous numbered project folder: {directory}")
                targets[identifier] = directory
    # The caller may hold the target writer lock, but aggregation is still stale.
    parents = {path.parent for path in targets.values()} | unknown_parents
    parents.add((project / completion.relative_path(stages[step]["folder"])).parent)
    for parent in parents:
        match = re.match(r"^(\d+)-", parent.name)
        _safe(parent / "DONE", project)
        _safe(parent / "STALE/PENDING.json", project)
        if match and (_occupied(parent / "DONE") or _occupied(parent / "STALE/PENDING.json")):
            identifier = f"stage:{int(match[1])}"
            if identifier in targets and targets[identifier] != parent:
                raise ValueError(f"Ambiguous numbered project stage: {parent}")
            targets[identifier] = parent
    result = {}
    for name, directory in targets.items():
        _safe(directory, project)
        if directory.exists():
            if not directory.is_dir():
                raise ValueError(f"Expected a stage directory: {directory}")
            result[name] = directory
    return dict(sorted(result.items(), key=lambda item: item[1].as_posix()))


def _matches(path, project, rules):
    return any(path.parent == project / completion.relative_path(rule["folder"])
               and re.fullmatch(rule["filename_regex"], path.name, re.I)
               for rule in rules)


def _outputs(project, definition, paths):
    directory = project / completion.relative_path(definition["folder"])
    result = set()
    for value in paths:
        path = Path(value)
        # Relative paths are project-relative, just like definition inventories.
        path = path if path.is_absolute() else project / path
        if ".." in path.parts or "." in path.parts:
            raise ValueError(f"Noncanonical generated path: {path}")
        _safe(path, directory)
        relative = path.relative_to(directory)
        protected = {"stale", "done", "log.md", "logs", "approved"}
        if (any(part.casefold() in protected or part.casefold().startswith(".done")
                for part in relative.parts)
                or path.suffix.lower() in {".mov", ".mp4", ".wav", ".m4v", ".mkv", ".avi",
                                           ".webm", ".mxf", ".mp3", ".aiff", ".aif", ".flac",
                                           ".m4a", ".aac", ".ogg", ".wma", ".mpeg", ".mpg",
                                           ".mts", ".m2ts", ".braw", ".r3d", ".log"}
                or not _matches(path, project, definition.get("generated", []))
                or _matches(path, project, [item for item in definition.get("inputs", [])
                                           if item.get("role") != "project"])):
            raise ValueError(f"Not a stage-owned generated artifact: {path}")
        if path.exists():
            if not path.is_file():
                raise ValueError(f"Generated artifact must be a file: {path}")
            result.add(path)
    return sorted(result)


def _next_slot(directory):
    stale = directory / "STALE"
    number = (max((int(path.name) for path in stale.iterdir() if path.name.isdecimal()), default=0)
              + 1) if stale.is_dir() else 1
    while _occupied(directory / "STALE" / str(number)):
        number += 1
    slot = directory / "STALE" / str(number)
    _safe(slot, directory)
    return slot


def _plan(directory, step):
    for name in ("DONE", "STALE", "STALE/PENDING.json", "LOG.md"):
        path = directory / name
        _safe(path, directory)
        if path.exists() and (not path.is_dir() if name == "STALE" else not path.is_file()):
            raise ValueError(f"Invalid transition path: {path}")
    pending = read_pending(directory, step)
    had_done = (directory / "DONE").exists()
    if pending is not None and not had_done:
        return pending, False, False
    return _next_slot(directory), had_done, True


def _default_log(directory, title, items):
    log = directory / "LOG.md"
    if not log.exists():
        with log.open("x", encoding="utf-8"):
            pass
    spec = importlib.util.spec_from_file_location("studio_append_log", Path(__file__).with_name("append-log.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.append_entry(log, title, items, date.today())


def _transition(project, workflow, step, reason, append_log, include_self, paths=None):
    project = _directory(project)
    workflow = Path(workflow).resolve(strict=True)
    stages = load_stages(workflow)
    targets = _targets(project, stages, step, include_self)
    selected = _outputs(project, stages[step], paths) if paths is not None else []
    own = project / completion.relative_path(stages[step]["folder"])
    if paths is not None and step not in targets:
        raise ValueError(f"Regeneration requires an existing own-stage directory: {own}")
    locks, moved = [], []
    try:
        # Every lock is acquired before any marker, pointer, log, or output changes.
        for directory in targets.values():
            lock = directory / ".DONE.lock"
            _safe(lock, project)
            try:
                lock.mkdir()
            except FileExistsError as error:
                raise ValueError(f"Completion transition already active: {lock}") from error
            locks.append((lock, lock.stat()))
        plans = {name: _plan(directory, name) for name, directory in targets.items()}
        previous = None
        if paths is not None:
            # Recheck all sources under the same locks used by invalidation.
            selected = _outputs(project, stages[step], paths)
            slot = plans[step][0]
            collisions = []
            for source in selected:
                destination = slot / source.relative_to(own)
                _safe(destination, own)
                if _occupied(destination):
                    collisions.append(destination)
            if collisions and not plans[step][2]:
                event = _json(slot / "EVENT.json")
                recorded = set(event.get("artifacts", []))
                # Only a completed prior prepare proves these are our archived files.
                if all(path.is_file() and path.relative_to(slot).as_posix() in recorded
                       for path in collisions):
                    previous = slot
                    slot = _next_slot(own)
                    plans[step] = slot, False, True
            for source in selected:
                destination = slot / source.relative_to(own)
                _safe(destination, own)
                if _occupied(destination):
                    raise ValueError(f"Archive destination already occupied: {destination}")
                for parent in destination.parents:
                    if parent == slot.parent:
                        break
                    if parent.exists() and not parent.is_dir():
                        raise ValueError(f"Archive parent is not a directory: {parent}")
        for name, directory in targets.items():
            slot, had_done, fresh = plans[name]
            if fresh:
                if had_done:
                    saved = completion.archive_done(directory)
                    if saved != slot / "DONE":
                        raise ValueError(f"Unexpected completion archive slot: {saved}")
                else:
                    slot.mkdir(parents=True)
                event = {"schema": "studio.stale.event", "schema_version": 1,
                         "step": name, "had_done": had_done, "reason": reason}
                if name == step and previous is not None:
                    prior_event = _json(previous / "EVENT.json")
                    event.update(previous_slot=int(previous.name), had_done=prior_event["had_done"])
                    if event["had_done"]:
                        with (previous / "DONE").open("rb") as source, (slot / "DONE").open("xb") as destination:
                            shutil.copyfileobj(source, destination)
                            destination.flush()
                            os.fsync(destination.fileno())
                with (slot / "EVENT.json").open("x", encoding="utf-8") as stream:
                    json.dump(event, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                _write_pointer(directory, name, slot)
        if paths is not None:
            for source in selected:
                destination = plans[step][0] / source.relative_to(own)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Unlike POSIX rename(), link() cannot replace an occupied archive.
                os.link(source, destination)
                moved.append((source, destination))
                source.unlink()
        logger = append_log if append_log is not None else _default_log
        for name, directory in targets.items():
            slot = plans[name][0]
            logger(directory, "Completion invalidated", [reason,
                   f"Archive: {slot.relative_to(directory)}; "
                   + ("selected generated artifacts archived." if name == step and selected
                      else "outputs preserved.")])
        if selected:
            slot = plans[step][0]
            event = _json(slot / "EVENT.json")
            # Publish ownership only after output moves and logging have succeeded.
            event["artifacts"] = sorted(set(event.get("artifacts", []))
                                        | {path.relative_to(own).as_posix() for path in selected})
            _safe(slot / "EVENT.json", own)
            _replace_json(slot / "EVENT.json", event)
        return {name: plan[0] for name, plan in plans.items()}
    except BaseException as error:
        failures = []
        for source, destination in reversed(moved):
            try:
                # link() fails rather than overwrite a newly created current file.
                try:
                    os.link(destination, source)
                except FileExistsError:
                    # A failed source unlink left both names for our original inode.
                    if source.is_symlink() or not source.samefile(destination):
                        raise
                destination.unlink()
            except OSError as rollback_error:
                failures.append(f"{destination}: {rollback_error}")
        if failures:
            raise RuntimeError("Outputs retained for recovery in archive: " + "; ".join(failures)) from error
        raise
    finally:
        for lock, acquired in reversed(locks):
            try:
                current = lock.lstat()
                if (current.st_dev, current.st_ino) == (acquired.st_dev, acquired.st_ino):
                    lock.rmdir()
            except FileNotFoundError:
                pass


def invalidate(project, workflow, step: str, reason: str, append_log=None, *, include_self=True) -> dict[str, Path]:
    """Archive affected active markers, preserving every generated output in place."""
    return _transition(project, workflow, step, reason, append_log, include_self)


def prepare_regeneration(project, workflow, step, paths, reason, append_log=None) -> Path:
    """Invalidate the DAG and archive only explicitly selected owned artifacts."""
    paths = list(paths)
    return _transition(project, workflow, step, reason, append_log, True, paths)[step]
