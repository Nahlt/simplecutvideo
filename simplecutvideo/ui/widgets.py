"""Widget di supporto: slider con salto al clic e campo tempo hh:mm:ss.mmm."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLineEdit, QSlider, QStyle, QStyleOptionSlider

from ..core.timecode import format_ms, parse_time


class ClickSlider(QSlider):
    """Slider orizzontale che salta subito al punto cliccato."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
            )
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(
                    QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self
                )
                span = max(1, groove.width() - handle.width())
                x = int(event.position().x()) - groove.x() - handle.width() // 2
                value = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), x, span, opt.upsideDown
                )
                self.setValue(value)
                self.sliderMoved.emit(value)
        super().mousePressEvent(event)


class TimeEdit(QLineEdit):
    """Campo per un tempo; emette msEdited con il valore digitato (già valido)."""

    msEdited = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ms = 0
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.setFont(font)
        self.setPlaceholderText("hh:mm:ss.mmm")
        self.setFixedWidth(self.fontMetrics().horizontalAdvance("00:00:00.0000") + 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editingFinished.connect(self._commit)
        self.setText(format_ms(0))

    def set_ms(self, ms: int) -> None:
        self._ms = int(ms)
        if not self.hasFocus() or self.text() == "":
            self.setText(format_ms(ms))
        self._set_invalid(False)

    def force_ms(self, ms: int) -> None:
        self._ms = int(ms)
        self.setText(format_ms(ms))
        self._set_invalid(False)

    def ms(self) -> int:
        return self._ms

    def _commit(self) -> None:
        value = parse_time(self.text())
        if value is None:
            self._set_invalid(True)
            QTimer.singleShot(1200, lambda: (self.force_ms(self._ms)))
            return
        if value != self._ms:
            self.msEdited.emit(value)
        else:
            self.force_ms(self._ms)

    def _set_invalid(self, invalid: bool) -> None:
        self.setProperty("invalid", invalid)
        self.style().unpolish(self)
        self.style().polish(self)
