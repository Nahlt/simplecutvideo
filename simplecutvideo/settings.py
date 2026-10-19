"""Impostazioni portabili: file .ini accanto all'eseguibile, con ripiego su %APPDATA%."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings

from . import APP_NAME


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _writable(path: Path) -> bool:
    try:
        with open(path, "a", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False


def settings_path() -> Path:
    portable = _app_dir() / f"{APP_NAME}.ini"
    if _writable(portable):
        return portable
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    folder = Path(base) / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{APP_NAME}.ini"


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or settings_path()
        self._q = QSettings(str(self.path), QSettings.Format.IniFormat)

    def get_str(self, key: str, default: str = "") -> str:
        value = self._q.value(key, default)
        return str(value) if value is not None else default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._q.value(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self._q.value(key, default)
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return bool(value)

    def get_bytes(self, key: str):
        return self._q.value(key)

    def set(self, key: str, value) -> None:
        self._q.setValue(key, value)

    def sync(self) -> None:
        self._q.sync()
