"""Connect Studio projects with directory links (Python 3.9+, Windows/macOS).

Terminal usage from the Studio root (use an absolute script path elsewhere):
    python scripts/connect.py connect "01-Love Compounds Too"
    python scripts/connect.py disconnect "01-Love Compounds Too"

Python usage:
    from scripts.connect import connect, disconnect
    connect("01-Love Compounds Too")
    disconnect("01-Love Compounds Too")

Both functions accept optional workspace and project_root paths. project_root is
the synced Books/Parent Like a Millionaire/Marketing/Studio directory. Without
it, Windows OneDrive environment variables and the usual Windows/macOS sync
locations are searched. Multiple matching roots require an explicit path.
If Windows denies symlink creation, a directory junction is used instead.
"""

import argparse
import os
from pathlib import Path
import stat
import struct
import sys
from typing import Optional, Union


PathLike = Union[str, os.PathLike]
PROJECT_ROOT_PARTS = ("Books", "Parent Like a Millionaire", "Marketing", "Studio")
WORKSPACE = Path(__file__).resolve().parent.parent


class ConnectionError(Exception):
    """A Studio connection cannot safely be created or removed."""


def _project_name(project: str) -> str:
    if (
        not project
        or project in (".", "..")
        or any(character in project for character in ("/", "\\", ":", "\0"))
        or project != project.strip()
        or project.endswith(".")
    ):
        raise ConnectionError("Specify the exact project folder name, without a path.")
    return project


def _workspace(workspace: Optional[PathLike]) -> Path:
    root = Path(workspace).expanduser().resolve() if workspace is not None else WORKSPACE
    if not root.is_dir():
        raise ConnectionError(f"Workspace directory does not exist: {root}")
    return root


def _candidate_project_roots() -> list[Path]:
    home = Path.home()
    sync_roots = [
        Path(value).expanduser()
        for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
        if (value := os.environ.get(name))
    ]
    sync_roots.append(home / "OneDrive")
    sync_roots.extend(sorted(home.glob("OneDrive*")))
    sync_roots.extend(sorted((home / "Library" / "CloudStorage").glob("OneDrive*")))

    # A legacy macOS path may point at the same folder as CloudStorage.
    roots = []
    for sync_root in sync_roots:
        root = sync_root.joinpath(*PROJECT_ROOT_PARTS).resolve()
        if root not in roots:
            roots.append(root)
    return roots


def _project_root(project_root: Optional[PathLike]) -> Path:
    if project_root is not None:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ConnectionError(f"OneDrive Studio directory does not exist: {root}")
        return root

    roots = [root for root in _candidate_project_roots() if root.is_dir()]
    if not roots:
        raise ConnectionError(
            "Cannot find the synced OneDrive Studio directory. "
            "Specify --project-root with its local path."
        )
    if len(roots) > 1:
        choices = "\n".join(f"  {root}" for root in roots)
        raise ConnectionError(
            "Found multiple OneDrive Studio directories. "
            f"Specify --project-root to choose one:\n{choices}"
        )
    return roots[0]


def _link_target(link: Path) -> Path:
    target_text = str(link.readlink())
    if os.name == "nt":
        # readlink() returns the Windows extended-path spelling for junctions.
        if target_text.startswith("\\\\?\\UNC\\"):
            target_text = "\\\\" + target_text[8:]
        elif target_text.startswith("\\\\?\\"):
            target_text = target_text[4:]
    target = Path(target_text)
    if not target.is_absolute():
        target = link.parent / target
    return target.resolve()


def _is_junction(link: Path) -> bool:
    if os.name != "nt":
        return False
    return getattr(link.lstat(), "st_reparse_tag", None) == stat.IO_REPARSE_TAG_MOUNT_POINT


def _is_link(link: Path) -> bool:
    return link.is_symlink() or _is_junction(link)


def _create_junction(source: Path, link: Path) -> None:
    """Create a Windows junction directly, without shell path interpolation.

    Use the documented REPARSE_DATA_BUFFER mount-point layout and
    FSCTL_SET_REPARSE_POINT. Only a newly created empty directory is modified.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    )
    kernel.DeviceIoControl.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL

    source_text = str(source)
    if source_text.startswith("\\\\?\\UNC\\"):
        raise ConnectionError("Windows junction targets must be on a local drive.")
    if source_text.startswith("\\\\?\\"):
        substitute = "\\??\\" + source_text[4:]
    elif source_text.startswith("\\\\"):
        raise ConnectionError("Windows junction targets must be on a local drive.")
    else:
        substitute = "\\??\\" + source_text
    substitute_bytes = substitute.encode("utf-16-le")
    print_bytes = source_text.encode("utf-16-le")
    path_buffer = substitute_bytes + b"\0\0" + print_bytes + b"\0\0"
    if len(path_buffer) + 16 > 16384:
        raise ConnectionError("The project path is too long for a Windows junction.")
    data = struct.pack(
        "<IHHHHHH", stat.IO_REPARSE_TAG_MOUNT_POINT, len(path_buffer) + 8, 0,
        0, len(substitute_bytes), len(substitute_bytes) + 2, len(print_bytes),
    ) + path_buffer
    buffer = ctypes.create_string_buffer(data)

    link.mkdir()
    try:
        handle = kernel.CreateFileW(
            str(link), 0x40000000, 0, None, 3, 0x02200000, None,
        )  # GENERIC_WRITE, OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            returned = wintypes.DWORD()
            if not kernel.DeviceIoControl(
                handle, 0x000900A4, buffer, len(data), None, 0,
                ctypes.byref(returned), None,
            ):  # FSCTL_SET_REPARSE_POINT
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
    except OSError:
        link.rmdir()  # Nonrecursive removal of only the new empty entry.
        raise


def connect(
    project: str,
    *,
    workspace: Optional[PathLike] = None,
    project_root: Optional[PathLike] = None,
) -> Path:
    """Link an exact project folder into the workspace; return the link path.

    Reconnecting the same target is harmless. Existing files, directories, or
    links to other targets are never replaced. No project data is copied.
    Windows falls back to a directory junction on missing symlink privileges.
    """
    name = _project_name(project)
    root = _project_root(project_root)
    source = root / name
    if not source.is_dir():
        raise ConnectionError(f"Project folder does not exist: {source}")
    source = source.resolve()
    if source.parent != root:
        raise ConnectionError(f"Project must be a direct folder inside {root}: {source}")

    link = _workspace(workspace) / name
    if os.path.lexists(link):
        if _is_link(link) and _link_target(link) == source:
            return link
        raise ConnectionError(f"Refusing to replace an existing workspace entry: {link}")

    try:
        try:
            link.symlink_to(source, target_is_directory=True)
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 1314:
                raise
            _create_junction(source, link)
    except OSError as error:
        raise ConnectionError(f"Cannot create project link {link}: {error}") from error
    return link


def disconnect(
    project: str,
    *,
    workspace: Optional[PathLike] = None,
    project_root: Optional[PathLike] = None,
) -> bool:
    """Remove a project's workspace link, leaving OneDrive data intact.

    Return True if a link was removed, or False if it was already absent.
    Broken project links can be removed even when OneDrive is unavailable.
    Existing files, real directories, and links to unrelated targets are refused.
    """
    name = _project_name(project)
    link = _workspace(workspace) / name
    if not os.path.lexists(link):
        return False
    if not _is_link(link):
        raise ConnectionError(f"Refusing to remove a workspace entry that is not a project link: {link}")

    # Disconnect does not need the project or its sync root to still exist.
    roots = (
        [Path(project_root).expanduser().resolve()]
        if project_root is not None
        else _candidate_project_roots()
    )
    target = _link_target(link)
    if not any(target == root / name for root in roots):
        raise ConnectionError(
            f"Refusing to remove a link outside the specified OneDrive project roots: {link}"
        )
    if _is_junction(link):
        link.rmdir()  # Remove the junction itself, never its contents.
    else:
        link.unlink()
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("connect", "disconnect"))
    parser.add_argument("project", nargs="+", help="Exact project folder name (spaces may be quoted).")
    parser.add_argument("--workspace", type=Path, help="Workspace root; defaults to this script's Studio root.")
    parser.add_argument("--project-root", type=Path, help="Synced Books/Parent Like a Millionaire/Marketing/Studio directory.")
    args = parser.parse_args(argv)
    project = " ".join(args.project)

    try:
        if args.action == "connect":
            link = connect(project, workspace=args.workspace, project_root=args.project_root)
            kind = "junction" if _is_junction(link) else "symlink"
            print(f"Connected: {link} -> {_link_target(link)} ({kind})")
        else:
            removed = disconnect(project, workspace=args.workspace, project_root=args.project_root)
            print(f"Disconnected: {project}" if removed else f"Already disconnected: {project}")
    except (ConnectionError, OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
