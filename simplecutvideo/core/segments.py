"""Selezioni multiple sulla linea temporale e calcolo delle parti in output.

Tutti i tempi sono in millisecondi interi.

Le selezioni sono intervalli [start, end) ordinati e mai sovrapposti.
Il file viene diviso in parti alternate:

    fuori(0→sel1.start)  sel1  fuori(sel1.end→sel2.start)  sel2  ...  fuori(selN.end→fine)

Le parti di lunghezza trascurabile (es. una selezione che parte da 0) vengono
omesse, così con una sola selezione si ottengono due o tre parti.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import count

from .timecode import format_compact

MIN_RANGE_MS = 100      # durata minima di una selezione
MIN_PIECE_MS = 50       # sotto questa durata una parte viene ignorata
DEFAULT_NEW_RANGE_MS = 10_000

_ids = count(1)


@dataclass
class Range:
    start: int
    end: int
    uid: int = field(default_factory=lambda: next(_ids))

    @property
    def length(self) -> int:
        return self.end - self.start

    def contains(self, ms: int) -> bool:
        return self.start <= ms < self.end


class SelectionModel:
    """Elenco ordinato di selezioni con una selezione attiva."""

    def __init__(self, duration: int = 0):
        self.duration = max(0, int(duration))
        self.ranges: list[Range] = []
        self.active: int | None = None

    # ------------------------------------------------------------------ base
    def reset(self, duration: int, full_range: bool = True) -> None:
        self.duration = max(0, int(duration))
        self.ranges = []
        self.active = None
        if full_range and self.duration >= MIN_RANGE_MS:
            self.ranges.append(Range(0, self.duration))
            self.active = 0

    def active_range(self) -> Range | None:
        if self.active is None or not (0 <= self.active < len(self.ranges)):
            return None
        return self.ranges[self.active]

    def set_active(self, index: int | None) -> None:
        if index is None or 0 <= index < len(self.ranges):
            self.active = index

    def index_at(self, ms: int) -> int | None:
        for i, r in enumerate(self.ranges):
            if r.contains(ms):
                return i
        return None

    def _bounds(self, index: int) -> tuple[int, int]:
        """Limiti entro cui può muoversi la selezione `index` (vicine escluse)."""
        low = self.ranges[index - 1].end if index > 0 else 0
        high = self.ranges[index + 1].start if index + 1 < len(self.ranges) else self.duration
        return low, high

    # ------------------------------------------------------------- modifiche
    def add_at(self, position: int, length: int = DEFAULT_NEW_RANGE_MS) -> int | None:
        """Aggiunge una selezione che parte da `position`.

        La fine viene limitata dalla selezione successiva o dalla fine del file.
        Restituisce l'indice della nuova selezione, oppure None se in quel punto
        non c'è spazio (posizione dentro una selezione esistente o troppo vicina).
        """
        position = max(0, min(int(position), self.duration))
        if self.index_at(position) is not None:
            return None
        next_start = self.duration
        for r in self.ranges:
            if r.start >= position:
                next_start = r.start
                break
        start = position
        end = min(position + max(length, MIN_RANGE_MS), next_start)
        if end - start < MIN_RANGE_MS:
            # prova ad allargarsi verso sinistra, se lo spazio lo consente
            prev_end = max((r.end for r in self.ranges if r.end <= position), default=0)
            start = max(prev_end, end - MIN_RANGE_MS)
            if end - start < MIN_RANGE_MS:
                return None
        new = Range(start, end)
        self.ranges.append(new)
        self.ranges.sort(key=lambda r: r.start)
        self.active = self.ranges.index(new)
        return self.active

    def remove(self, index: int) -> None:
        if not (0 <= index < len(self.ranges)):
            return
        del self.ranges[index]
        if not self.ranges:
            self.active = None
        else:
            self.active = min(index, len(self.ranges) - 1)

    def set_start(self, index: int, ms: int) -> int:
        """Imposta l'inizio rispettando i vincoli; restituisce il valore applicato."""
        r = self.ranges[index]
        low, _ = self._bounds(index)
        r.start = max(low, min(int(ms), r.end - MIN_RANGE_MS))
        return r.start

    def set_end(self, index: int, ms: int) -> int:
        r = self.ranges[index]
        _, high = self._bounds(index)
        r.end = min(high, max(int(ms), r.start + MIN_RANGE_MS))
        return r.end

    def place_start(self, index: int, ms: int) -> int:
        """Come set_start, ma se l'inizio supera la fine sposta anche la fine
        (mantenendo la durata, entro i limiti). Usato da tastiera e campi."""
        r = self.ranges[index]
        if ms > r.end - MIN_RANGE_MS:
            _, high = self._bounds(index)
            r.end = min(high, int(ms) + r.length)
        return self.set_start(index, ms)

    def place_end(self, index: int, ms: int) -> int:
        """Come set_end, ma se la fine precede l'inizio sposta anche l'inizio."""
        r = self.ranges[index]
        if ms < r.start + MIN_RANGE_MS:
            low, _ = self._bounds(index)
            r.start = max(low, int(ms) - r.length)
        return self.set_end(index, ms)

    def move(self, index: int, new_start: int) -> int:
        """Sposta l'intera selezione mantenendone la durata."""
        r = self.ranges[index]
        low, high = self._bounds(index)
        length = r.length
        start = max(low, min(int(new_start), high - length))
        r.start, r.end = start, start + length
        return start

    def total_selected(self) -> int:
        return sum(r.length for r in self.ranges)


# ----------------------------------------------------------------- parti
@dataclass(frozen=True)
class Piece:
    """Una parte del file: una selezione oppure un tratto fuori selezione."""

    start: int
    end: int
    selected: bool          # True = selezione, False = fuori selezione
    sel_number: int | None  # numero della selezione (1-based) se selected
    number: int             # numero progressivo della parte (1-based)

    @property
    def length(self) -> int:
        return self.end - self.start


def build_pieces(duration: int, ranges: list[Range]) -> list[Piece]:
    pieces: list[Piece] = []
    cursor = 0

    def add(start: int, end: int, selected: bool, sel_number: int | None) -> None:
        if end - start >= MIN_PIECE_MS:
            pieces.append(Piece(start, end, selected, sel_number, len(pieces) + 1))

    for i, r in enumerate(sorted(ranges, key=lambda x: x.start), start=1):
        add(cursor, r.start, False, None)
        add(r.start, r.end, True, i)
        cursor = r.end
    add(cursor, duration, False, None)
    return pieces


# ------------------------------------------------------------- nomi file
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """Rende un nome valido per Windows (caratteri vietati sostituiti con _)."""
    name = _INVALID_CHARS.sub("_", name).strip().rstrip(".")
    stem = name.split(".")[0].upper()
    if stem in _RESERVED:
        name = "_" + name
    return name


def piece_filename(stem: str, piece: Piece, ext: str) -> str:
    return sanitize_filename(
        f"{stem}_{piece.number:02d}_{format_compact(piece.start)}-{format_compact(piece.end)}.{ext}"
    )


def merged_filename(stem: str, ext: str) -> str:
    return sanitize_filename(f"{stem}_cut.{ext}")


def ensure_extension(name: str, ext: str) -> str:
    """Aggiunge o corregge l'estensione del nome file."""
    name = sanitize_filename(name)
    base, dot, current = name.rpartition(".")
    if dot and current.lower() in {"mp4", "mp3", "m4v", "mov", "mkv"}:
        name = base
    return f"{name}.{ext}"
