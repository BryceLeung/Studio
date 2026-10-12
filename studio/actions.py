"""One action registry for dashboard controls, the CLI, and agent callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import importlib.util
import inspect
import os
from pathlib import Path
import re
import sysconfig
from typing import Any, Callable, Optional
import warnings

from .project import Project, ProjectLock


_ACTION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.!:-]*")


@dataclass(frozen=True)
class _Action:
    function: Callable[..., Any]
    description: str
    meaningful: bool


_registry: dict[str, _Action] = {}
_append_entry: Optional[Callable[..., None]] = None


class ActionError(ValueError):
    """An action name is unknown or its invocation is invalid."""


class ActionLogError(RuntimeError):
    """An operation completed but its append-only audit entry failed."""

    def __init__(self, name: str, result: Any):
        super().__init__(f"Action {name} succeeded, but its dashboard log could not be appended.")
        self.action = name
        self.result = result


def action(
    name: str,
    *,
    description: Optional[str] = None,
    meaningful: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a project-first function without wrapping its direct calls.

    Use invoke() to obtain ownership checks and audit logging. Set meaningful
    to False for frequent UI-only actions such as selection and scrolling.
    """
    if not _ACTION_NAME.fullmatch(name):
        raise ActionError(f"Invalid action name: {name!r}")

    def register(function: Callable[..., Any]) -> Callable[..., Any]:
        if name in _registry and _registry[name].function is not function:
            raise ActionError(f"Action is already registered: {name}")
        text = description if description is not None else inspect.getdoc(function) or ""
        _registry[name] = _Action(function, " ".join(text.split()), bool(meaningful))
        return function

    return register


def available_actions() -> dict[str, dict[str, Any]]:
    """Return JSON-friendly metadata without exposing registered callables."""
    return {
        name: {
            "description": item.description,
            "meaningful": item.meaningful,
            "signature": str(inspect.signature(item.function)),
        }
        for name, item in sorted(_registry.items())
    }


def _append_helper() -> Callable[..., None]:
    global _append_entry
    if _append_entry is None:
        helper_path = Path(__file__).resolve().parent.parent / "scripts" / "append-log.py"
        if not helper_path.is_file():
            helper_path = Path(sysconfig.get_path("data")) / "share" / "studio-dashboard" / "append-log.py"
        spec = importlib.util.spec_from_file_location("_studio_append_log", helper_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load Studio's append-only log helper: {helper_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _append_entry = module.append_entry
    return _append_entry


def _ensure_log(project: Project) -> None:
    project.ensure_state()
    if project.log_path.is_symlink():
        raise ValueError(f"Dashboard log must not be a symlink: {project.log_path}")
    try:
        descriptor = os.open(
            project.log_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        if not project.log_path.is_file():
            raise ValueError(f"Dashboard log is not a regular file: {project.log_path}")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(f"# Studio dashboard — {project.name}\n")
        stream.flush()
        os.fsync(stream.fileno())


def _log(project: Project, name: str, *, error: Optional[BaseException] = None) -> None:
    _ensure_log(project)
    items = [
        f"Action: {name}",
        f"Time (UTC): {datetime.now(timezone.utc).isoformat()}",
        "Result: succeeded" if error is None else f"Result: failed ({type(error).__name__})",
    ]
    # Action arguments, return values, and exception messages can contain whole
    # scripts/transcripts. The common log records only operation metadata.
    _append_helper()(project.log_path, f"{name} — {'succeeded' if error is None else 'failed'}", items, date.today())


def invoke(
    action_name: str,
    project: Project,
    /,
    *args: Any,
    _lock: Optional[ProjectLock] = None,
    **kwargs: Any,
) -> Any:
    """Run a registered action and append success/failure for meaningful work.

    Dashboard and CLI hosts can supply their held project lock through the
    reserved _lock keyword. An ownership failure performs neither the action
    nor a log write. A failed action preserves its original exception even if
    logging also fails. A successful action with a failed audit write raises
    ActionLogError, whose result retains the completed operation's output.
    """
    try:
        registered = _registry[action_name]
    except KeyError as error:
        raise ActionError(f"Unknown Studio action: {action_name}") from error
    if not isinstance(project, Project):
        raise ActionError("Actions require a validated Studio Project.")
    if _lock is not None:
        if _lock.project.root != project.root:
            raise ActionError("The supplied lock belongs to a different project.")
        _lock.assert_owned()
    try:
        result = registered.function(project, *args, **kwargs)
    except Exception as error:
        if registered.meaningful:
            try:
                _log(project, action_name, error=error)
            except Exception as log_error:
                try:
                    warnings.warn(
                        f"Action {action_name} failed; its dashboard log also failed ({type(log_error).__name__}).",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                except Warning:
                    pass  # Warning-as-error settings must not mask the action failure.
        raise
    if registered.meaningful:
        try:
            _log(project, action_name)
        except Exception as error:
            raise ActionLogError(action_name, result) from error
    return result
