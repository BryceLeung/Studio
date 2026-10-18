"""Remove only explicitly selected Studio STALE archive directories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil


NUMBERED = re.compile(r"([1-9]\d*)-.+")
SCOPE = re.compile(r"[1-9]\d*(?:\.[1-9]\d*)?")


def numbered_folders(parent):
    return sorted((p for p in parent.iterdir() if NUMBERED.fullmatch(p.name)
                   and (p.is_dir() or p.is_symlink())), key=lambda p: p.name)


def require_directory(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"Refusing linked or non-directory path: {path}")


def plan(project, scope):
    """Preflight the complete scope before any deletion, without traversing archives."""
    if scope != "all" and not SCOPE.fullmatch(scope):
        raise ValueError("Stage must be a number, stage.substage, or all")
    stage_id, _, sub_id = scope.partition(".")
    stages = numbered_folders(project)
    if scope != "all":
        stages = [p for p in stages if NUMBERED.fullmatch(p.name)[1] == stage_id]
        if not stages:
            raise ValueError(f"Stage folder not found: {stage_id}")
        if len(stages) != 1:
            raise ValueError(f"Ambiguous stage folder: {stage_id}")
    selected = []
    for stage in stages:
        require_directory(stage)
        children = numbered_folders(stage)
        if sub_id:
            children = [p for p in children if NUMBERED.fullmatch(p.name)[1] == sub_id]
            if len(children) != 1:
                raise ValueError(f"Missing or ambiguous substage folder: {scope}")
        else:
            selected.append(stage)
        for child in children:
            require_directory(child)
            selected.append(child)
    archives = []
    for directory in selected:
        archive = directory / "STALE"
        if archive.exists() or archive.is_symlink():
            require_directory(archive)
            archives.append(archive)
    return archives


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    removed = []
    try:
        project = args.project_root.resolve(strict=True)
        require_directory(project)
        archives = plan(project, args.stage)
        if not args.dry_run:
            for archive in archives:
                require_directory(archive)
                shutil.rmtree(archive)
                removed.append(archive.relative_to(project).as_posix())
        print(json.dumps({"status": "preview" if args.dry_run else "cleared",
                          "project": str(project), "stage": args.stage,
                          "selected": [p.relative_to(project).as_posix() for p in archives],
                          "removed": removed}, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "removed": removed,
                          "error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
