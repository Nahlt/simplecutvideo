"""Individuazione di FFmpeg, analisi dei file (ffprobe) e rilevamento encoder."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
EXE = ".exe" if IS_WINDOWS else ""

# Codec che possono essere copiati così come sono in un contenitore MP4
MP4_VIDEO_COPY_OK = {"h264", "hevc", "mpeg4", "av1", "vp9", "mpeg2video"}
MP4_AUDIO_COPY_OK = {"aac", "mp3", "ac3", "eac3", "opus", "alac", "flac"}

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
# Codec più efficienti di H.264: in ricodifica serve più bitrate per pari qualità
EFFICIENT_CODECS = {"hevc", "vp9", "av1"}

# (nome encoder, pixel format, descrizione), in ordine di preferenza
if IS_WINDOWS:
    HW_ENCODERS = [
        ("h264_nvenc", "yuv420p", "NVIDIA NVENC"),
        ("h264_qsv", "nv12", "Intel Quick Sync"),
        ("h264_amf", "nv12", "AMD AMF"),
    ]
else:
    # Su Linux AMF non esiste: le schede Intel e AMD si usano tramite VA-API
    HW_ENCODERS = [
        ("h264_nvenc", "yuv420p", "NVIDIA NVENC"),
        ("h264_vaapi", "vaapi", "VA-API (Intel/AMD)"),
        ("h264_qsv", "nv12", "Intel Quick Sync"),
    ]
SW_ENCODER = ("libx264", "yuv420p", "CPU (x264)")
VAAPI_UPLOAD = "format=nv12,hwupload"


def vaapi_device() -> str | None:
    """Primo dispositivo di rendering DRM (es. /dev/dri/renderD128), se presente."""
    devices = sorted(glob.glob("/dev/dri/renderD*"))
    return devices[0] if devices else None


def subprocess_env() -> dict[str, str]:
    """Ambiente pulito per i programmi esterni (xdg-open, dbus-send, ffmpeg).

    Il pacchetto PyInstaller può impostare LD_LIBRARY_PATH sulle proprie
    librerie: i programmi di sistema lanciati da qui devono invece usare
    quelle del sistema, altrimenti possono non partire.
    """
    env = dict(os.environ)
    if IS_WINDOWS:
        return env
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        env["LD_LIBRARY_PATH"] = original
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _no_window_kwargs() -> dict:
    """Evita la comparsa di finestre console su Windows."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"env": subprocess_env()}


def run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        **_no_window_kwargs(),
    )


# ------------------------------------------------------------ localizzazione
def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("SIMPLECUTVIDEO_FFMPEG")
    if env:
        dirs.append(Path(env))
    appdir = os.environ.get("APPDIR")
    if appdir:  # dentro un'AppImage
        dirs.append(Path(appdir) / "ffmpeg")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir / "ffmpeg")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / "ffmpeg")
    project_root = Path(__file__).resolve().parents[2]
    dirs.append(project_root / "ffmpeg" / "bin")
    dirs.append(project_root / "ffmpeg")
    return dirs


@dataclass(frozen=True)
class FFmpegPaths:
    ffmpeg: str
    ffprobe: str


def find_ffmpeg() -> FFmpegPaths | None:
    for d in _candidate_dirs():
        ff, fp = d / f"ffmpeg{EXE}", d / f"ffprobe{EXE}"
        if ff.is_file() and fp.is_file():
            return FFmpegPaths(str(ff), str(fp))
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        return FFmpegPaths(ff, fp)
    return None


# ------------------------------------------------------------------ analisi
class ProbeError(Exception):
    pass


@dataclass
class MediaInfo:
    path: str
    duration_ms: int
    size_bytes: int
    container: str
    total_bitrate: int          # bit/s
    # video (assente nei file solo audio: has_video False, v_index -1)
    has_video: bool
    v_index: int                # indice assoluto della traccia video nel file
    vcodec: str
    width: int
    height: int
    fps: float
    v_bitrate: int              # bit/s (stimato se non dichiarato)
    pix_fmt: str
    is_hdr: bool
    rotation: int
    # audio
    has_audio: bool
    a_index: int | None
    acodec: str | None
    a_bitrate: int | None
    a_channels: int | None
    a_sample_rate: int | None = None

    @property
    def frame_ms(self) -> float:
        """Passo di avanzamento "un fotogramma" (per i file solo audio: 40 ms)."""
        return 1000.0 / self.fps if self.fps > 0 else 40.0

    @property
    def audio_only(self) -> bool:
        return not self.has_video

    @property
    def video_copy_ok(self) -> bool:
        return self.has_video and self.vcodec in MP4_VIDEO_COPY_OK

    @property
    def mp3_copy_ok(self) -> bool:
        """L'audio è già MP3: si può tagliare senza ricompressione."""
        return self.acodec == "mp3"

    @property
    def audio_copy_ok(self) -> bool:
        return (not self.has_audio) or (self.acodec in MP4_AUDIO_COPY_OK)

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation % 180 == 90:
            return self.height, self.width
        return self.width, self.height


def _parse_rate(value: str | None) -> float:
    if not value or value in ("0/0", "0"):
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(value)
    except ValueError:
        return 0.0


def _int(value, default: int | None = 0) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_probe(path: str, data: dict) -> MediaInfo:
    """Costruisce MediaInfo dall'output JSON di ffprobe.

    Accetta file con video (e audio facoltativo) oppure file solo audio
    (es. MP3, anche con copertina incorporata, che non conta come video).
    """
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next(
        (
            s for s in streams
            if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise ProbeError("no_video")
    main = video if video is not None else audio

    duration_s = _parse_rate(fmt.get("duration")) or _parse_rate(main.get("duration"))
    if duration_s <= 0:
        raise ProbeError("no_duration")
    duration_ms = int(round(duration_s * 1000))

    size = _int(fmt.get("size"), 0) or 0
    if not size:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
    total_bitrate = _int(fmt.get("bit_rate"), 0) or (int(size * 8 / duration_s) if size else 0)

    a_bitrate = _int(audio.get("bit_rate"), None) if audio else None
    if audio is not None and video is None and not a_bitrate and total_bitrate:
        a_bitrate = total_bitrate   # file solo audio: il bitrate complessivo è quello dell'audio

    audio_fields = dict(
        has_audio=audio is not None,
        a_index=_int(audio.get("index"), None) if audio else None,
        acodec=audio.get("codec_name") if audio else None,
        a_bitrate=a_bitrate,
        a_channels=_int(audio.get("channels"), None) if audio else None,
        a_sample_rate=_int(audio.get("sample_rate"), None) if audio else None,
    )
    common = dict(path=path, duration_ms=duration_ms, size_bytes=size,
                  container=fmt.get("format_name", ""), total_bitrate=total_bitrate)

    if video is None:
        return MediaInfo(
            **common, has_video=False, v_index=-1, vcodec="", width=0, height=0, fps=0.0,
            v_bitrate=0, pix_fmt="", is_hdr=False, rotation=0, **audio_fields,
        )

    v_bitrate = _int(video.get("bit_rate"), 0) or 0
    if not v_bitrate and total_bitrate:
        v_bitrate = max(total_bitrate - (a_bitrate or (128_000 if audio else 0)), 100_000)
    if not v_bitrate:
        v_bitrate = 4_000_000

    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
    if not (1 <= fps <= 240):
        fps = 25.0

    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = _int(side.get("rotation"), 0) or 0
    rotation = rotation % 360
    if not rotation:
        rotation = (_int(video.get("tags", {}).get("rotate"), 0) or 0) % 360

    return MediaInfo(
        **common,
        has_video=True,
        v_index=_int(video.get("index"), 0) or 0,
        vcodec=video.get("codec_name", "unknown"),
        width=_int(video.get("width"), 0) or 0,
        height=_int(video.get("height"), 0) or 0,
        fps=fps,
        v_bitrate=v_bitrate,
        pix_fmt=video.get("pix_fmt", ""),
        is_hdr=video.get("color_transfer") in HDR_TRANSFERS,
        rotation=rotation,
        **audio_fields,
    )


def probe(ffprobe: str, path: str, timeout: float = 30) -> MediaInfo:
    args = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = run(args, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("timeout") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ProbeError(message or "ffprobe_failed")
    try:
        data = json.loads(result.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ProbeError("invalid_output") from exc
    return parse_probe(path, data)


def read_keyframes(ffprobe: str, path: str, timeout: float = 300) -> list[float]:
    """Istanti (in secondi) dei fotogrammi chiave della traccia video.

    Legge solo l'indice dei pacchetti, senza decodificare: è veloce anche su
    file lunghi. Serve al taglio veloce, che può iniziare solo su un keyframe.
    """
    args = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path,
    ]
    try:
        result = run(args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    times: list[float] = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                times.append(float(parts[0]))
            except ValueError:
                continue
    return sorted(set(times))


# --------------------------------------------------------- capacità ffmpeg
@dataclass(frozen=True)
class EncoderInfo:
    name: str
    pix_fmt: str
    label: str
    device: str | None = None   # dispositivo VA-API

    @property
    def is_hardware(self) -> bool:
        return self.name != SW_ENCODER[0]


@dataclass(frozen=True)
class Capabilities:
    encoders: tuple[EncoderInfo, ...]   # in ordine di preferenza, CPU per ultima
    has_tonemap: bool
    has_mp3: bool

    def best(self, use_gpu: bool) -> EncoderInfo:
        if use_gpu:
            return self.encoders[0]
        return self.encoders[-1]

    @property
    def software(self) -> EncoderInfo:
        return self.encoders[-1]


def _test_encoder(ffmpeg: str, name: str, pix_fmt: str, device: str | None = None) -> bool:
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if device:
        args += ["-vaapi_device", device]
    args += ["-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=0.2", "-frames:v", "3"]
    if device:
        args += ["-vf", VAAPI_UPLOAD, "-c:v", name]
    else:
        args += ["-c:v", name, "-pix_fmt", pix_fmt]
    args += ["-f", "null", "-"]
    try:
        return run(args, timeout=20).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def detect_capabilities(ffmpeg: str) -> Capabilities:
    """Verifica gli encoder disponibili e funzionanti su questo PC (qualche secondo)."""
    try:
        encoders_txt = run([ffmpeg, "-hide_banner", "-encoders"], timeout=20).stdout.decode(
            "utf-8", "replace"
        )
        filters_txt = run([ffmpeg, "-hide_banner", "-filters"], timeout=20).stdout.decode(
            "utf-8", "replace"
        )
    except (subprocess.TimeoutExpired, OSError):
        encoders_txt = filters_txt = ""

    def listed(name: str, text: str) -> bool:
        return any(len(p) > 1 and p[1] == name for p in (line.split() for line in text.splitlines()))

    found: list[EncoderInfo] = []
    for name, pix_fmt, label in HW_ENCODERS:
        if not listed(name, encoders_txt):
            continue
        device = None
        if name == "h264_vaapi":
            device = vaapi_device()
            if device is None:
                continue
        if _test_encoder(ffmpeg, name, pix_fmt, device):
            found.append(EncoderInfo(name, pix_fmt, label, device))
    found.append(EncoderInfo(*SW_ENCODER))
    return Capabilities(
        encoders=tuple(found),
        has_tonemap=listed("zscale", filters_txt) and listed("tonemap", filters_txt),
        has_mp3=listed("libmp3lame", encoders_txt),
    )
