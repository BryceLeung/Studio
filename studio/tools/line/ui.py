"""Native sentence review controls for Line!."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from studio.actions import ActionLogError, invoke
from . import actions as _registered_actions  # noqa: F401
from .mapping import active_source, coverage, new_state, review_status
from .shortcuts import KeyboardMappingsDialog, load_mappings, save_mappings


_COLORS = {"exact": "#c8ecd5", "partial": "#fff0b3", "flub": "#f7cece"}
_INK = {"exact": "#21643a", "partial": "#715000", "flub": "#8b2525"}


def _status_icon(status: str) -> QIcon:
    swatch = QPixmap(12, 12)
    swatch.fill(QColor(_COLORS.get(status, "#dddddd")))
    return QIcon(swatch)


def _timestamp(value: Any) -> str:
    if value is None:
        return "unknown"
    seconds, milliseconds = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


class _SentenceTable(QTableWidget):
    """Keep wrapped sentences readable after a window or splitter resize."""

    def __init__(self, parent: QWidget):
        super().__init__(0, 3, parent)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self.resizeRowsToContents)
        self.setHorizontalHeaderLabels(["Line", "Sentence", "Status"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(True)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setMinimumSectionSize(38)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 52)
        self.setColumnWidth(2, 124)
        self.setMinimumWidth(280)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start()


class LineWidget(QWidget):
    """Two source panels using the same registered actions as CLI callers."""

    def __init__(self, project: Any, lock: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.lock = lock
        self.state = new_state()
        self._refreshing = False
        self._active_panel = "script"
        self._source_keys: dict[str, Any] = {}
        self._review_key: Any = None
        self._panels: dict[str, dict[str, Any]] = {}
        self._shortcuts: list[QShortcut] = []
        self._review_shortcuts: dict[str, QShortcut] = {}
        self.keyboard_mappings = load_mappings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        toolbar = QHBoxLayout()
        title = QLabel("Line!")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        toolbar.addWidget(title)
        toolbar.addStretch()
        self.new_button = self._button("New review", self.new_review)
        self.open_button = self._button("Open mapping…", self.open_mapping)
        self.save_button = self._button("Save new version", self.save_mapping)
        self.save_button.setToolTip("Save a new mapping version (⌘/Ctrl+S).")
        for button in (self.new_button, self.open_button, self.save_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.mapping_label = QLabel()
        self.mapping_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.mapping_label.setWordWrap(True)
        layout.addWidget(self.mapping_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._make_panel("script", "SCRIPT"))
        splitter.addWidget(self._make_panel("transcript", "TRANSCRIPT"))
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        controls = QHBoxLayout()
        self.pair_label = QLabel("Select one sentence in each panel.")
        controls.addWidget(self.pair_label, 1)
        self.link_button = self._button("Link", self.link_selected)
        self.discard_button = self._button("Discard", self.discard_selected)
        for button in (self.link_button, self.discard_button):
            controls.addWidget(button)
        layout.addLayout(controls)

        self.readings_label = QLabel("Readings for selected script sentence")
        layout.addWidget(self.readings_label)
        self.readings = QListWidget()
        self.readings.setMaximumHeight(135)
        self.readings.setMinimumHeight(70)
        self.readings.setWordWrap(True)
        self.readings.setToolTip("Select a linked reading to jump to its transcript sentence.")
        self.readings.itemClicked.connect(self._reading_clicked)
        layout.addWidget(self.readings)

        self.coverage_label = QLabel()
        self.coverage_label.setWordWrap(True)
        layout.addWidget(self.coverage_label)
        self.review_progress = QProgressBar()
        self.review_progress.setRange(0, 100)
        layout.addWidget(self.review_progress)

        self._shortcut(QKeySequence.StandardKey.Save, self.save_mapping)
        self._shortcut(QKeySequence("Ctrl+G"), lambda: self.jump(self._active_panel))
        self._apply_review_shortcuts()
        self._refresh()

    def _button(self, text: str, callback: Any) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: self._run_ui(callback))
        return button

    def _run_ui(self, callback: Any) -> Any:
        """Show callback failures in the window rather than only on stderr."""
        try:
            return callback()
        except Exception as error:
            QMessageBox.warning(self, "Line!", str(error))
            return None

    def _shortcut(self, key: Any, callback: Any) -> None:
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda: self._run_ui(callback))
        self._shortcuts.append(shortcut)

    def _apply_review_shortcuts(self) -> None:
        for shortcut in self._review_shortcuts.values():
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._review_shortcuts.clear()
        callbacks = {
            "script_up": lambda: self.navigate("script", -1),
            "script_down": lambda: self.navigate("script", 1),
            "link": self.link_selected,
            "transcript_up": lambda: self.navigate("transcript", -1),
            "transcript_down": lambda: self.navigate("transcript", 1),
            "discard": self.discard_selected,
        }
        for name, callback in callbacks.items():
            key = self.keyboard_mappings[name]
            if not key:
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(name not in ("link", "discard"))
            shortcut.activated.connect(lambda callback=callback: self._run_ui(callback))
            self._review_shortcuts[name] = shortcut
        for name, widgets in self._panels.items():
            for direction, control in (("up", "previous"), ("down", "next")):
                key = self.keyboard_mappings[f"{name}_{direction}"]
                widgets[control].setToolTip(f"{direction.title()} on {name}" + (f" ({key})" if key else ""))

    def configure_shortcuts(self) -> None:
        dialog = KeyboardMappingsDialog(self.keyboard_mappings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_mappings(dialog.mappings)
            self.keyboard_mappings = dialog.mappings
            self._apply_review_shortcuts()
            self._refresh()

    def _make_panel(self, panel: str, title: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        layout.addWidget(label)
        source_label = QLabel()
        source_label.setWordWrap(True)
        source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(source_label)
        controls = QHBoxLayout()
        load = self._button("Load…", lambda: self.load_source(panel))
        close = self._button("Close", lambda: self._dispatch(f"line.close_{panel}"))
        previous = self._button("Previous", lambda: self.navigate(panel, -1))
        next_button = self._button("Next", lambda: self.navigate(panel, 1))
        jump = self._button("Jump…", lambda: self.jump(panel))
        jump.setToolTip("Jump to a sentence number (⌘/Ctrl+G for the active panel).")
        for button in (load, close, previous, next_button, jump):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        table = _SentenceTable(widget)
        table.installEventFilter(self)
        table.currentCellChanged.connect(
            lambda row, column, old_row, old_column: self._table_selected(panel, row)
        )
        layout.addWidget(table, 1)
        detail = QLabel()
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(detail)
        self._panels[panel] = {
            "widget": widget, "source_label": source_label, "table": table,
            "close": close, "previous": previous, "next": next_button,
            "jump": jump, "detail": detail,
        }
        return widget

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for panel, widgets in self._panels.items():
                if watched is widgets["table"]:
                    self._active_panel = panel
                    break
        return super().eventFilter(watched, event)

    def _dispatch(self, action_name: str, /, **kwargs: Any) -> tuple[bool, Any]:
        try:
            result = invoke(action_name, self.project, self.state, _lock=self.lock, **kwargs)
        except ActionLogError as error:
            self._refresh()
            QMessageBox.warning(self, "Dashboard log", str(error))
            return True, error.result
        except Exception as error:
            self._refresh()
            QMessageBox.warning(self, "Line!", str(error))
            return False, None
        self._refresh()
        return True, result

    def _table_selected(self, panel: str, row: int) -> None:
        if self._refreshing or row < 0:
            return
        self._active_panel = panel
        self._dispatch("line.select", panel=panel, index=row)

    def _selected_row(self, panel: str) -> dict | None:
        source = self.state[panel]
        index = self.state["selected"][panel]
        if source is None or index is None or not 0 <= index < len(source["rows"]):
            return None
        return source["rows"][index]

    def navigate(self, panel: str, delta: int) -> None:
        self._active_panel = panel
        if self.state[panel] is not None:
            self._dispatch("line.navigate", panel=panel, delta=delta)

    def jump(self, panel: str) -> None:
        source = self.state[panel]
        if source is None:
            return
        self._active_panel = panel
        current = self.state["selected"][panel]
        line, accepted = QInputDialog.getInt(
            self, f"Jump to {panel} sentence", "Sentence number:",
            (current + 1) if current is not None else 1, 1, len(source["rows"]),
        )
        if accepted:
            self._dispatch("line.jump", panel=panel, line=line)

    def _choose_file(self, title: str, directory: Path, file_filter: str) -> Path | None:
        initial = directory if directory.is_dir() else Path(self.project.root)
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self, title, str(initial), file_filter,
        )
        if not filename:
            return None
        try:
            path = Path(filename).resolve(strict=True)
            path.relative_to(Path(self.project.root).resolve())
        except (OSError, ValueError):
            QMessageBox.warning(self, "Line!", "Select a file inside this Studio project.")
            return None
        return path

    def load_source(self, panel: str) -> None:
        self._active_panel = panel
        root = Path(self.project.root)
        if panel == "script":
            directory = root / "1-Pre Production" / "1-Script Review" / "Approved"
            file_filter = "Script files (*.docx *.txt *.json)"
        else:
            directory = root / "3-Post Production" / "2-Transcribe"
            file_filter = "Word transcripts (*.json)"
        path = self._choose_file(f"Load {panel}", directory, file_filter)
        if path is None:
            return
        retained = active_source(self.state, panel)
        same_source = False
        if retained:
            reference = retained["source"]
            try:
                same_source = (
                    path.relative_to(root.resolve()).as_posix() == reference["path"]
                    and hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
                )
            except OSError as error:
                QMessageBox.warning(self, "Line!", str(error))
                return
        if retained and not same_source and not self._prepare_replacement(f"load a different {panel}", keep_other=panel):
            return
        self._dispatch(f"line.load_{panel}", path=str(path))

    def has_unsaved_changes(self) -> bool:
        return bool(self.state.get("dirty"))

    def _dirty_choice(self, purpose: str) -> str:
        if not self.has_unsaved_changes():
            return "continue"
        choice = QMessageBox.question(
            self, "Unsaved review", f"Save your review before you {purpose}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return "continue" if self.save_mapping() else "cancel"
        if choice == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    def confirm_close(self) -> bool:
        """Called by the dashboard before releasing a project or closing."""
        return self._dirty_choice("close this project") != "cancel"

    def _prepare_replacement(self, purpose: str, keep_other: str | None = None) -> bool:
        choice = self._dirty_choice(purpose)
        if choice == "cancel":
            return False
        if choice != "discard" and keep_other is None:
            return True
        # A different source starts a new mapping. Reset through its action,
        # retaining the other source when replacing only one panel.
        other = "transcript" if keep_other == "script" else "script"
        retained = active_source(self.state, other) if keep_other else None
        path = retained["source"]["path"] if retained else None
        was_closed = self.state[other] is None
        success, _result = self._dispatch("line.reset")
        if success and path:
            success, _result = self._dispatch(f"line.load_{other}", path=path)
            if success and was_closed:
                success, _result = self._dispatch(f"line.close_{other}")
        return success

    def new_review(self) -> None:
        choice = self._dirty_choice("start a new review")
        if choice != "cancel":
            self._dispatch("line.reset")

    def open_mapping(self) -> None:
        path = self._choose_file("Open Line! mapping", Path(self.project.line_dir), "Line! mappings (*.json)")
        if path is not None and self._prepare_replacement("open another mapping"):
            self._dispatch("line.load_mapping", path=str(path))

    def save_mapping(self) -> bool:
        if not active_source(self.state, "script") or not active_source(self.state, "transcript"):
            QMessageBox.information(self, "Line!", "Load a script and transcript before saving a mapping.")
            return False
        name = self.state.get("mapping_name")
        if not name:
            name, accepted = QInputDialog.getText(self, "Save mapping version", "Mapping name:", text="review")
            if not accepted:
                return False
        success, _path = self._dispatch("line.save", name=name)
        return success

    def link_selected(self) -> None:
        transcript = self._selected_row("transcript")
        if transcript is None:
            return
        linked = any(link["transcript_id"] == transcript["id"] for link in self.state["links"])
        if linked:
            self._dispatch("line.unlink")
        elif self._selected_row("script") is not None and transcript["id"] not in self.state["discarded"]:
            success, _result = self._dispatch("line.link")
            if success:
                self.navigate("transcript", 1)

    def discard_selected(self) -> None:
        transcript = self._selected_row("transcript")
        if transcript is None:
            return
        if transcript["id"] in self.state["discarded"]:
            self._dispatch("line.restore")
        else:
            success, _result = self._dispatch("line.discard")
            if success:
                self.navigate("transcript", 1)

    def _reading_clicked(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None or self.state["transcript"] is None:
            return
        self._active_panel = "transcript"
        self._dispatch("line.select", panel="transcript", index=index)
        self._panels["transcript"]["table"].setFocus()

    def _populate(self, panel: str) -> bool:
        source = self.state[panel]
        key = id(source) if source is not None else None
        if self._source_keys.get(panel, object()) == key:
            return False
        self._source_keys[panel] = key
        table = self._panels[panel]["table"]
        table.setRowCount(0)
        if source is not None:
            table.setRowCount(len(source["rows"]))
            for index, row in enumerate(source["rows"]):
                number = QTableWidgetItem(str(row["line"]))
                number.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
                sentence = QTableWidgetItem(row["text"])
                sentence.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                sentence.setToolTip(row["text"])
                status = QTableWidgetItem()
                status.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                table.setItem(index, 0, number)
                table.setItem(index, 1, sentence)
                table.setItem(index, 2, status)
        table._resize_timer.start()
        return True

    def _paint_statuses(self, script_status: dict, transcript_status: dict) -> None:
        script = active_source(self.state, "script")
        script_lines = {row["id"]: row["line"] for row in script["rows"]} if script else {}
        links = {link["transcript_id"]: link for link in self.state["links"]}
        for panel in ("script", "transcript"):
            source = self.state[panel]
            if source is None:
                continue
            table = self._panels[panel]["table"]
            for index, row in enumerate(source["rows"]):
                status = script_status.get(row["id"], "missing") if panel == "script" else transcript_status[row["id"]]
                color_status = status
                text = status.title()
                tooltip = "Best linked reading for this script sentence." if panel == "script" else text
                if panel == "transcript" and status == "linked":
                    link = links[row["id"]]
                    color_status = link["classification"]
                    text = f"{color_status.title()} → S{script_lines.get(link['script_id'], '?')}"
                    tooltip = "Classification of this individual reading."
                cell = table.item(index, 2)
                cell.setText(text)
                cell.setToolTip(tooltip)
                cell.setIcon(_status_icon(color_status) if color_status in _COLORS else QIcon())
                cell.setBackground(QBrush(QColor(_COLORS[color_status])) if color_status in _COLORS else QBrush())
                cell.setForeground(QBrush(QColor(_INK[color_status])) if color_status in _INK else QBrush())
                sentence = table.item(index, 1)
                font = sentence.font()
                font.setStrikeOut(status == "discarded")
                sentence.setFont(font)
                sentence.setForeground(QBrush(QColor("#888888")) if status == "discarded" else QBrush())

    def _refresh_readings(self, script_row: dict | None, transcript_row: dict | None) -> None:
        self.readings.clear()
        if script_row is None:
            self.readings_label.setText("Readings — select a script sentence")
            return
        self.readings_label.setText(f"Readings for script line {script_row['line']} — exact / partial / flub")
        transcript = active_source(self.state, "transcript")
        rows = {row["id"]: (index, row) for index, row in enumerate(transcript["rows"])} if transcript else {}
        selected_item = None
        for link in sorted(self.state["links"], key=lambda entry: rows.get(entry["transcript_id"], (-1, None))[0]):
            if link["script_id"] != script_row["id"] or link["transcript_id"] not in rows:
                continue
            index, row = rows[link["transcript_id"]]
            classification = link["classification"]
            item = QListWidgetItem(
                _status_icon(classification),
                f"T{row['line']} · {classification.title()} · {_timestamp(row.get('start_ms'))} — {row['text']}",
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setBackground(QColor(_COLORS[classification]))
            item.setForeground(QColor(_INK[classification]))
            if self.state["transcript"] is None:
                item.setToolTip("Load this transcript again to jump to the reading.")
            self.readings.addItem(item)
            if transcript_row and transcript_row["id"] == row["id"]:
                item.setSelected(True)
                selected_item = item
        if self.readings.count() == 0:
            item = QListWidgetItem("No linked readings yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.readings.addItem(item)
        elif selected_item is not None:
            self.readings.scrollToItem(selected_item)

    def _refresh(self) -> None:
        self._refreshing = True
        try:
            sources_changed = False
            for panel, widgets in self._panels.items():
                sources_changed = self._populate(panel) or sources_changed
                source = self.state[panel]
                selected = self.state["selected"][panel]
                loaded = source is not None
                rows = source["rows"] if loaded else []
                widgets["close"].setEnabled(loaded)
                widgets["previous"].setEnabled(loaded and selected is not None and selected > 0)
                widgets["next"].setEnabled(loaded and (selected is None or selected < len(rows) - 1))
                widgets["jump"].setEnabled(loaded)
                widgets["table"].setEnabled(loaded)
                if loaded:
                    reference = source["source"]["path"]
                    suffix = f" · {len(source.get('words', []))} words" if panel == "transcript" else ""
                    widgets["source_label"].setText(f"{Path(reference).name} · {len(rows)} sentences{suffix}")
                    widgets["source_label"].setToolTip(reference)
                else:
                    retained = active_source(self.state, panel)
                    text = f"Closed: {Path(retained['source']['path']).name} · review retained" if retained else "No source loaded"
                    widgets["source_label"].setText(text)
                    widgets["source_label"].setToolTip("")
                if selected is not None and loaded:
                    widgets["table"].setCurrentCell(selected, 1)
                    widgets["table"].scrollToItem(widgets["table"].item(selected, 1))
                    row = rows[selected]
                    detail = f"Selected: line {row['line']} of {len(rows)}"
                    if panel == "transcript":
                        detail += f" · {_timestamp(row.get('start_ms'))}–{_timestamp(row.get('end_ms'))} · words {row['word_start'] + 1}–{row['word_end'] + 1}"
                    widgets["detail"].setText(detail)
                else:
                    widgets["table"].clearSelection()
                    widgets["detail"].setText("Select a sentence." if loaded else "")

            script_status = coverage(self.state)
            transcript_status = review_status(self.state)
            review_key = (
                tuple((link["script_id"], link["transcript_id"], link["classification"]) for link in self.state["links"]),
                tuple(self.state["discarded"]),
            )
            if sources_changed or review_key != self._review_key:
                self._paint_statuses(script_status, transcript_status)
                self._review_key = review_key

            script_row = self._selected_row("script")
            transcript_row = self._selected_row("transcript")
            transcript_link = next((
                link for link in self.state["links"]
                if transcript_row and link["transcript_id"] == transcript_row["id"]
            ), None)
            discarded = transcript_row is not None and transcript_row["id"] in self.state["discarded"]
            can_link = (
                script_row is not None and transcript_row is not None and not discarded
            )
            self.link_button.setText("Unlink" if transcript_link else "Link")
            self.link_button.setEnabled(transcript_link is not None or can_link)
            link_key = self.keyboard_mappings["link"]
            self.link_button.setToolTip(
                ("Remove this transcript sentence's existing link." if transcript_link
                 else "Link with automatic quality, then advance the transcript.")
                + (f" ({link_key})" if link_key else "")
            )
            self.discard_button.setText("Restore" if discarded else "Discard")
            self.discard_button.setEnabled(transcript_row is not None)
            discard_key = self.keyboard_mappings["discard"]
            self.discard_button.setToolTip(
                ("Return this transcript sentence to review." if discarded
                 else "Discard this transcript sentence, then advance.")
                + (f" ({discard_key})" if discard_key else "")
            )
            self.pair_label.setText(
                f"Selected pair: S{script_row['line']} → T{transcript_row['line']}"
                if script_row and transcript_row else "Select one sentence in each panel."
            )
            self._refresh_readings(script_row, transcript_row)

            mapping_path = self.state.get("mapping_path")
            self.mapping_label.setText(
                (f"Mapping: {mapping_path}" if mapping_path else "New review · no saved mapping")
                + (" · Unsaved changes" if self.has_unsaved_changes() else "")
            )
            self.save_button.setEnabled(bool(active_source(self.state, "script") and active_source(self.state, "transcript")))
            exact = sum(status == "exact" for status in script_status.values())
            partial = sum(status == "partial" for status in script_status.values())
            flub = sum(status == "flub" for status in script_status.values())
            missing = sum(status == "missing" for status in script_status.values())
            linked = sum(status == "linked" for status in transcript_status.values())
            rejected = sum(status == "discarded" for status in transcript_status.values())
            unreviewed = sum(status == "unreviewed" for status in transcript_status.values())
            self.coverage_label.setText(
                f"Script coverage (best reading): {exact} exact · {partial} partial · {flub} flub · {missing} missing"
                f"\nTranscript review: {linked} linked · {rejected} discarded · {unreviewed} unreviewed"
            )
            total = len(transcript_status)
            reviewed = linked + rejected
            self.review_progress.setValue(round(100 * reviewed / total) if total else 0)
            self.review_progress.setFormat(f"Transcript reviewed: {reviewed} / {total}")
        finally:
            self._refreshing = False
