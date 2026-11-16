"""Avvio dell'applicazione."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, APP_VERSION
from .core.ffmpeg import find_ffmpeg
from .i18n import i18n, system_language, tr
from .settings import Settings
from .ui.main_window import MainWindow
from .ui.theme import apply_dark_theme


def _resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [base / "assets" / name, Path(__file__).resolve().parent / "assets" / name]
    return next((c for c in candidates if c.exists()), candidates[-1])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from .linux_integration import audio_backend_fallback, handle_cli
    code = handle_cli(argv[1:])
    if code is not None:
        return code
    audio_backend_fallback()
    if sys.platform.startswith("win"):
        try:  # icona corretta nella barra delle applicazioni
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.{APP_VERSION}")
        except Exception:
            pass

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    if not sys.platform.startswith("win"):
        # collega la finestra al file .desktop (icona corretta su Wayland e nella barra)
        app.setDesktopFileName("simplecutvideo")
    app.setApplicationVersion(APP_VERSION)
    icon = _resource("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    apply_dark_theme(app)

    settings = Settings()
    i18n.set_language(settings.get_str("ui/language", "") or system_language())

    ffmpeg = find_ffmpeg()
    window = MainWindow(settings, ffmpeg)
    window.show()
    if ffmpeg is None:
        message = tr("err_no_ffmpeg")
        if not sys.platform.startswith("win"):
            message += "\n\nsudo apt install ffmpeg"
        QMessageBox.critical(window, APP_NAME, message)

    files = [a for a in argv[1:] if os.path.isfile(a)]
    if files:
        window.open_file(files[0])
    return app.exec()
