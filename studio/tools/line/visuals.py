"""Mapping visualizations, implemented directly with Qt (no diff calculation)."""

from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


COLORS = {"exact": "#c8ecd5", "partial": "#fff0b3", "flub": "#f7cece"}
MARKERS = {"exact": "#479b65", "partial": "#d4a619", "flub": "#ce6262",
           "missing": "#8b9199", "unreviewed": "#8b9199", "discarded": "#c7cbd0"}


def watch_table(observer, table):
    """Follow viewport geometry, including delayed wrapped-row measurement."""
    for widget in (table, table.viewport(), table.horizontalHeader()):
        widget.installEventFilter(observer)
    table.verticalScrollBar().valueChanged.connect(lambda *_: observer.update())
    table.verticalScrollBar().rangeChanged.connect(lambda *_: observer.update())
    table.verticalHeader().sectionResized.connect(lambda *_: observer.update())
    table.horizontalHeader().sectionResized.connect(lambda *_: observer.update())


class OverviewStrip(QWidget):
    """A document-height map next to a table's scrollbar."""

    activated = Signal(int)

    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.table = table
        self.statuses = []
        self.rows = None
        self._heights = {}
        self.setFixedWidth(18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click or drag to select a group. Green: Exact; yellow: Partial; red: Flub. "
                        "Grey: missing/unreviewed; pale inset marks: discarded. Outline: visible area.")
        watch_table(self, table)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                            QEvent.Type.FontChange, QEvent.Type.LayoutRequest):
            self.update()
        return False

    def track(self):
        viewport = self.table.viewport()
        y = self.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0))).y()
        return QRectF(1, y, self.width() - 2, viewport.height())

    def boundaries(self):
        edges = [0]
        for row in self.document_rows():
            if not self.table.isRowHidden(row):
                self._heights[row] = self.table.rowHeight(row)
            edges.append(edges[-1] + self._heights.get(row, self.table.verticalHeader().defaultSectionSize()))
        return edges

    def document_rows(self):
        return self.rows if self.rows is not None else list(range(self.table.rowCount()))

    def row_at(self, y):
        edges, track = self.boundaries(), self.track()
        if len(edges) < 2 or track.height() <= 0 or edges[-1] <= 0:
            return None
        position = max(0, min(1, (y - track.top()) / track.height())) * edges[-1]
        return self.document_rows()[min(len(edges) - 2, bisect_right(edges, position) - 1)]

    def _activate(self, event):
        row = self.row_at(event.position().y())
        if row is not None:
            self.activated.emit(row)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._activate(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._activate(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        track, edges = self.track(), self.boundaries()
        painter.setClipRect(track)
        painter.fillRect(track, self.palette().base())
        if len(edges) < 2 or edges[-1] <= 0:
            return
        scale = track.height() / edges[-1]
        for row, status in enumerate(self.statuses[:len(edges) - 1]):
            top = track.top() + edges[row] * scale
            height = max(2, (edges[row + 1] - edges[row]) * scale - 1)
            inset = 4 if status == "discarded" else 1
            painter.fillRect(QRectF(track.left() + inset, top, track.width() - 2 * inset, height),
                             QColor(MARKERS.get(status, MARKERS["unreviewed"])))
        visible = [(i, row) for i, row in enumerate(self.document_rows()) if not self.table.isRowHidden(row)
                   and self.table.rowViewportPosition(row) + self.table.rowHeight(row) > 0
                   and self.table.rowViewportPosition(row) < self.table.viewport().height()]
        visible_top = visible_end = 0
        if visible:
            i, row = visible[0]
            visible_top = edges[i] + max(0, -self.table.rowViewportPosition(row))
            i, row = visible[-1]
            visible_end = min(edges[i + 1], edges[i] + self.table.viewport().height() - self.table.rowViewportPosition(row))
        painter.setPen(QPen(self.palette().text().color(), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(track.left() + .5, track.top() + visible_top * scale,
                                track.width() - 1, max(2, (visible_end - visible_top) * scale)))
        current = self.table.currentRow()
        row = self.document_rows().index(current) if current in self.document_rows() else -1
        if self.table.selectedItems() and 0 <= row < len(edges) - 1:
            y = track.top() + (edges[row] + edges[row + 1]) * .5 * scale
            painter.setPen(QPen(QColor("#1671bd"), 2))
            painter.drawLine(QPoint(0, round(y)), QPoint(self.width(), round(y)))


class ConnectionBands(QWidget):
    """Curved filled bands between actual table row rectangles."""

    def __init__(self, script_table, transcript_table, parent=None):
        super().__init__(parent)
        self.tables = (script_table, transcript_table)
        self.links = []  # (script row, transcript row, classification)
        self.focus_panel = 0
        self.setFixedWidth(88)
        self.setToolTip("Connections between script groups and transcript readings. "
                        "Select a group to emphasize its connections. Edge ticks indicate offscreen endpoints.")
        for table in self.tables:
            watch_table(self, table)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                            QEvent.Type.FontChange, QEvent.Type.LayoutRequest):
            self.update()
        return False

    def endpoint(self, side, row):
        table = self.tables[side]
        viewport = table.viewport()
        origin = self.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0))).y()
        top = origin + table.rowViewportPosition(row)
        bottom = top + table.rowHeight(row)
        lower, upper = origin, origin + viewport.height()
        offscreen = -1 if bottom <= lower else 1 if top >= upper else 0
        if offscreen:
            edge = lower if offscreen < 0 else upper
            return edge - (3 if offscreen > 0 else 0), edge + (3 if offscreen < 0 else 0), offscreen
        return max(lower, top), min(upper, bottom), 0

    def emphasized(self, link):
        table = self.tables[self.focus_panel]
        rows = {index.row() for index in table.selectionModel().selectedRows()}
        return not rows or link[self.focus_panel] in rows

    def paintEvent(self, event):
        if any(table.rowCount() == 0 or not table.isEnabled() for table in self.tables):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        # Related bands are drawn last so crossing readings remain inspectable.
        for link in sorted(self.links, key=self.emphasized):
            left, right, quality = link
            if left >= self.tables[0].rowCount() or right >= self.tables[1].rowCount():
                continue
            if self.tables[0].isRowHidden(left) or self.tables[1].isRowHidden(right):
                continue
            lt, lb, lo = self.endpoint(0, left)
            rt, rb, ro = self.endpoint(1, right)
            if lo and lo == ro:
                continue
            path = QPainterPath()
            path.moveTo(0, lt)
            path.cubicTo(width * .5, lt, width * .5, rt, width, rt)
            path.lineTo(width, rb)
            path.cubicTo(width * .5, rb, width * .5, lb, 0, lb)
            path.closeSubpath()
            selected = self.emphasized(link)
            color = QColor(MARKERS.get(quality, "#8b9199"))
            color.setAlpha(125 if selected else 24)
            painter.fillPath(path, color)
            color.setAlpha(210 if selected else 40)
            painter.setPen(QPen(color, 1))
            painter.drawPath(path)
            for x, top, bottom, offscreen in ((1, lt, lb, lo), (width - 1, rt, rb, ro)):
                if offscreen:
                    painter.setPen(QPen(color, 3))
                    painter.drawLine(QPoint(x, round(top)), QPoint(x, round(bottom)))
