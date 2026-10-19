"""Conversione tra millisecondi e testo hh:mm:ss.mmm."""

from __future__ import annotations

import re

_NUM = r"\d+(?:[.,]\d+)?"
_PATTERN = re.compile(
    rf"^\s*(?:(?:(\d+):)?(\d+):)?({_NUM})\s*$"
)


def format_ms(ms: int | float, with_ms: bool = True) -> str:
    """Restituisce hh:mm:ss.mmm (o hh:mm:ss se with_ms è False)."""
    ms = max(0, int(round(ms)))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    if with_ms:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_compact(ms: int) -> str:
    """Formato breve per i nomi dei file: 01m30s oppure 1h02m03s."""
    total_s = max(0, int(ms // 1000))
    hours, rest = divmod(total_s, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes:02d}m{seconds:02d}s"


def format_seconds(seconds: float) -> str:
    """Secondi in formato ffmpeg (precisione al millisecondo)."""
    return f"{max(0.0, seconds):.3f}"


def parse_time(text: str) -> int | None:
    """Interpreta un tempo digitato dall'utente e restituisce i millisecondi.

    Formati accettati: ``90``, ``90.5``, ``1:30``, ``1:30.250``, ``01:02:03.004``.
    La virgola è accettata come separatore decimale. Restituisce None se il
    testo non è valido.
    """
    if text is None:
        return None
    match = _PATTERN.match(text)
    if not match:
        return None
    hours_s, minutes_s, seconds_s = match.groups()
    seconds = float(seconds_s.replace(",", "."))
    minutes = int(minutes_s) if minutes_s is not None else 0
    hours = int(hours_s) if hours_s is not None else 0
    if minutes_s is not None and seconds >= 60:
        return None
    if hours_s is not None and minutes >= 60:
        return None
    total = (hours * 3600 + minutes * 60) * 1000 + seconds * 1000
    return int(round(total))
