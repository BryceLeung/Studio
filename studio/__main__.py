"""Launch the native dashboard or call the same actions from a terminal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Optional

from .actions import ActionLogError, available_actions, invoke
from .project import Project, discover_projects
from .tools import register_actions


def resolve_project(value: str) -> Project:
    path = Path(value).expanduser()
    if path.exists():
        return Project(path)
    matches = [
        candidate for candidate in discover_projects(Path(__file__).resolve().parent.parent)
        if candidate.name == value
    ]
    if len(matches) == 1:
        return Project(matches[0])
    if len(matches) > 1:
        raise ValueError("Several OneDrive roots contain that project; pass its full path.")
    raise ValueError(f"Studio project not found: {value}")


def call(action_name: str, project: Project, /, *args: Any, **kwargs: Any) -> Any:
    try:
        return invoke(action_name, project, *args, **kwargs)
    except ActionLogError as warning:
        print(str(warning), file=sys.stderr)
        return warning.result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Studio native dashboard and shared actions")
    commands = parser.add_subparsers(dest="command", required=True)
    gui = commands.add_parser("gui", help="Launch the native desktop dashboard")
    gui.add_argument("--project", help="Project folder path or exact discovered name")
    commands.add_parser("projects", help="List available OneDrive projects")
    commands.add_parser("actions", help="List registered actions as JSON")
    action = commands.add_parser("action", help="Invoke a Line! action with temporary in-memory state")
    action.add_argument("name", help="Registered action name, e.g. line.link")
    action.add_argument("--project", required=True)
    action.add_argument("--mapping", help="Existing common mapping JSON to load")
    action.add_argument("--script", help="Script input, relative to project or full path")
    action.add_argument("--transcript", help="Transcript JSON, relative to project or full path")
    action.add_argument("--script-line", type=int, help="Select a script grouping (numbered from 1)")
    action.add_argument("--transcript-line", type=int, help="Select a transcript grouping (numbered from 1)")
    action.add_argument("--args", default="{}", help="JSON object containing action keyword arguments")
    action.add_argument("--save", metavar="NAME", help="Save the result as a new mapping version")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"gui", "projects", "actions", "action", "-h", "--help"}:
        arguments.insert(0, "gui")
    parser = _parser()
    args = parser.parse_args(arguments)
    register_actions()
    try:
        if args.command == "projects":
            paths = discover_projects(Path(__file__).resolve().parent.parent)
            print(json.dumps([{"name": path.name, "path": str(path)} for path in paths], indent=2))
            return 0
        if args.command == "actions":
            print(json.dumps(available_actions(), indent=2))
            return 0
        if args.command == "gui":
            from .dashboard import launch
            return launch(resolve_project(args.project).root if args.project else None)

        if not args.name.startswith("line.") or args.name not in available_actions():
            raise ValueError("The action command expects a registered line.* action. Use ‘actions’ to list them.")
        parameters = json.loads(args.args)
        if not isinstance(parameters, dict):
            raise ValueError("--args must be a JSON object.")
        if any(name in parameters for name in ("project", "state", "_lock")):
            raise ValueError("--args may contain only the action's own parameters.")
        project = resolve_project(args.project)
        from .tools.line.mapping import new_state
        state = new_state()
        lock = call("dashboard.open", project)
        try:
            if args.mapping:
                call("line.load_mapping", project, state, path=args.mapping, _lock=lock)
            if args.script:
                call("line.load_script", project, state, path=args.script, _lock=lock)
            if args.transcript:
                call("line.load_transcript", project, state, path=args.transcript, _lock=lock)
            if args.script_line is not None:
                call("line.jump", project, state, panel="script", line=args.script_line, _lock=lock)
            if args.transcript_line is not None:
                call("line.jump", project, state, panel="transcript", line=args.transcript_line, _lock=lock)
            result = call(args.name, project, state, _lock=lock, **parameters)
            if args.save:
                call("line.save", project, state, name=args.save, _lock=lock)
            print(json.dumps({
                "action": args.name,
                "result": result if not isinstance(result, dict) else None,
                "groupings": state["groupings"], "links": state["links"], "discarded": state["discarded"],
                "mapping_path": state["mapping_path"], "dirty": state["dirty"],
            }, ensure_ascii=False, indent=2))
        finally:
            call("dashboard.close", project, lock)
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, TypeError) as error:
        print(f"Studio: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
