"""Content-project discovery and the dashboard's local ownership lock.

The lock is atomic on a local filesystem. OneDrive synchronizes copies of files,
so it cannot provide a distributed mutex between machines. A lock from another
host is always refused; it is never expired or automatically stolen.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import socket
import stat
from typing import Any, Iterator, Optional
import uuid


PROJECT_ROOT_PARTS = ("Books", "Parent Like a Millionaire", "Marketing", "Studio")
CONNECTED_DIRECTORY = "Connected Projects"
_PROJECT_NAME = re.compile(r"\d{2}(?:\.\d+)?-.+")


class ProjectError(ValueError):
    """A path does not identify a supported OneDrive content project."""


class ProjectLockError(RuntimeError):
    """The dashboard cannot acquire or retain project ownership."""


def _candidate_project_roots() -> list[Path]:
    """Use the same Windows/macOS OneDrive locations as scripts/connect.py."""
    home = Path.home()
    sync_roots = [
        Path(value).expanduser()
        for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
        if (value := os.environ.get(name))
    ]
    sync_roots.append(home / "OneDrive")
    sync_roots.extend(sorted(home.glob("OneDrive*")))
    sync_roots.extend(sorted((home / "Library" / "CloudStorage").glob("OneDrive*")))
    roots: list[Path] = []
    for sync_root in sync_roots:
        try:
            root = sync_root.joinpath(*PROJECT_ROOT_PARTS).resolve()
        except (OSError, RuntimeError):
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _is_project_parent(parent: Path) -> bool:
    if parent in _candidate_project_roots():
        return True
    # Accept an explicitly selected synced location belonging to another local
    # user, while still requiring the established Studio content-root layout.
    parts = parent.parts
    if len(parts) <= len(PROJECT_ROOT_PARTS):
        return False
    suffix = parts[-len(PROJECT_ROOT_PARTS):]
    if os.name == "nt":
        suffix = tuple(part.casefold() for part in suffix)
        expected = tuple(part.casefold() for part in PROJECT_ROOT_PARTS)
    else:
        expected = PROJECT_ROOT_PARTS
    return suffix == expected and any(
        part.casefold().startswith("onedrive")
        for part in parts[:-len(PROJECT_ROOT_PARTS)]
    )


def _reject_git_directory(root: Path) -> None:
    for directory in (root, *root.parents):
        if os.path.lexists(directory / ".git"):
            raise ProjectError(f"Project data must be outside a Git repository: {root}")


@dataclass(frozen=True)
class Project:
    """An existing numbered content project, resolved through any workspace link.

    Construction only reads the filesystem. State directories are created when
    a lock, log, or tool output first needs them.
    """

    root: Path

    def __post_init__(self) -> None:
        try:
            root = Path(self.root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ProjectError(f"Project directory does not exist: {self.root}") from error
        if not root.is_dir():
            raise ProjectError(f"Project path is not a directory: {root}")
        _reject_git_directory(root)
        if not _PROJECT_NAME.fullmatch(root.name):
            raise ProjectError(f"Expected a numbered Studio project folder: {root}")
        if not _is_project_parent(root.parent):
            raise ProjectError(
                "Select a project inside the synced OneDrive "
                "Books/Parent Like a Millionaire/Marketing/Studio directory. "
                f"Received: {root}"
            )
        object.__setattr__(self, "root", root)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def state_dir(self) -> Path:
        return self.root / "studio"

    @property
    def line_dir(self) -> Path:
        return self.state_dir / "line"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "LOG.md"

    def ensure_state(self) -> Path:
        if not self.root.is_dir() or self.root.resolve() != self.root:
            raise ProjectError(f"Project directory is unavailable: {self.root}")
        _reject_git_directory(self.root)
        self._ensure_directory(self.state_dir)
        return self.state_dir

    def ensure_line_dir(self) -> Path:
        self.ensure_state()
        self._ensure_directory(self.line_dir)
        return self.line_dir

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.is_symlink() or path.resolve() != path:
            raise ProjectError(f"Project state directory must not be a link or junction: {path}")
        try:
            path.mkdir(exist_ok=True)
        except OSError as error:
            raise ProjectError(f"Cannot create project state directory {path}: {error}") from error
        if not path.is_dir() or path.resolve() != path:
            raise ProjectError(f"Project state path is not a directory: {path}")


def discover_projects(workflow_root: Path) -> list[Path]:
    """Find connected projects and direct children of the local OneDrive roots.

    Invalid, unavailable, and broken links are omitted. All returned paths are
    resolved, deduplicated content-project paths; discovery creates no files.
    """
    workflow_root = Path(workflow_root).expanduser().resolve()
    candidates: list[Path] = []
    connected = workflow_root / CONNECTED_DIRECTORY
    if connected.is_dir():
        try:
            candidates.extend(sorted(connected.iterdir()))
        except OSError:
            pass
    for root in _candidate_project_roots():
        if root.is_dir():
            try:
                candidates.extend(sorted(root.iterdir()))
            except OSError:
                continue
    projects: set[Path] = set()
    for candidate in candidates:
        try:
            projects.add(Project(candidate).root)
        except (ProjectError, OSError, RuntimeError):
            continue
    return sorted(projects, key=lambda path: (path.name.casefold(), str(path).casefold()))


def _pid_alive(pid: int) -> bool:
    """Return False only when the local process is confirmed absent."""
    if os.name == "nt":
        # os.kill(pid, 0) does not have Unix's harmless semantics on Windows.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: missing PID
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        return error.errno != errno.ESRCH
    except OverflowError:
        return True  # Unrepresentable metadata cannot prove a dead owner.
    return True


class ProjectLock:
    """Own project/studio/dashboard.lock until explicitly released.

    A small persistent advisory-lock file serializes local ownership changes,
    including recovery of a dead owner. PID reuse is treated conservatively:
    any live local PID keeps the existing lock, regardless of its age.
    """

    def __init__(self, project: Project):
        self.project = project
        self.path = project.state_dir / "dashboard.lock"
        self.token = uuid.uuid4().hex
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def owner(self) -> Optional[dict[str, Any]]:
        try:
            return self._read_owner()
        except FileNotFoundError:
            return None

    @contextmanager
    def _metadata_guard(self) -> Iterator[None]:
        path = self.project.state_dir / ".dashboard.lock.guard"
        if path.is_symlink():
            raise ProjectLockError(f"Lock guard must not be a symlink: {path}")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        locked = False
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ProjectLockError(f"Lock guard is not a regular file: {path}")
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise ProjectLockError("Another local dashboard is changing this project's lock.") from error
            else:
                import fcntl

                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise ProjectLockError("Another local dashboard is changing this project's lock.") from error
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_owner(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise ProjectLockError(f"Dashboard lock must not be a symlink: {self.path}")
        descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ProjectLockError(f"Dashboard lock is not a regular file: {self.path}")
                data = stream.read(65537)
        finally:
            if descriptor != -1:
                os.close(descriptor)
        try:
            owner = json.loads(data) if len(data) <= 65536 else None
        except (ValueError, TypeError) as error:
            raise ProjectLockError(f"Dashboard lock metadata is unreadable: {self.path}") from error
        if (
            not isinstance(owner, dict)
            or not isinstance(owner.get("hostname"), str)
            or not owner["hostname"]
            or not isinstance(owner.get("token"), str)
            or not owner["token"]
            or type(owner.get("pid")) is not int
            or owner["pid"] <= 0
        ):
            raise ProjectLockError(f"Dashboard lock metadata is invalid: {self.path}")
        return owner

    def _is_own(self, owner: dict[str, Any]) -> bool:
        return (
            owner["token"] == self.token
            and owner["hostname"].casefold() == self.hostname.casefold()
            and owner["pid"] == self.pid == os.getpid()
        )

    def _write_owner(self) -> None:
        owner = {
            "schema_version": 1,
            "token": self.token,
            "hostname": self.hostname,
            "pid": self.pid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        identity = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(owner, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # Clean up only the incomplete file created by this attempt.
            try:
                current = self.path.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    self.path.unlink()
            except OSError:
                pass
            raise
        finally:
            if descriptor != -1:
                os.close(descriptor)

    def acquire(self) -> ProjectLock:
        if self._acquired:
            self.assert_owned()
            return self
        self.project.ensure_state()
        with self._metadata_guard():
            try:
                self._write_owner()
            except FileExistsError:
                owner = self._read_owner()
                if owner["hostname"].casefold() != self.hostname.casefold():
                    raise ProjectLockError(
                        f"Project is locked on {owner['hostname']} (PID {owner['pid']}). "
                        "Close that dashboard and wait for OneDrive to synchronize."
                    )
                if _pid_alive(owner["pid"]):
                    raise ProjectLockError(f"Project is already open on this machine (PID {owner['pid']}).")
                # The local guard prevents another local dashboard from racing
                # recovery. Check again for changes made by a sync client.
                if self._read_owner() != owner:
                    raise ProjectLockError("Project lock changed during dead-owner recovery; reopen the project.")
                self.path.unlink()
                self._write_owner()
            self._acquired = True
        return self

    def assert_owned(self) -> None:
        if not self._acquired:
            raise ProjectLockError("This dashboard does not own the project lock.")
        try:
            owner = self._read_owner()
        except FileNotFoundError as error:
            raise ProjectLockError("Project lock disappeared; reopen the project before editing.") from error
        if not self._is_own(owner):
            raise ProjectLockError("Project lock ownership changed; reopen the project before editing.")

    def release(self) -> bool:
        if not self._acquired:
            return False
        with self._metadata_guard():
            try:
                owner = self._read_owner()
            except FileNotFoundError:
                self._acquired = False
                return False
            if not self._is_own(owner):
                self._acquired = False
                raise ProjectLockError("Project lock ownership changed; another dashboard's lock was preserved.")
            self.path.unlink()
            self._acquired = False
        return True

    def __enter__(self) -> ProjectLock:
        return self.acquire()

    def __exit__(self, *_exc: Any) -> None:
        self.release()
