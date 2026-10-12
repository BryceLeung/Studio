"""Shared dashboard session operations, independent of the window toolkit."""

from .actions import action
from .project import Project, ProjectLock


@action("dashboard.open", description="Open a project and acquire its dashboard lock.")
def open_project(project: Project) -> ProjectLock:
    lock = ProjectLock(project)
    lock.acquire()
    return lock


@action("dashboard.close", description="Close a project and release its dashboard lock.")
def close_project(project: Project, lock: ProjectLock) -> None:
    if lock.project.root != project.root:
        raise ValueError("The lock belongs to another project.")
    lock.release()
