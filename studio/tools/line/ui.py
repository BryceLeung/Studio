"""Native word-group review controls for Line!."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt, QTimer, Signal, QSize
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCursor
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
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyle,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from studio.actions import ActionLogError, invoke
from . import actions as _registered_actions  # noqa: F401
from .mapping import active_source, coverage, default_mapping_name, group_rows, is_discarded, linked_group, new_state, range_key, range_text, review_status, word_range
from .shortcuts import KeyboardMappingsDialog, load_mappings, save_mappings
from .visuals import COLORS, ConnectionBands, OverviewStrip
from . import combined
from .playback import LinePlayback, audio_clip


_COLORS = COLORS
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


class _GroupingTable(QTableWidget):
    """Keep wrapped word groups readable after a window or splitter resize."""

    def __init__(self, parent: QWidget):
        super().__init__(0, 3, parent)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self.resizeRowsToContents)
        self.setHorizontalHeaderLabels(["Group", "Words", "Status"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
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

    def changeEvent(self, event: Any) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "_resize_timer"):
            self._resize_timer.start()


class _SplitPicker(QTextEdit):
    """A temporary, wrapped word display with a movable split caret."""

    chosen = Signal(int)
    cancelled = Signal()
    preferred_height_changed = Signal(int)

    def __init__(self, source: dict, group: dict, parent: QWidget):
        super().__init__(parent)
        words = source["words"][group["word_start"]:group["word_end"] + 1]
        text = range_text(source, group)
        positions = []
        offset = 0
        for index, word in enumerate(words):
            if index:
                offset += len(word.get("separator", " ").encode("utf-16-le")) // 2
                positions.append(offset)
            offset += len(word["text"].encode("utf-16-le")) // 2
        self._positions = positions
        self._first_word = group["word_start"]
        self._boundary = 0
        self.setPlainText(text)
        self.setReadOnly(True)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCursorWidth(3)
        self.setStyleSheet("background: #eaf4ff; padding: 3px;")
        self.setToolTip("Click a gap between words to split. Left/Right moves the marker; Enter confirms; Escape cancels.")
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(lambda: self.preferred_height_changed.emit(self.sizeHint().height()))
        self.document().documentLayout().documentSizeChanged.connect(lambda _size: self._height_timer.start(0))
        self._show_boundary()

    def sizeHint(self) -> QSize:
        return QSize(max(160, self.width()), max(42, int(self.document().size().height()) + 12))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_height_timer"):
            self._height_timer.start(0)

    def _show_boundary(self) -> None:
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._positions[self._boundary])
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self.viewport().update()

    def viewportEvent(self, event: Any) -> bool:
        handled = super().viewportEvent(event)
        if event.type() == QEvent.Type.Paint and self._positions:
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._positions[self._boundary])
            marker = self.cursorRect(cursor)
            painter = QPainter(self.viewport())
            painter.setPen(QPen(QColor("#0878d1"), 3))
            painter.drawLine(marker.left() - 2, marker.top(), marker.left() - 2, marker.bottom())
            painter.end()
        return handled

    def _closest(self, point: Any) -> int:
        cursor = self.cursorForPosition(point)
        return min(range(len(self._positions)), key=lambda index: abs(self._positions[index] - cursor.position()))

    def mouseMoveEvent(self, event: Any) -> None:
        self._boundary = self._closest(event.position().toPoint())
        self._show_boundary()
        event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._boundary = self._closest(event.position().toPoint())
            self.chosen.emit(self._first_word + self._boundary + 1)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key == Qt.Key.Key_Left:
            self._boundary = max(0, self._boundary - 1)
            self._show_boundary()
        elif key == Qt.Key.Key_Right:
            self._boundary = min(len(self._positions) - 1, self._boundary + 1)
            self._show_boundary()
        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.chosen.emit(self._first_word + self._boundary + 1)
        else:
            event.ignore()


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
        self._split_mode: tuple[str, int] | None = None
        self._shortcuts: list[QShortcut] = []
        self._review_shortcuts: dict[str, QShortcut] = {}
        self._collapsed: set[str] = set()
        self._table_rows: dict[str, dict[int, int]] = {"script": {}, "transcript": {}}
        self._headers: dict[str, tuple[int, QPushButton]] = {}
        self._playback = None
        self._playing_selection = None
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
        self.open_button = self._button("Open Line project…", self.open_mapping)
        self.save_button = self._button("Save new version", self.save_mapping)
        self.save_button.setToolTip("Save groupings and mappings as a new Line project version (⌘/Ctrl+S).")
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
        self.bands = ConnectionBands(self._panels["script"]["table"], self._panels["transcript"]["table"])
        splitter.insertWidget(1, self.bands)
        splitter.splitterMoved.connect(lambda *_: self.bands.update())
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        layout.addWidget(splitter, 1)

        controls = QHBoxLayout()
        self.pair_label = QLabel("Select one word grouping in each panel.")
        controls.addWidget(self.pair_label, 1)
        self.link_button = self._button("Link", self.link_selected)
        self.discard_button = self._button("Discard", self.discard_selected)
        for button in (self.link_button, self.discard_button):
            controls.addWidget(button)
        layout.addLayout(controls)

        playback_controls = QHBoxLayout()
        self.play_button = self._button("Play line", self.play_selected)
        self.audio_label = QLabel("Select a transcript line to hear its audio.")
        self.audio_label.setWordWrap(True)
        playback_controls.addWidget(self.play_button)
        playback_controls.addWidget(self.audio_label, 1)
        layout.addLayout(playback_controls)

        self.readings_label = QLabel("Readings for selected script grouping")
        layout.addWidget(self.readings_label)
        self.readings = QListWidget()
        self.readings.setMaximumHeight(135)
        self.readings.setMinimumHeight(70)
        self.readings.setWordWrap(True)
        self.readings.setToolTip("Select a linked reading to jump to its transcript grouping.")
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

    def _audio_selection(self):
        row = self._selected_row("transcript")
        if row is None or len(self._selected_indexes("transcript")) != 1 or self._split_mode:
            raise ValueError("Select one transcript line to hear its audio.")
        return audio_clip(self.project.root, self.state["transcript"], row)

    def play_selected(self) -> None:
        if self._playback and self._playback.active:
            self._playback.stop()
            return
        clip = self._audio_selection()
        if self._playback is None:
            self._playback = LinePlayback(self)
            self._playback.changed.connect(self._playback_changed)
            self._playback.failed.connect(lambda message: self.audio_label.setText(f"Audio: {message}"))
        self._playing_selection = clip
        path, start, end = clip
        self.audio_label.setText(f"{path.name} · {_timestamp(start)}–{_timestamp(end)}")
        self._playback.play(*clip)

    def _playback_changed(self, active: bool) -> None:
        self.play_button.setText("Stop" if active else "Play line")
        if not active:
            self._playing_selection = None

    def hideEvent(self, event: Any) -> None:
        if self._playback:
            self._playback.stop()
        super().hideEvent(event)

    def _refresh_audio(self) -> None:
        try:
            clip = self._audio_selection()
            reason = f"{clip[0].name} · {_timestamp(clip[1])}–{_timestamp(clip[2])}"
        except (ValueError, OSError) as error:
            clip, reason = None, str(error)
        if self._playback and self._playback.active and clip != self._playing_selection:
            self._playback.stop()
        self.play_button.setEnabled(clip is not None)
        key = self.keyboard_mappings["play"]
        self.play_button.setToolTip("Play / stop the selected transcript line" + (f" ({key})" if key else ""))
        self.audio_label.setText(reason)

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
            "play": self.play_selected,
        }
        for name, callback in callbacks.items():
            key = self.keyboard_mappings[name]
            if not key:
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(name not in ("link", "discard", "play"))
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
        load = self._button("Add files…" if panel == "transcript" else "Load…", lambda: self.load_source(panel))
        close = self._button("Close", lambda: self._dispatch(f"line.close_{panel}"))
        previous = self._button("Previous", lambda: self.navigate(panel, -1))
        next_button = self._button("Next", lambda: self.navigate(panel, 1))
        jump = self._button("Jump…", lambda: self.jump(panel))
        jump.setToolTip("Jump to a grouping number (⌘/Ctrl+G for the active panel).")
        for button in (load, close, previous, next_button, jump):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        table = _GroupingTable(widget)
        table.installEventFilter(self)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda point: self._group_menu(panel, point))
        table.currentCellChanged.connect(
            lambda row, column, old_row, old_column: self._table_selected(panel, row)
        )
        table.itemSelectionChanged.connect(lambda: self._selection_changed(panel))
        table.cellClicked.connect(lambda row, _column: self._header_clicked(panel, row))
        overview = OverviewStrip(table, widget)
        overview.activated.connect(lambda index: self._overview_selected(panel, index))
        table_layout = QHBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(2)
        table_layout.addWidget(table, 1)
        table_layout.addWidget(overview)
        layout.addLayout(table_layout, 1)
        detail = QLabel()
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(detail)
        self._panels[panel] = {
            "widget": widget, "source_label": source_label, "table": table,
            "close": close, "previous": previous, "next": next_button,
            "jump": jump, "detail": detail, "overview": overview,
        }
        return widget

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for panel, widgets in self._panels.items():
                if watched is widgets["table"]:
                    self._active_panel = panel
                    if hasattr(self, "bands"):
                        self.bands.focus_panel = 0 if panel == "script" else 1
                        self.bands.update()
                    break
        return super().eventFilter(watched, event)

    def _selected_indexes(self, panel: str) -> list[int]:
        table = self._panels[panel]["table"]
        reverse = {row: index for index, row in self._table_rows[panel].items()}
        return sorted(reverse[index.row()] for index in table.selectionModel().selectedRows() if index.row() in reverse)

    def _display_row(self, panel: str, index: int) -> int:
        return self._table_rows[panel][index]

    def _header_clicked(self, panel: str, row: int) -> None:
        if panel == "transcript":
            for path, (header, _button) in self._headers.items():
                if header == row:
                    self._toggle_recording(path)
                    break

    def _apply_collapsed(self) -> None:
        table = self._panels["transcript"]["table"]
        for path, (_header, button) in self._headers.items():
            collapsed = path in self._collapsed
            button.setText(Path(path).name)
            button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight if collapsed else QStyle.StandardPixmap.SP_ArrowDown))
        for index, group in enumerate(self.state["groupings"]["transcript"]):
            recording = combined.recording_for(self.state["transcript"], group)
            if recording and index in self._table_rows["transcript"]:
                table.setRowHidden(self._display_row("transcript", index), recording[1]["source"]["path"] in self._collapsed)
        self.bands.update()
        self._panels["transcript"]["overview"].update()

    def _toggle_recording(self, path: str) -> None:
        self._cancel_split(refresh=False)
        self._panels["transcript"]["overview"].boundaries()
        if path in self._collapsed:
            self._collapsed.remove(path)
        else:
            self._collapsed.add(path)
        selected = self._selected_row("transcript")
        self._apply_collapsed()
        if selected and combined.recording_for(self.state["transcript"], selected)[1]["source"]["path"] in self._collapsed:
            self._dispatch("line.select", panel="transcript", index=None)
        else:
            self._refresh()

    def _selection_changed(self, panel: str) -> None:
        if not self._refreshing:
            QTimer.singleShot(0, self._refresh)

    def _group_menu(self, panel: str, point: Any) -> None:
        table = self._panels[panel]["table"]
        row = table.rowAt(point.y())
        if row < 0 or self.state[panel] is None:
            return
        reverse = {display: index for index, display in self._table_rows[panel].items()}
        if row not in reverse:
            return
        if reverse[row] not in self._selected_indexes(panel):
            table.clearSelection()
            table.setCurrentCell(row, 1)
            table.selectionModel().select(table.model().index(row, 1),
                                          QItemSelectionModel.SelectionFlag.ClearAndSelect
                                          | QItemSelectionModel.SelectionFlag.Rows)
        indexes = self._selected_indexes(panel)
        menu = QMenu(table)
        if len(indexes) == 1:
            group = self.state["groupings"][panel][indexes[0]]
            if panel == "script":
                discarded = word_range(group) in self.state.get("script_discarded", [])
                action = menu.addAction("Restore script" if discarded else "Discard script")
                action.setEnabled(self._split_mode is None)
                action.triggered.connect(self.discard_script_selected)
                menu.addSeparator()
            error = _registered_actions.grouping_edit_error(self.state, panel, indexes)
            if group["word_start"] == group["word_end"]:
                error = "A grouping needs at least two words to split."
            action = menu.addAction("Split group" if error is None else f"Split group ({error})")
            action.setEnabled(error is None)
            action.triggered.connect(lambda: self._run_ui(lambda: self._begin_split(panel, indexes[0])))
        if len(indexes) >= 2:
            error = _registered_actions.grouping_edit_error(self.state, panel, indexes)
            if indexes != list(range(indexes[0], indexes[-1] + 1)):
                error = "Select adjacent groups."
            if panel == "transcript" and error is None:
                discarded = [is_discarded(self.state, self.state["groupings"][panel][index]) for index in indexes]
                if any(value != discarded[0] for value in discarded):
                    error = "Restore discarded groups first."
            action = menu.addAction("Merge groups" if error is None else f"Merge groups ({error})")
            action.setEnabled(error is None)
            action.triggered.connect(lambda: self._run_ui(lambda: self._merge_groups(panel, indexes)))
        if not menu.isEmpty():
            menu.exec(table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def _begin_split(self, panel: str, index: int) -> None:
        error = _registered_actions.grouping_edit_error(self.state, panel, [index])
        if error:
            raise ValueError(error)
        group = self.state["groupings"][panel][index]
        if group["word_start"] == group["word_end"]:
            raise ValueError("A grouping needs at least two words to split.")
        self._cancel_split(refresh=False)
        table = self._panels[panel]["table"]
        picker = _SplitPicker(self.state[panel], group, table)
        picker.chosen.connect(lambda boundary: self._split_at(panel, boundary))
        picker.cancelled.connect(self._cancel_split)
        picker.preferred_height_changed.connect(
            lambda height: self._resize_split_row(panel, index, picker, height)
        )
        display = self._display_row(panel, index)
        table.setCellWidget(display, 1, picker)
        table.resizeRowToContents(display)
        self._split_mode = panel, index
        for shortcut in self._review_shortcuts.values():
            shortcut.setEnabled(False)
        self._refresh()
        picker.setFocus()

    def _resize_split_row(self, panel: str, index: int, picker: _SplitPicker, height: int) -> None:
        table = self._panels[panel]["table"]
        display = self._display_row(panel, index)
        if self._split_mode == (panel, index) and table.cellWidget(display, 1) is picker and abs(table.rowHeight(display) - height) > 2:
            table.setRowHeight(display, height)

    def _cancel_split(self, *, refresh: bool = True) -> None:
        if self._split_mode is None:
            return
        panel, index = self._split_mode
        self._split_mode = None
        table = self._panels[panel]["table"]
        display = self._display_row(panel, index)
        table.removeCellWidget(display, 1)
        table.resizeRowToContents(display)
        for shortcut in self._review_shortcuts.values():
            shortcut.setEnabled(True)
        if refresh:
            self._refresh()

    def _split_at(self, panel: str, boundary: int) -> None:
        self._cancel_split(refresh=False)
        success, _result = self._dispatch("line.split", panel=panel, boundary=boundary)
        if success:
            table = self._panels[panel]["table"]
            index = self.state["selected"][panel]
            table.clearSelection()
            for row in (index, index + 1):
                table.selectionModel().select(table.model().index(self._display_row(panel, row), 0),
                                              QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            self._refresh()

    def _merge_groups(self, panel: str, indexes: list[int]) -> None:
        success, _result = self._dispatch("line.merge", panel=panel, indexes=indexes)
        if success:
            self._panels[panel]["table"].selectRow(self._display_row(panel, indexes[0]))
            self._refresh()

    def _dispatch(self, action_name: str, /, **kwargs: Any) -> tuple[bool, Any]:
        if self._split_mode is not None and action_name != "line.select":
            self._cancel_split(refresh=False)
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
        if action_name in ("line.reset", "line.load_mapping"):
            self._collapsed.clear()
            self._source_keys.clear()
        self._refresh()
        return True, result

    def _table_selected(self, panel: str, row: int) -> None:
        if self._refreshing or row < 0:
            return
        row = next((index for index, display in self._table_rows[panel].items() if display == row), None)
        if row is None:
            return
        if self._split_mode and self._split_mode != (panel, row):
            self._cancel_split(refresh=False)
        self._active_panel = panel
        self._dispatch("line.select", panel=panel, index=row)

    def _overview_selected(self, panel: str, index: int) -> None:
        self._active_panel = panel
        index = next((group for group, row in self._table_rows[panel].items() if row == index), None)
        if index is not None:
            self._dispatch("line.jump", panel=panel, line=index + 1)

    def _selected_row(self, panel: str) -> dict | None:
        source = self.state[panel]
        index = self.state["selected"][panel]
        if source is None or index is None or not 0 <= index < len(self.state["groupings"][panel]):
            return None
        return group_rows(self.state, panel)[index]

    def navigate(self, panel: str, delta: int) -> None:
        self._active_panel = panel
        if self.state[panel] is not None:
            if panel == "transcript" and self._collapsed:
                visible = [index for index, group in enumerate(self.state["groupings"][panel])
                           if combined.recording_for(self.state[panel], group)[1]["source"]["path"] not in self._collapsed]
                selected = self.state["selected"][panel]
                candidates = visible if selected is None else [i for i in visible if (i - selected) * delta > 0]
                if candidates:
                    self._dispatch("line.select", panel=panel, index=candidates[0] if delta > 0 else candidates[-1])
                return
            self._dispatch("line.navigate", panel=panel, delta=delta)

    def jump(self, panel: str) -> None:
        source = self.state[panel]
        if source is None:
            return
        self._active_panel = panel
        current = self.state["selected"][panel]
        line, accepted = QInputDialog.getInt(
            self, f"Jump to {panel} grouping", "Group number:",
            (current + 1) if current is not None else 1, 1, len(self.state["groupings"][panel]),
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
            filenames, _filter = QFileDialog.getOpenFileNames(self, "Add transcript files", str(directory), "Word transcripts (*.json)")
            if filenames:
                self._dispatch("line.add_transcripts", paths=filenames)
            return
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
        paths = ([item["source"]["path"] for item in combined.recordings(retained)] if other == "transcript"
                 else [retained["source"]["path"]]) if retained else []
        was_closed = self.state[other] is None
        success, _result = self._dispatch("line.reset")
        if success and paths:
            success, _result = (self._dispatch("line.add_transcripts", paths=paths) if other == "transcript"
                                else self._dispatch(f"line.load_{other}", path=paths[0]))
            if success and was_closed:
                success, _result = self._dispatch(f"line.close_{other}")
        return success

    def new_review(self) -> None:
        choice = self._dirty_choice("start a new review")
        if choice != "cancel":
            self._dispatch("line.reset")

    def open_mapping(self) -> None:
        path = self._choose_file("Open Line project", Path(self.project.line_dir), "Line projects (*.json)")
        if path is not None and self._prepare_replacement("open another mapping"):
            self._dispatch("line.load_mapping", path=str(path))

    def save_mapping(self) -> bool:
        if not active_source(self.state, "script") or not active_source(self.state, "transcript"):
            QMessageBox.information(self, "Line!", "Load a script and transcript before saving a Line project.")
            return False
        name = self.state.get("mapping_name")
        if not name:
            name, accepted = QInputDialog.getText(
                self, "Save Line project version", "Project name:", text=default_mapping_name(self.state)
            )
            if not accepted:
                return False
        success, _path = self._dispatch("line.save", name=name)
        return success

    def link_selected(self) -> None:
        transcript = self._selected_row("transcript")
        if transcript is None or self._split_mode or len(self._selected_indexes("transcript")) != 1:
            return
        linked = linked_group(self.state, "transcript", transcript)
        if linked:
            self._dispatch("line.unlink")
        elif self._selected_row("script") is not None and len(self._selected_indexes("script")) == 1 and not is_discarded(self.state, transcript):
            success, _result = self._dispatch("line.link")
            if success:
                self.navigate("script", 1)
                self.navigate("transcript", 1)

    def discard_script_selected(self) -> None:
        script = self._selected_row("script")
        if script is None or self._split_mode or len(self._selected_indexes("script")) != 1:
            return
        discarded = word_range(script) in self.state.get("script_discarded", [])
        success, _result = self._dispatch("line.restore" if discarded else "line.discard", panel="script")
        if success and not discarded:
            self.navigate("script", 1)

    def discard_selected(self) -> None:
        transcript = self._selected_row("transcript")
        if transcript is None or self._split_mode or len(self._selected_indexes("transcript")) != 1:
            return
        if is_discarded(self.state, transcript):
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
        key = (
            source["source"].get("path"), source["source"].get("sha256"),
            tuple(range_key(group) for group in self.state["groupings"][panel]),
        ) if source is not None else None
        if self._source_keys.get(panel, object()) == key:
            return False
        self._source_keys[panel] = key
        table = self._panels[panel]["table"]
        self._panels[panel]["overview"]._heights.clear()
        table.clearSpans()
        table.setRowCount(0)
        self._table_rows[panel] = {}
        if panel == "transcript":
            self._headers = {}
        if source is not None:
            rows = group_rows(self.state, panel)
            sectioned = panel == "transcript" and len(combined.recordings(source)) > 1
            table.setRowCount(len(rows) + (len(combined.recordings(source)) if sectioned else 0))
            display = 0
            previous_recording = None
            for index, row in enumerate(rows):
                if sectioned and row["recording"] != previous_recording:
                    previous_recording = row["recording"]
                    path = combined.recordings(source)[previous_recording]["source"]["path"]
                    button = QPushButton(table)
                    button.setStyleSheet("QPushButton { text-align: left; padding: 6px; font-weight: bold; border: 0; "
                                         "background: palette(midlight); color: palette(text); }")
                    button.setToolTip(f"{path}\nClick to collapse or expand this file. Coverage includes hidden files.")
                    button.clicked.connect(lambda _checked=False, path=path: self._toggle_recording(path))
                    header = QTableWidgetItem()
                    header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    table.setItem(display, 0, header)
                    table.setSpan(display, 0, 1, 3)
                    table.setCellWidget(display, 0, button)
                    self._headers[path] = display, button
                    display += 1
                self._table_rows[panel][index] = display
                number = QTableWidgetItem(str(row["line"]))
                number.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
                sentence = QTableWidgetItem(row["text"])
                sentence.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                sentence.setToolTip(row["text"])
                status = QTableWidgetItem()
                status.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                table.setItem(display, 0, number)
                table.setItem(display, 1, sentence)
                table.setItem(display, 2, status)
                display += 1
            if sectioned:
                self._apply_collapsed()
        table._resize_timer.start()
        return True

    def _paint_statuses(self, script_status: dict, transcript_status: dict) -> None:
        script_lines = {range_key(row): row["line"] for row in group_rows(self.state, "script")}
        links = {range_key(link["transcript"]): link for link in self.state["links"]}
        for panel in ("script", "transcript"):
            source = self.state[panel]
            if source is None:
                continue
            table = self._panels[panel]["table"]
            for index, row in enumerate(group_rows(self.state, panel)):
                display = self._display_row(panel, index)
                key = range_key(row)
                status = script_status.get(key, "missing") if panel == "script" else transcript_status[key]
                color_status = status
                text = status.title()
                tooltip = "Best linked reading for this script grouping." if panel == "script" else text
                if panel == "transcript" and status == "linked":
                    link = links[key]
                    color_status = link["classification"]
                    text = f"{color_status.title()} → S{script_lines.get(range_key(link['script']), '?')}"
                    tooltip = "Classification of this individual reading."
                cell = table.item(display, 2)
                cell.setText(text)
                cell.setToolTip(tooltip)
                cell.setIcon(_status_icon(color_status) if color_status in _COLORS else QIcon())
                cell.setBackground(QBrush(QColor(_COLORS[color_status])) if color_status in _COLORS else QBrush())
                cell.setForeground(QBrush(QColor(_INK[color_status])) if color_status in _INK else QBrush())
                sentence = table.item(display, 1)
                font = sentence.font()
                font.setStrikeOut(status == "discarded")
                sentence.setFont(font)
                sentence.setForeground(QBrush(QColor("#888888")) if status == "discarded" else QBrush())
                sentence.setBackground(QBrush(QColor(_COLORS[color_status])) if color_status in _COLORS else QBrush())

    def _refresh_visuals(self, script_status: dict, transcript_status: dict) -> None:
        links = {range_key(link["transcript"]): link["classification"] for link in self.state["links"]}
        indexes = {}
        for panel in ("script", "transcript"):
            rows = group_rows(self.state, panel) if self.state[panel] is not None else []
            indexes[panel] = {range_key(row): self._display_row(panel, index) for index, row in enumerate(rows)}
            statuses = []
            for row in rows:
                key = range_key(row)
                statuses.append(script_status[key] if panel == "script" else links.get(key, transcript_status[key]))
            overview = self._panels[panel]["overview"]
            overview.rows = list(self._table_rows[panel].values())
            overview.statuses = statuses
            overview.setEnabled(bool(rows))
            overview.update()
        self.bands.links = [
            (indexes["script"][range_key(link["script"])],
             indexes["transcript"][range_key(link["transcript"])], link["classification"])
            for link in self.state["links"]
            if range_key(link["script"]) in indexes["script"] and range_key(link["transcript"]) in indexes["transcript"]
        ]
        self.bands.focus_panel = 0 if self._active_panel == "script" else 1
        self.bands.update()

    def _refresh_readings(self, script_row: dict | None, transcript_row: dict | None) -> None:
        self.readings.clear()
        if script_row is None:
            self.readings_label.setText("Readings — select a script grouping")
            return
        self.readings_label.setText(f"Readings for script group {script_row['line']} — exact / partial / flub")
        transcript = active_source(self.state, "transcript")
        rows = {range_key(row): (index, row) for index, row in enumerate(group_rows(self.state, "transcript"))} if transcript else {}
        selected_item = None
        for link in sorted(self.state["links"], key=lambda entry: rows.get(range_key(entry["transcript"]), (-1, None))[0]):
            if link["script"] != word_range(script_row) or range_key(link["transcript"]) not in rows:
                continue
            index, row = rows[range_key(link["transcript"])]
            classification = link["classification"]
            item = QListWidgetItem(
                _status_icon(classification),
                (f"{row['filename']} · " if len(combined.recordings(transcript)) > 1 else "")
                + f"T{row['line']} · {classification.title()} · {_timestamp(row.get('start_ms'))} — {row['text']}",
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setBackground(QColor(_COLORS[classification]))
            item.setForeground(QColor(_INK[classification]))
            if self.state["transcript"] is None:
                item.setToolTip("Load this transcript again to jump to the reading.")
            self.readings.addItem(item)
            if transcript_row and range_key(transcript_row) == range_key(row):
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
                rows = group_rows(self.state, panel) if loaded else []
                widgets["close"].setEnabled(loaded)
                widgets["previous"].setEnabled(loaded and selected is not None and selected > 0)
                widgets["next"].setEnabled(loaded and (selected is None or selected < len(rows) - 1))
                widgets["jump"].setEnabled(loaded)
                widgets["table"].setEnabled(loaded)
                if loaded:
                    reference = source["source"]["path"]
                    widgets["source_label"].setText(f"{Path(reference).name} · {len(rows)} groups · {len(source['words'])} words")
                    widgets["source_label"].setToolTip(reference)
                else:
                    retained = active_source(self.state, panel)
                    text = f"Closed: {Path(retained['source']['path']).name} · review retained" if retained else "No source loaded"
                    widgets["source_label"].setText(text)
                    widgets["source_label"].setToolTip("")
                if selected is not None and loaded:
                    if panel == "transcript":
                        recording = combined.recording_for(source, rows[selected])
                        if recording and recording[1]["source"]["path"] in self._collapsed:
                            self._collapsed.remove(recording[1]["source"]["path"])
                            self._apply_collapsed()
                    display = self._display_row(panel, selected)
                    if widgets["table"].currentRow() != display:
                        widgets["table"].clearSelection()
                        widgets["table"].setCurrentCell(display, 1)
                        widgets["table"].scrollToItem(widgets["table"].item(display, 1))
                    row = rows[selected]
                    detail = f"Selected: group {row['line']} of {len(rows)} · words {row['word_start'] + 1}–{row['word_end'] + 1}"
                    if panel == "transcript":
                        detail += f" · {row['filename']} · {_timestamp(row.get('start_ms'))}–{_timestamp(row.get('end_ms'))}"
                    if self._split_mode == (panel, selected):
                        detail += " · Click a gap to split (←/→, Enter, Esc)"
                    widgets["detail"].setText(detail)
                else:
                    widgets["table"].clearSelection()
                    widgets["detail"].setText("Select a word grouping." if loaded else "")

            script_status = coverage(self.state)
            transcript_status = review_status(self.state)
            review_key = (
                tuple((range_key(link["script"]), range_key(link["transcript"]), link["classification"]) for link in self.state["links"]),
                tuple(range_key(group) for group in self.state["discarded"]),
                tuple(range_key(group) for group in self.state.get("script_discarded", [])),
            )
            if sources_changed or review_key != self._review_key:
                self._paint_statuses(script_status, transcript_status)
                self._review_key = review_key

            script_row = self._selected_row("script")
            transcript_row = self._selected_row("transcript")
            transcript_link = linked_group(self.state, "transcript", transcript_row) if transcript_row else None
            discarded = transcript_row is not None and is_discarded(self.state, transcript_row)
            one_script = len(self._selected_indexes("script")) == 1
            one_transcript = len(self._selected_indexes("transcript")) == 1
            can_link = (
                script_row is not None and transcript_row is not None and not discarded
                and one_script and one_transcript and self._split_mode is None
            )
            self.link_button.setText("Unlink" if transcript_link else "Link")
            self.link_button.setEnabled((transcript_link is not None and one_transcript and self._split_mode is None) or can_link)
            link_key = self.keyboard_mappings["link"]
            self.link_button.setToolTip(
                ("Remove this transcript group's existing link." if transcript_link
                 else "Link with automatic quality, then advance both group selections.")
                + (f" ({link_key})" if link_key else "")
            )
            self.discard_button.setText("Restore" if discarded else "Discard")
            self.discard_button.setEnabled(transcript_row is not None and one_transcript and self._split_mode is None)
            discard_key = self.keyboard_mappings["discard"]
            self.discard_button.setToolTip(
                ("Return this transcript group to review." if discarded
                 else "Discard this transcript group, then advance.")
                + (f" ({discard_key})" if discard_key else "")
            )
            self.pair_label.setText(
                f"Selected pair: S{script_row['line']} → T{transcript_row['line']}"
                if script_row and transcript_row and one_script and one_transcript else "Select one word grouping in each panel."
            )
            self._refresh_audio()
            self._refresh_readings(script_row, transcript_row)

            mapping_path = self.state.get("mapping_path")
            self.mapping_label.setText(
                (f"Line project: {mapping_path}" if mapping_path else "New review · no saved Line project")
                + (f" · Quality labels updated: {self.state['_quality_updates']}" if self.state.get("_quality_updates") else "")
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
                f" · {sum(status == 'discarded' for status in script_status.values())} discarded"
                f"\nTranscript review: {linked} linked · {rejected} discarded · {unreviewed} unreviewed"
            )
            total = len(transcript_status)
            reviewed = linked + rejected
            self.review_progress.setValue(round(100 * reviewed / total) if total else 0)
            self.review_progress.setFormat(f"Transcript reviewed: {reviewed} / {total}")
            self._refresh_visuals(script_status, transcript_status)
        finally:
            self._refreshing = False
