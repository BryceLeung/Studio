"""One action registry for dashboard controls, the CLI, and agent callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import sysconfig
from typing import Any, Callable, Optional
import warnings

from .project import Project, ProjectLock


_ACTION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.!:-]*")
ActionFormatter = Callable[[dict[str, Any], Any], str]


@dataclass(frozen=True)
class _Action:
    function: Callable[..., Any]
    description: str
    meaningful: bool
    describe: Optional[ActionFormatter] = None


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
    describe: Optional[ActionFormatter] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a project-first function without wrapping its direct calls.

    Use invoke() to obtain ownership checks and audit logging. Set meaningful
    to False for frequent UI-only actions such as selection and scrolling.
    A describe formatter receives bounded named inputs captured before execution
    and the actual return value after success. Keep descriptions short. Formatter
    failures use the generic summary and never change the action's outcome.
    """
    if not _ACTION_NAME.fullmatch(name):
        raise ActionError(f"Invalid action name: {name!r}")

    def register(function: Callable[..., Any]) -> Callable[..., Any]:
        if name in _registry and _registry[name].function is not function:
            raise ActionError(f"Action is already registered: {name}")
        text = description if description is not None else inspect.getdoc(function) or ""
        _registry[name] = _Action(function, " ".join(text.split()), bool(meaningful), describe)
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


def _value_snapshot(value: Any) -> str:
    """Serialize an input or return value with bounded JSON-friendly contents."""
    remaining = 300

    def encode(value: Any, depth: int = 0) -> Any:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            return {"omitted": "snapshot limit"}
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, (str, Path)):
            text = str(value)
            return text if len(text) <= 1000 else text[:1000] + "… [truncated]"
        if isinstance(value, Project):
            return {"root": str(value.root)}
        if isinstance(value, ProjectLock):
            return {"type": "ProjectLock", "project": str(value.project.root)}
        if depth >= 6:
            return {"type": type(value).__name__, "omitted": "nesting limit"}
        if isinstance(value, dict):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 30 or remaining <= 0:
                    result["__omitted_items__"] = len(value) - index
                    break
                label = key if isinstance(key, str) else f"<{type(key).__name__} key {index}>"
                result[label[:200]] = encode(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            # Large collections (notably transcript rows and words) retain their
            # size without copying their contents or consuming the whole budget.
            if len(value) > 10:
                return {"type": type(value).__name__, "count": len(value), "omitted": "collection contents"}
            result = []
            for index, item in enumerate(value):
                if remaining <= 0:
                    result.append({"omitted_items": len(value) - index})
                    break
                result.append(encode(item, depth + 1))
            return result
        return {"type": type(value).__name__}

    return json.dumps(encode(value), ensure_ascii=True, allow_nan=True)


def _input_snapshot(function: Callable[..., Any], project: Project, args: tuple, kwargs: dict) -> str:
    """Capture named inputs before mutation, including default values."""
    try:
        bound = inspect.signature(function).bind(project, *args, **kwargs)
        bound.apply_defaults()
        inputs = bound.arguments
    except TypeError:
        # Preserve the original call error while still recording invalid inputs.
        inputs = {"project": project, "args": args, "kwargs": kwargs}
    return _value_snapshot(inputs)


def _short_value(value: Any) -> str:
    """Keep the generic fallback small even for full tool state objects."""
    if value is None or isinstance(value, (str, bool, int, float, Path)):
        text = json.dumps(str(value) if isinstance(value, Path) else value, ensure_ascii=True)
        return text if len(text) <= 120 else text[:117] + "..."
    if isinstance(value, (dict, list, tuple)):
        return f"<{type(value).__name__}: {len(value)} items>"
    return f"<{type(value).__name__}>"


def _summary(registered: _Action, inputs: str, result: Any = None, *, failed: bool = False) -> str:
    named = json.loads(inputs)
    if registered.describe is not None and not failed:
        try:
            text = registered.describe(named, result)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Action descriptions must be non-empty strings")
            return " ".join(text.split())[:600]
        except Exception:
            # Audit presentation must not turn a completed operation into a failure.
            pass
    details = [f"{key}={_short_value(value)}" for key, value in list(named.items())[:8] if key != "project"]
    if not failed and result is not None:
        details.append(f"returned {_short_value(result)}")
    text = "; ".join(details) or registered.description or "No additional details"
    return " ".join(text.split())[:600]


def _log(project: Project, name: str, summary: str, *, error: Optional[BaseException] = None) -> None:
    _ensure_log(project)
    items = [
        f"Time (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Summary: {summary}",
    ]
    if error is not None:
        items.append(f"Error: {type(error).__name__}")
    _append_helper()(project.log_path, f"{name} — {'succeeded' if error is None else 'failed'}", items, date.today())


def invoke(
    action_name: str,
    project: Project,
    /,
    *args: Any,
    _lock: Optional[ProjectLock] = None,
    **kwargs: Any,
) -> Any:
    """Run a registered action and log a compact summary of meaningful work.

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
    inputs = _input_snapshot(registered.function, project, args, kwargs) if registered.meaningful else ""
    try:
        result = registered.function(project, *args, **kwargs)
    except Exception as error:
        if registered.meaningful:
            try:
                _log(project, action_name, _summary(registered, inputs, failed=True), error=error)
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
            _log(project, action_name, _summary(registered, inputs, result))
        except Exception as error:
            raise ActionLogError(action_name, result) from error
    return result
