"""Integrazione con il desktop Linux (menu delle applicazioni e "Apri con").

L'AppImage è un file unico che il sistema non conosce finché non viene
"registrato". Con

    ./SimpleCutVideo-x86_64.AppImage --install-desktop

si crea una voce nel menu e l'app compare in "Apri con" per i file video
(file .desktop in ~/.local/share/applications). Con --uninstall-desktop la
voce viene rimossa. Non servono permessi di amministratore.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import APP_NAME, APP_VERSION

DESKTOP_ID = "simplecutvideo"
MIME_TYPES = (
    "video/mp4", "video/x-m4v", "video/quicktime", "video/x-matroska", "video/x-msvideo",
    "video/webm", "video/mp2t", "video/x-ms-wmv", "video/mpeg", "video/3gpp",
    "audio/mpeg", "audio/mp4", "audio/aac", "audio/x-wav", "audio/flac", "audio/ogg",
    "audio/x-vorbis+ogg", "audio/x-opus+ogg", "audio/x-ms-wma",
)


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _paths() -> tuple[Path, Path]:
    data = _data_home()
    desktop = data / "applications" / f"{DESKTOP_ID}.desktop"
    icon = data / "icons" / "hicolor" / "256x256" / "apps" / f"{DESKTOP_ID}.png"
    return desktop, icon


def _launcher() -> str:
    """Comando da usare nel file .desktop."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return appimage
    if getattr(sys, "frozen", False):
        return sys.executable
    return f"{sys.executable} -m simplecutvideo"


def _quote(path: str) -> str:
    # Specifica Desktop Entry: argomenti con spazi tra virgolette doppie
    if " -m " in path:
        exe, rest = path.split(" -m ", 1)
        return f'"{exe}" -m {rest}'
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def desktop_entry(exec_cmd: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "GenericName=Video and audio cutter",
        "GenericName[it]=Taglio video e audio",
        "Comment=Cut video and audio files and export the parts as MP4 or MP3",
        "Comment[it]=Taglia file video e audio ed esporta le parti in MP4 o MP3",
        "Comment[fr]=Découpe des fichiers vidéo et audio et exporte les parties en MP4 ou MP3",
        "Comment[es]=Corta archivos de vídeo y audio y exporta las partes en MP4 o MP3",
        "Comment[de]=Video- und Audiodateien schneiden und Teile als MP4 oder MP3 exportieren",
        f"Exec={exec_cmd} %f",
        f"Icon={DESKTOP_ID}",
        "Terminal=false",
        "Categories=AudioVideo;Audio;Video;AudioVideoEditing;",
        "MimeType=" + ";".join(MIME_TYPES) + ";",
        f"StartupWMClass={APP_NAME}",
        f"X-AppImage-Version={APP_VERSION}",
        "",
    ])


def _icon_source() -> Path | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in (base / "assets" / "icon.png",
                      Path(__file__).resolve().parent / "assets" / "icon.png"):
        if candidate.exists():
            return candidate
    return None


def _refresh(folder: Path) -> None:
    tool = shutil.which("update-desktop-database")
    if tool:
        subprocess.run([tool, str(folder)], capture_output=True, check=False)


def install() -> int:
    desktop, icon = _paths()
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(desktop_entry(_quote(_launcher())), encoding="utf-8")
    source = _icon_source()
    if source is not None:
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, icon)
    _refresh(desktop.parent)
    print(f"{APP_NAME}: voce di menu creata / menu entry created\n  {desktop}")
    if os.environ.get("APPIMAGE"):
        print("Se sposti l'AppImage, ripeti --install-desktop. / "
              "If you move the AppImage, run --install-desktop again.")
    return 0


def uninstall() -> int:
    desktop, icon = _paths()
    for path in (desktop, icon):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _refresh(desktop.parent)
    print(f"{APP_NAME}: voce di menu rimossa / menu entry removed")
    return 0


def audio_backend_fallback(env: dict[str, str] | None = None) -> str | None:
    """Sceglie PulseAudio se il servizio PipeWire non è attivo.

    Qt 6 usa PipeWire quando ne trova le librerie; se le librerie ci sono ma il
    servizio non è in esecuzione (es. Mint 21 con PulseAudio) l'app può chiudersi
    all'avvio. In quel caso si forza il backend PulseAudio, che funziona anche
    tramite pipewire-pulse. Restituisce il backend impostato, o None.
    """
    env = os.environ if env is None else env
    if sys.platform.startswith("win") or env.get("QT_AUDIO_BACKEND"):
        return None
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    remote = env.get("PIPEWIRE_REMOTE", "pipewire-0")
    socket = remote if os.path.isabs(remote) else os.path.join(runtime, remote)
    if os.path.exists(socket):
        return None
    env["QT_AUDIO_BACKEND"] = "pulseaudio"
    return "pulseaudio"


def handle_cli(argv: list[str]) -> int | None:
    """Gestisce le opzioni da riga di comando; None = avvia l'interfaccia."""
    if sys.platform.startswith("win"):
        return None
    if "--install-desktop" in argv:
        return install()
    if "--uninstall-desktop" in argv:
        return uninstall()
    return None
