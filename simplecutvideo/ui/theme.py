"""Tema scuro (stile Fusion) e colori condivisi."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = QColor("#F2A93B")
ACCENT_DARK = QColor("#C9862A")
PLAYHEAD = QColor("#F5F6F7")
TRACK = QColor("#2A2D33")
TRACK_BORDER = QColor("#3A3E46")
TEXT_DIM = QColor("#9AA0A9")
BG = QColor("#1B1D21")
SURFACE = QColor("#23262B")
ERROR = QColor("#E5534B")

STYLESHEET = f"""
QToolTip {{ color: #E8EAED; background: #2D3036; border: 1px solid #44484F; padding: 4px; }}
QGroupBox {{ border: 1px solid #33363C; border-radius: 6px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_DIM.name()}; }}
QPushButton {{ padding: 5px 12px; }}
QPushButton#primary {{
    background: {ACCENT.name()}; color: #1B1D21; font-weight: 600;
    border: none; border-radius: 4px; padding: 8px 20px;
}}
QPushButton#primary:hover {{ background: #F7BB5C; }}
QPushButton#primary:disabled {{ background: #4A4D53; color: #7A7F87; }}
QLineEdit[invalid="true"] {{ border: 1px solid {ERROR.name()}; }}
QLabel#timeLabel {{ font-family: Consolas, 'Cascadia Mono', monospace; font-size: 13px; }}
QLabel#hint {{ color: {TEXT_DIM.name()}; }}
QLabel#sectionTitle {{ font-weight: 600; }}
QListWidget {{ background: {SURFACE.name()}; border: 1px solid #33363C; border-radius: 4px; }}
QListWidget::item {{ padding: 3px 4px; }}
QListWidget::item:selected {{ background: {ACCENT_DARK.name()}; color: #111; }}
QTableWidget {{ gridline-color: #33363C; }}
"""


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, BG)
    p.setColor(QPalette.ColorRole.WindowText, QColor("#E8EAED"))
    p.setColor(QPalette.ColorRole.Base, SURFACE)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#2A2D32"))
    p.setColor(QPalette.ColorRole.Text, QColor("#E8EAED"))
    p.setColor(QPalette.ColorRole.Button, QColor("#2C2F35"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#E8EAED"))
    p.setColor(QPalette.ColorRole.Highlight, ACCENT)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#111111"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2D3036"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#E8EAED"))
    p.setColor(QPalette.ColorRole.PlaceholderText, TEXT_DIM)
    p.setColor(QPalette.ColorRole.Link, ACCENT)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6B7078"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#202227"))
    app.setPalette(p)
    app.setStyleSheet(STYLESHEET)
