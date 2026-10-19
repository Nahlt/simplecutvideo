"""Traduce le scelte dell'utente in comandi FFmpeg e stima le dimensioni."""

from __future__ import annotations

import bisect
import os
from dataclasses import dataclass, field

from .ffmpeg import EFFICIENT_CODECS, EncoderInfo, MediaInfo
from .segments import Piece
from .timecode import format_seconds

FMT_MP4 = "mp4"
FMT_MP3 = "mp3"
MODE_PRECISE = "precise"
MODE_FAST = "fast"

SEEK_MARGIN_S = 0.2

MP3_BITRATES = (96, 128, 160, 192, 256, 320)
DEFAULT_MP3_BITRATE = 192

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv"
)


@dataclass
class ExportSettings:
    fmt: str = FMT_MP4
    mode: str = MODE_PRECISE
    mp3_bitrate: int = DEFAULT_MP3_BITRATE
    use_gpu: bool = True
    merge: bool = False


@dataclass
class Step:
    """Una singola esecuzione di ffmpeg."""

    args: list[str]
    weight_ms: int          # durata elaborata, per la barra di avanzamento
    output: str             # file prodotto da questo passo


@dataclass
class Job:
    """Un file finale da produrre, eventualmente in più passi."""

    output: str
    pieces: list[Piece]
    steps: list[Step]
    temp_files: list[str] = field(default_factory=list)
    prepare_files: dict[str, str] = field(default_factory=dict)  # file di testo da scrivere prima

    @property
    def weight_ms(self) -> int:
        return sum(s.weight_ms for s in self.steps)


# ------------------------------------------------------------- parametri
def video_target_bitrate(info: MediaInfo) -> int:
    """Bitrate video per la ricodifica: qualità equivalente all'originale."""
    factor = 1.5 if info.vcodec in EFFICIENT_CODECS else 1.0
    return int(min(max(info.v_bitrate * factor, 300_000), 100_000_000))


def audio_target_bitrate(info: MediaInfo) -> int:
    if not info.a_bitrate:
        return 192_000
    return int(min(max(info.a_bitrate, 128_000), 320_000))


def _video_codec_args(info: MediaInfo, encoder: EncoderInfo) -> list[str]:
    rate = video_target_bitrate(info)
    common = ["-b:v", str(rate), "-maxrate", str(int(rate * 1.5)), "-bufsize", str(rate * 2)]
    if encoder.name == "libx264":
        codec = ["-c:v", "libx264", "-preset", "medium"]
    elif encoder.name == "h264_nvenc":
        codec = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr"]
    elif encoder.name == "h264_qsv":
        codec = ["-c:v", "h264_qsv", "-preset", "medium"]
    elif encoder.name == "h264_amf":
        codec = ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "vbr_peak"]
    else:
        codec = ["-c:v", encoder.name]
    return codec + common + ["-pix_fmt", encoder.pix_fmt]


def _hdr_args(info: MediaInfo, has_tonemap: bool) -> tuple[str | None, list[str]]:
    """Filtro di conversione HDR→SDR e relativi metadati colore."""
    if info.is_hdr and has_tonemap:
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
        return TONEMAP_CHAIN, tags
    return None, []


def _input_args(info: MediaInfo, piece: Piece) -> list[str]:
    return [
        "-ss", format_seconds(piece.start / 1000),
        "-t", format_seconds(piece.length / 1000),
        "-i", info.path,
    ]


def _head() -> list[str]:
    return ["-hide_banner", "-nostdin", "-y"]


def _tail(output: str, mp4: bool = True) -> list[str]:
    extra = ["-movflags", "+faststart"] if mp4 else []
    return extra + ["-progress", "pipe:1", "-nostats", output]


def _audio_mp4_args(info: MediaInfo) -> list[str]:
    if not info.has_audio:
        return []
    return ["-c:a", "aac", "-b:a", str(audio_target_bitrate(info))]


# ----------------------------------------------------------- precise MP4
def _precise_mp4(ffmpeg: str, info: MediaInfo, pieces: list[Piece], output: str,
                 encoder: EncoderInfo, has_tonemap: bool) -> list[str]:
    vfilter, color_tags = _hdr_args(info, has_tonemap)
    args = [ffmpeg, *_head()]
    for piece in pieces:
        args += _input_args(info, piece)

    v, a_idx = info.v_index, info.a_index
    if len(pieces) == 1:
        args += ["-map", f"0:{v}"]
        if info.has_audio:
            args += ["-map", f"0:{a_idx}"]
        if vfilter:
            args += ["-vf", vfilter]
    else:
        n = len(pieces)
        a = 1 if info.has_audio else 0
        inputs = "".join(
            f"[{i}:{v}]" + (f"[{i}:{a_idx}]" if a else "") for i in range(n)
        )
        graph = f"{inputs}concat=n={n}:v=1:a={a}[vc]" + ("[ac]" if a else "")
        if vfilter:
            graph += f";[vc]{vfilter}[v]"
            vout = "[v]"
        else:
            vout = "[vc]"
        args += ["-filter_complex", graph, "-map", vout]
        if a:
            args += ["-map", "[ac]"]

    args += _video_codec_args(info, encoder) + color_tags + _audio_mp4_args(info)
    args += ["-sn", "-dn", "-map_chapters", "-1"]
    return args + _tail(output)


# -------------------------------------------------------------- fast MP4
def snap_start(piece: Piece, keyframes: list[float] | None) -> float:
    """Inizio effettivo (secondi) di una parte nel taglio veloce.

    Senza ricodifica il video può iniziare solo su un fotogramma chiave: si usa
    quello più vicino all'inizio richiesto, purché resti prima della fine.
    """
    start = piece.start / 1000
    if not keyframes or piece.start == 0:
        return start
    end = piece.end / 1000
    i = bisect.bisect_left(keyframes, start)
    candidates = [k for k in keyframes[max(0, i - 1): i + 1] if k < end - 0.1]
    if not candidates:
        before = [k for k in keyframes[:i] if k < end - 0.1]
        return before[-1] if before else start
    return min(candidates, key=lambda k: (abs(k - start), k > start))


def _fast_mp4(ffmpeg: str, info: MediaInfo, piece: Piece, output: str,
              keyframes: list[float] | None) -> list[str]:
    start_s = snap_start(piece, keyframes)
    # FFmpeg arretra la ricerca di ~0,13 s con alcuni contenitori (es. MKV con
    # B-frame): si punta leggermente oltre il keyframe scelto per atterrare su
    # di esso e non su quello precedente. I fotogrammi tra il keyframe e il
    # punto di ricerca restano nel file (copia), quindi il video parte dal keyframe.
    seek = start_s + SEEK_MARGIN_S if keyframes and piece.start else start_s
    duration_s = piece.end / 1000 - seek
    args = [
        ffmpeg, *_head(),
        "-ss", f"{seek:.6f}", "-t", f"{duration_s:.6f}", "-i", info.path,
        "-map", f"0:{info.v_index}",
    ]
    if info.has_audio:
        args += ["-map", f"0:{info.a_index}"]
    args += ["-c:v", "copy"]
    if info.vcodec == "hevc":
        args += ["-tag:v", "hvc1"]   # compatibilità con lettori Apple/Windows
    if info.has_audio:
        args += ["-c:a", "copy"] if info.audio_copy_ok else _audio_mp4_args(info)
    args += ["-avoid_negative_ts", "make_zero", "-sn", "-dn", "-map_chapters", "-1"]
    return args + _tail(output)


def _concat_list(paths: list[str]) -> str:
    lines = []
    for p in paths:
        safe = p.replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- MP3
def _mp3(ffmpeg: str, info: MediaInfo, pieces: list[Piece], output: str, kbps: int) -> list[str]:
    args = [ffmpeg, *_head()]
    for piece in pieces:
        args += _input_args(info, piece)
    if len(pieces) == 1:
        args += ["-map", f"0:{info.a_index}"]
    else:
        n = len(pieces)
        inputs = "".join(f"[{i}:{info.a_index}]" for i in range(n))
        args += ["-filter_complex", f"{inputs}concat=n={n}:v=0:a=1[a]", "-map", "[a]"]
    args += ["-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k"]
    if info.a_channels and info.a_channels > 2:
        args += ["-ac", "2"]                       # MP3 supporta al massimo 2 canali
    if info.a_sample_rate and info.a_sample_rate > 48_000:
        args += ["-ar", "48000"]                   # e al massimo 48 kHz
    args += ["-id3v2_version", "3"]
    return args + _tail(output, mp4=False)


# ------------------------------------------------------------ pianificazione
def plan_export(
    ffmpeg: str,
    info: MediaInfo,
    outputs: list[tuple[str, list[Piece]]],
    settings: ExportSettings,
    encoder: EncoderInfo,
    has_tonemap: bool,
    temp_dir: str | None = None,
    keyframes: list[float] | None = None,
) -> list[Job]:
    """Crea l'elenco dei lavori.

    `outputs` contiene coppie (percorso di destinazione, parti da includere):
    una coppia per parte se i file sono separati, una sola coppia con tutte le
    parti se l'utente ha scelto di unirle.
    """
    jobs: list[Job] = []
    for out_path, pieces in outputs:
        total = sum(p.length for p in pieces)
        if settings.fmt == FMT_MP3:
            step = Step(_mp3(ffmpeg, info, pieces, out_path, settings.mp3_bitrate), total, out_path)
            jobs.append(Job(out_path, pieces, [step]))
        elif settings.mode == MODE_PRECISE:
            args = _precise_mp4(ffmpeg, info, pieces, out_path, encoder, has_tonemap)
            jobs.append(Job(out_path, pieces, [Step(args, total, out_path)]))
        elif len(pieces) == 1:
            step = Step(_fast_mp4(ffmpeg, info, pieces[0], out_path, keyframes), total, out_path)
            jobs.append(Job(out_path, pieces, [step]))
        else:
            if temp_dir is None:
                raise ValueError("temp_dir richiesto per unire parti in modalità veloce")
            stem = os.path.splitext(os.path.basename(out_path))[0]
            temps = [os.path.join(temp_dir, f"{stem}_part{i:02d}.mp4") for i in range(len(pieces))]
            steps = [
                Step(_fast_mp4(ffmpeg, info, p, t, keyframes), p.length, t)
                for p, t in zip(pieces, temps)
            ]
            list_path = os.path.join(temp_dir, f"{stem}_list.txt")
            concat = [
                ffmpeg, *_head(), "-f", "concat", "-safe", "0", "-i", list_path,
                "-map", "0", "-c", "copy", *_tail(out_path),
            ]
            steps.append(Step(concat, max(total // 10, 1), out_path))
            jobs.append(
                Job(
                    out_path, pieces, steps,
                    temp_files=temps + [list_path],
                    prepare_files={list_path: _concat_list(temps)},
                )
            )
    return jobs


# --------------------------------------------------------------- stime
def estimate_bytes(info: MediaInfo, duration_ms: int, settings: ExportSettings) -> int:
    """Stima della dimensione di un file in output di durata `duration_ms`."""
    seconds = duration_ms / 1000
    if settings.fmt == FMT_MP3:
        return int(settings.mp3_bitrate * 1000 / 8 * seconds) + 1024
    if settings.mode == MODE_FAST:
        if info.duration_ms <= 0:
            return 0
        return int(info.size_bytes * duration_ms / info.duration_ms)
    audio = audio_target_bitrate(info) if info.has_audio else 0
    return int((video_target_bitrate(info) + audio) / 8 * seconds * 1.02)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit in ("B", "KB"):
                return f"{size:.0f} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
