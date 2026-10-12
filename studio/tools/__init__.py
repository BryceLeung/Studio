"""Small registry of native dashboard tools."""

from dataclasses import dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    module: str
    widget: str

    def create(self, project: Any, lock: Any, parent: Any = None) -> Any:
        factory = getattr(import_module(self.module), self.widget)
        return factory(project, lock, parent)


TOOLS = {"line": Tool("line", "Line!", "studio.tools.line.ui", "LineWidget")}


def register_actions() -> None:
    """Register actions without importing the GUI or creating a window."""
    import_module("studio.session")
    import_module("studio.tools.line.actions")
