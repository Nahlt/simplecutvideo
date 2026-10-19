"""Barra temporale delle selezioni: più intervalli, maniglie trascinabili, righello."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.segments import SelectionModel
from .theme import ACCENT, PLAYHEAD, TEXT_DIM, TRACK, TRACK_BORDER

MARGIN = 12          # spazio ai lati per afferrare le maniglie agli estremi
TRACK_TOP = 6
TRACK_H = 34
HANDLE_W = 7
HIT_PX = 7
TICK_STEPS_S = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]


def _tick_label(ms: int, long_format: bool, fine: bool) -> str:
    total = ms / 1000
    h, rest = divmod(int(total), 3600)
    m, s = divmod(rest, 60)
    if fine:
        frac = f"{total % 1:.1f}"[1:]
        return f"{m:d}:{s:02d}{frac}" if not long_format else f"{h}:{m:02d}:{s:02d}{frac}"
    return f"{h}:{m:02d}:{s:02d}" if long_format else f"{m:d}:{s:02d}"


class RangeBar(QWidget):
    seekRequested = Signal(int)      # clic su un'area vuota o trascinamento
    rangesChanged = Signal()         # una selezione è stata modificata
    activeChanged = Signal()         # cambiata la selezione attiva
    addRequested = Signal(int)       # doppio clic su un'area vuota

    def __init__(self, model: SelectionModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.position = 0
        self._drag: tuple[str, int, int] | None = None   # (modo, indice, offset)
        self.setMouseTracking(True)
        self.setMinimumHeight(TRACK_TOP + TRACK_H + 22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # -------------------------------------------------------- coordinate
    def _usable(self) -> float:
        return max(1.0, self.width() - 2 * MARGIN)

    def _x(self, ms: int) -> float:
        if self.model.duration <= 0:
            return MARGIN
        return MARGIN + self._usable() * ms / self.model.duration

    def _ms(self, x: float) -> int:
        if self.model.duration <= 0:
            return 0
        value = (x - MARGIN) / self._usable() * self.model.duration
        return int(max(0, min(self.model.duration, value)))

    def set_position(self, ms: int) -> None:
        self.position = ms
        self.update()

    # ------------------------------------------------------------ disegno
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(MARGIN, TRACK_TOP, self._usable(), TRACK_H)

        p.setPen(QPen(TRACK_BORDER, 1))
        p.setBrush(TRACK)
        p.drawRoundedRect(track, 4, 4)

        if self.model.duration > 0:
            self._paint_ticks(p, track)
            self._paint_ranges(p, track)
            self._paint_playhead(p, track)
        p.end()

    def _paint_ticks(self, p: QPainter, track: QRectF) -> None:
        duration_s = self.model.duration / 1000
        step = TICK_STEPS_S[-1]
        for candidate in TICK_STEPS_S:
            if self._usable() * candidate / max(duration_s, 0.001) >= 70:
                step = candidate
                break
        long_format = duration_s >= 3600
        fine = step < 1
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        p.setFont(font)
        y = track.bottom()
        t = 0.0
        while t <= duration_s + 1e-6:
            x = self._x(int(t * 1000))
            p.setPen(QPen(TRACK_BORDER, 1))
            p.drawLine(QPointF(x, y + 1), QPointF(x, y + 5))
            label = _tick_label(int(t * 1000), long_format, fine)
            w = p.fontMetrics().horizontalAdvance(label)
            lx = min(max(x - w / 2, 0), self.width() - w)
            p.setPen(TEXT_DIM)
            p.drawText(QPointF(lx, y + 17), label)
            t += step

    def _paint_ranges(self, p: QPainter, track: QRectF) -> None:
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        for i, r in enumerate(self.model.ranges):
            x1, x2 = self._x(r.start), self._x(r.end)
            rect = QRectF(x1, track.top() + 1, max(2.0, x2 - x1), track.height() - 2)
            active = i == self.model.active
            fill = QColor(ACCENT)
            fill.setAlpha(205 if active else 95)
            p.setPen(QPen(ACCENT, 1.5) if active else Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRect(rect)
            label = str(i + 1)
            if rect.width() > p.fontMetrics().horizontalAdvance(label) + 2 * HANDLE_W + 6:
                p.setPen(QColor("#1B1D21") if active else QColor("#F5F6F7"))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            if active:
                for x in (x1, x2):
                    self._paint_handle(p, x, track)

    def _paint_handle(self, p: QPainter, x: float, track: QRectF) -> None:
        handle = QRectF(x - HANDLE_W / 2, track.top() - 3, HANDLE_W, track.height() + 6)
        p.setPen(QPen(QColor("#1B1D21"), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(handle, 2, 2)
        p.setPen(QPen(QColor("#8A8F98"), 1))
        cy = handle.center().y()
        for dy in (-4, 0, 4):
            p.drawLine(QPointF(x - 1.5, cy + dy), QPointF(x + 1.5, cy + dy))

    def _paint_playhead(self, p: QPainter, track: QRectF) -> None:
        x = self._x(self.position)
        p.setPen(QPen(PLAYHEAD, 2))
        p.drawLine(QPointF(x, track.top() - 2), QPointF(x, track.bottom() + 2))
        tri = QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, TRACK_TOP + 1)])
        path = QPainterPath()
        path.addPolygon(tri)
        p.fillPath(path, PLAYHEAD)

    # ------------------------------------------------------------- mouse
    def _hit(self, x: float) -> tuple[str, int] | None:
        """Cosa c'è sotto il puntatore: maniglia, corpo di una selezione o niente."""
        order = list(range(len(self.model.ranges)))
        if self.model.active is not None:
            order.remove(self.model.active)
            order.insert(0, self.model.active)
        for i in order:
            r = self.model.ranges[i]
            if abs(x - self._x(r.start)) <= HIT_PX:
                return "start", i
            if abs(x - self._x(r.end)) <= HIT_PX:
                return "end", i
        for i, r in enumerate(self.model.ranges):
            if self._x(r.start) < x < self._x(r.end):
                return "move", i
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.model.duration <= 0:
            return
        x = event.position().x()
        hit = self._hit(x)
        if hit is None:
            self._drag = ("seek", -1, 0)
            self.seekRequested.emit(self._ms(x))
            return
        mode, index = hit
        if index != self.model.active:
            self.model.set_active(index)
            self.activeChanged.emit()
        offset = self._ms(x) - self.model.ranges[index].start
        self._drag = (mode, index, offset)
        self.update()

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._drag is None:
            hit = self._hit(x) if self.model.duration > 0 else None
            if hit and hit[0] in ("start", "end"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        mode, index, offset = self._drag
        ms = self._ms(x)
        if mode == "seek":
            self.seekRequested.emit(ms)
            return
        if mode == "start":
            value = self.model.set_start(index, ms)
        elif mode == "end":
            value = self.model.set_end(index, ms)
        else:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            value = self.model.move(index, ms - offset)
        self.rangesChanged.emit()
        self.seekRequested.emit(value)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.model.duration > 0:
            if self._hit(event.position().x()) is None:
                self.addRequested.emit(self._ms(event.position().x()))
