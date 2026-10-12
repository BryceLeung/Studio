"""Native desktop container for Studio project tools."""

from __future__ import annotations

import atexit
from pathlib import Path
import sys
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QLabel, QMainWindow, QMessageBox,
    QStackedWidget, QToolBar, QVBoxLayout, QWidget,
)

from .actions import ActionLogError, invoke
from .project import Project, ProjectLock, ProjectLockError, discover_projects
from .tools import TOOLS, register_actions


class Dashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        register_actions()
        self.project: Optional[Project] = None
        self.lock: Optional[ProjectLock] = None
        self.widgets: dict[str, QWidget] = {}
        self.resize(1400, 900)
        self.setWindowTitle("Studio")

        self.stack = QStackedWidget()
        welcome = QWidget()
        layout = QVBoxLayout(welcome)
        heading = QLabel("Studio")
        heading.setStyleSheet("font-size: 28px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("Open a project to review script and transcript sentences in Line!"))
        layout.addStretch()
        self.stack.addWidget(welcome)
        self.setCentralWidget(self.stack)

        toolbar = QToolBar("Project")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.open_action = QAction("Open Project…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(lambda: self.choose_project())
        toolbar.addAction(self.open_action)
        self.close_action = QAction("Close Project", self)
        self.close_action.triggered.connect(lambda: self.close_project())
        self.close_action.setEnabled(False)
        toolbar.addAction(self.close_action)
        toolbar.addSeparator()
        self.tool_picker = QComboBox()
        for name, tool in TOOLS.items():
            self.tool_picker.addItem(tool.label, name)
        self.tool_picker.setEnabled(False)
        self.tool_picker.currentIndexChanged.connect(self.select_tool)
        toolbar.addWidget(self.tool_picker)
        self.project_label = QLabel("No project open")
        toolbar.addSeparator()
        toolbar.addWidget(self.project_label)

        project_menu = self.menuBar().addMenu("Project")
        project_menu.addAction(self.open_action)
        recent = project_menu.addMenu("Available Projects")
        projects = discover_projects(Path(__file__).resolve().parent.parent)
        for path in projects:
            item = recent.addAction(path.name)
            item.triggered.connect(lambda checked=False, p=path: self.open_project(p))
        recent.setEnabled(bool(projects))
        project_menu.addAction(self.close_action)
        line_menu = self.menuBar().addMenu("Line!")
        self.keyboard_action = line_menu.addAction("Keyboard mappings…")
        self.keyboard_action.setEnabled(False)
        self.keyboard_action.triggered.connect(self.configure_line_shortcuts)
        self.statusBar().showMessage("Choose a Studio project.")

        self.guard = QTimer(self)
        self.guard.setInterval(5000)
        self.guard.timeout.connect(self.check_lock)
        self.guard.start()
        atexit.register(self.release_on_exit)

    def choose_project(self) -> None:
        projects = discover_projects(Path(__file__).resolve().parent.parent)
        initial = str(self.project.root.parent) if self.project else (
            str(projects[0].parent) if projects else str(Path.home())
        )
        path = QFileDialog.getExistingDirectory(self, "Open Studio Project", initial)
        if path:
            self.open_project(Path(path))

    def open_project(self, path: Path) -> None:
        try:
            project = Project(path)
            if self.project and project.root == self.project.root:
                self.raise_()
                self.activateWindow()
                return
            if not self.confirm_tools():
                return
            try:
                lock = invoke("dashboard.open", project)
            except ActionLogError as warning:
                lock = warning.result
                QMessageBox.warning(self, "Action log", str(warning))
            # Acquire the new project before releasing the current one. If
            # another dashboard owns it, the current project stays open.
            try:
                self._clear_project()
            except Exception:
                lock.release()
                raise
            self.project, self.lock = project, lock
            self.tool_picker.setEnabled(True)
            self.close_action.setEnabled(True)
            self.project_label.setText(project.name)
            self.project_label.setToolTip(str(project.root))
            self.setWindowTitle(f"Studio — {project.name}")
            try:
                self.select_tool()
            except Exception:
                self._clear_project()
                raise
            self.statusBar().showMessage(f"Project state: {project.state_dir}")
        except Exception as error:
            QMessageBox.warning(self, "Cannot open project", str(error))

    def select_tool(self, *_args: object) -> None:
        if self.project is None or self.lock is None:
            return
        name = self.tool_picker.currentData()
        if name not in self.widgets:
            widget = TOOLS[name].create(self.project, self.lock, self)
            self.widgets[name] = widget
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(self.widgets[name])
        self.keyboard_action.setEnabled(name == "line")

    def configure_line_shortcuts(self) -> None:
        widget = self.widgets.get("line")
        if widget is not None:
            widget._run_ui(widget.configure_shortcuts)

    def confirm_tools(self) -> bool:
        for widget in self.widgets.values():
            confirm = getattr(widget, "confirm_close", None)
            if confirm is not None and not confirm():
                return False
        return True

    def close_project(self) -> bool:
        if not self.confirm_tools():
            return False
        try:
            self._clear_project()
        except Exception as error:
            QMessageBox.warning(self, "Cannot close project", str(error))
            return False
        return True

    def _clear_project(self) -> None:
        if self.lock and self.project:
            try:
                invoke("dashboard.close", self.project, self.lock)
            except ActionLogError as warning:
                QMessageBox.warning(self, "Action log", str(warning))
            except ProjectLockError:
                if self.lock.acquired:
                    raise
                # Another owner now holds the lock. Release leaves that file
                # intact; the old local view can still close immediately.
        for widget in self.widgets.values():
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self.widgets.clear()
        self.project = None
        self.lock = None
        self.stack.setCurrentIndex(0)
        self.tool_picker.setEnabled(False)
        self.keyboard_action.setEnabled(False)
        self.close_action.setEnabled(False)
        self.project_label.setText("No project open")
        self.setWindowTitle("Studio")
        self.statusBar().showMessage("Choose a Studio project.")

    def check_lock(self) -> None:
        if self.lock is None or not self.stack.currentWidget().isEnabled():
            return
        try:
            self.lock.assert_owned()
        except Exception as error:
            for widget in self.widgets.values():
                widget.setEnabled(False)
            QMessageBox.warning(self, "Project ownership changed", str(error))

    def release_on_exit(self) -> None:
        if self.lock is not None:
            try:
                self.lock.release()
            except (OSError, ProjectLockError):
                pass

    def closeEvent(self, event: object) -> None:
        if self.close_project():
            event.accept()
        else:
            event.ignore()


def launch(project_path: Optional[Path] = None) -> int:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("Studio")
    window = Dashboard()
    window.show()
    if project_path is not None:
        QTimer.singleShot(0, lambda: window.open_project(project_path))
    try:
        return app.exec()
    finally:
        window.release_on_exit()
