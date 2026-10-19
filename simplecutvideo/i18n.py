"""Gestione della lingua dell'interfaccia (cambio a caldo, senza riavvio)."""

from __future__ import annotations

from PySide6.QtCore import QLocale, QObject, Signal

from .translations import STRINGS

LANGUAGES = [
    ("it", "Italiano"),
    ("en", "English"),
    ("fr", "Français"),
    ("es", "Español"),
    ("de", "Deutsch"),
]
FALLBACK = "en"


def system_language() -> str:
    code = QLocale.system().name()[:2].lower()
    return code if code in STRINGS else FALLBACK


class I18n(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.language = FALLBACK

    def set_language(self, code: str) -> None:
        if code not in STRINGS:
            code = FALLBACK
        if code != self.language:
            self.language = code
            self.changed.emit()

    def tr(self, key: str, **kwargs) -> str:
        text = STRINGS[self.language].get(key) or STRINGS[FALLBACK].get(key) or key
        return text.format(**kwargs) if kwargs else text


i18n = I18n()


def tr(key: str, **kwargs) -> str:
    return i18n.tr(key, **kwargs)
