"""Shared dashboard session operations, independent of the window toolkit."""

from pathlib import Path

from .actions import action
from .project import Project, ProjectLock


def _describe_open(inputs: dict, result: ProjectLock) -> str:
    return f"Opened project: {result.project.name}"


def _describe_close(inputs: dict, result: None) -> str:
    return f"Closed project: {Path(inputs['project']['root']).name}"


@action("dashboard.open", description="Open a project and acquire its dashboard lock.", describe=_describe_open)
def open_project(project: Project) -> ProjectLock:
    lock = ProjectLock(project)
    lock.acquire()
    return lock


@action("dashboard.close", description="Close a project and release its dashboard lock.", describe=_describe_close)
def close_project(project: Project, lock: ProjectLock) -> None:
    if lock.project.root != project.root:
        raise ValueError("The lock belongs to another project.")
    lock.release()
