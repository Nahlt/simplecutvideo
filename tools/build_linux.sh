#!/usr/bin/env bash
# Crea la versione portabile di SimpleCutVideo per Linux (AppImage + tar.gz).
#
#   1. ambiente Python dedicato (.venv-build) e dipendenze   (--no-venv: usa il Python attivo)
#   2. FFmpeg statico per Linux 64 bit (build GPL di BtbN)    (salta se ./ffmpeg esiste già)
#   3. test automatici                                        (--skip-tests per saltarli)
#   4. compilazione con PyInstaller (cartella onedir)
#   5. AppDir con app, FFmpeg, AppRun, .desktop, icona e licenze
#   6. AppImage con appimagetool e archivio tar.gz della stessa cartella
#
# Risultato in dist/:  SimpleCutVideo-<versione>-x86_64.AppImage
#                      SimpleCutVideo-<versione>-linux-x86_64.tar.gz
set -euo pipefail

NO_VENV=0
SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --no-venv) NO_VENV=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "Opzione sconosciuta: $arg" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
step() { printf '\n\033[36m=== %s ===\033[0m\n' "$1"; }
fail() { printf '\033[31mErrore: %s\033[0m\n' "$1" >&2; exit 1; }

# ------------------------------------------------------------------ Python
step "Ambiente Python"
if [ "$NO_VENV" = 1 ]; then
    PY="$(command -v python3 || command -v python)" || fail "Python non trovato"
else
    command -v python3 >/dev/null || fail "Python 3 non trovato: sudo apt install python3 python3-venv"
    if [ ! -x .venv-build/bin/python ]; then
        python3 -m venv .venv-build || fail "impossibile creare l'ambiente: sudo apt install python3-venv"
    fi
    PY="$ROOT/.venv-build/bin/python"
fi
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt -r requirements-build.txt
VERSION="$("$PY" -c 'import simplecutvideo; print(simplecutvideo.APP_VERSION)')"
echo "Versione: $VERSION"

# ------------------------------------------------------------------ FFmpeg
step "FFmpeg"
FFDIR="$ROOT/ffmpeg"
if [ -x "$FFDIR/ffmpeg" ] && [ -x "$FFDIR/ffprobe" ]; then
    echo "FFmpeg già presente in $FFDIR"
else
    API="https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
    AUTH=()
    [ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
    RELEASE="$(curl -fsSL "${AUTH[@]}" -H "User-Agent: SimpleCutVideo-build" "$API" || true)"
    URL="$(printf '%s' "$RELEASE" | "$PY" -c '
import json, re, sys
try:
    assets = json.load(sys.stdin)["assets"]
except Exception:
    assets = []
# ultima versione stabile: ffmpeg-nX.Y-latest-linux64-gpl-X.Y.tar.xz (statica)
pat = re.compile(r"^ffmpeg-n(\d+)\.(\d+)-latest-linux64-gpl-[\d.]+\.tar\.xz$")
stable = sorted((a for a in assets if pat.match(a["name"])),
                key=lambda a: tuple(int(x) for x in pat.match(a["name"]).groups()), reverse=True)
print(stable[0]["browser_download_url"] if stable else "")
')"
    if [ -z "$URL" ]; then
        # API non raggiungibile: build "master" più recente, con nome fisso
        URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
    fi
    [ -n "$URL" ] || fail "nessuna build FFmpeg per Linux trovata nella release di BtbN"
    echo "Scarico $(basename "$URL")…"
    TMP="$ROOT/build/ffmpeg-download"
    rm -rf "$TMP"; mkdir -p "$TMP"
    curl -fL --retry 3 -o "$TMP/ffmpeg.tar.xz" "$URL"
    tar -xJf "$TMP/ffmpeg.tar.xz" -C "$TMP"
    SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    mkdir -p "$FFDIR"
    cp "$SRC/bin/ffmpeg" "$SRC/bin/ffprobe" "$FFDIR/"
    cp "$SRC/LICENSE.txt" "$FFDIR/LICENSE.txt" 2>/dev/null || true
    printf 'FFmpeg: %s\nhttps://github.com/BtbN/FFmpeg-Builds\nCodice sorgente FFmpeg: https://ffmpeg.org/download.html\n' \
        "$(basename "$URL")" > "$FFDIR/SOURCE.txt"
    chmod +x "$FFDIR/ffmpeg" "$FFDIR/ffprobe"
    rm -rf "$TMP"
fi
"$FFDIR/ffmpeg" -hide_banner -version | head -n1
ENCODERS="$("$FFDIR/ffmpeg" -hide_banner -encoders)"
FILTERS="$("$FFDIR/ffmpeg" -hide_banner -filters)"
for need in libx264 libmp3lame; do
    grep -q " $need " <<<"$ENCODERS" || fail "FFmpeg senza l'encoder $need"
done
for opt in h264_vaapi h264_nvenc h264_qsv; do
    grep -q " $opt " <<<"$ENCODERS" || echo "Avviso: FFmpeg senza $opt (accelerazione hardware non disponibile)"
done
for need in zscale tonemap; do
    grep -q " $need " <<<"$FILTERS" || fail "FFmpeg senza il filtro $need"
done
export SIMPLECUTVIDEO_FFMPEG="$FFDIR"

# ------------------------------------------------------------------ test
if [ "$SKIP_TESTS" = 0 ]; then
    step "Test"
    QT_QPA_PLATFORM=offscreen "$PY" -m pytest -q tests
fi

# ------------------------------------------------------------------ build
step "Compilazione con PyInstaller"
"$PY" -m PyInstaller --noconfirm --clean SimpleCutVideo.spec

step "AppDir"
APPDIR="$ROOT/build/AppDir"
rm -rf "$APPDIR"; mkdir -p "$APPDIR"
cp -a "$ROOT/dist/SimpleCutVideo" "$APPDIR/app"
mkdir -p "$APPDIR/ffmpeg"
cp -a "$FFDIR/." "$APPDIR/ffmpeg/"
install -m 755 packaging/linux/AppRun "$APPDIR/AppRun"
sed "s/@VERSION@/$VERSION/" packaging/linux/simplecutvideo.desktop > "$APPDIR/simplecutvideo.desktop"
cp packaging/linux/simplecutvideo.png "$APPDIR/simplecutvideo.png"
ln -sf simplecutvideo.png "$APPDIR/.DirIcon"
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "$APPDIR/simplecutvideo.desktop" "$APPDIR/usr/share/applications/"
cp "$APPDIR/simplecutvideo.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
cp THIRD_PARTY_NOTICES.txt LEGGIMI-LINUX.txt "$APPDIR/"
[ -f LICENSE ] && cp LICENSE "$APPDIR/"
ln -sf AppRun "$APPDIR/SimpleCutVideo"   # avvio comodo dalla cartella estratta

for f in AppRun app/SimpleCutVideo ffmpeg/ffmpeg ffmpeg/ffprobe; do
    [ -e "$APPDIR/$f" ] || fail "file mancante nel pacchetto: $f"
done
echo "Dimensione cartella: $(du -sh "$APPDIR" | cut -f1)"

# ------------------------------------------------------------------ AppImage
step "AppImage"
TOOLS="$ROOT/build/tools"
mkdir -p "$TOOLS"
TOOL="$TOOLS/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    curl -fL --retry 3 -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi
OUT="$ROOT/dist/SimpleCutVideo-$VERSION-x86_64.AppImage"
rm -f "$OUT"
# --appimage-extract-and-run: funziona anche senza FUSE (container, CI)
ARCH=x86_64 VERSION="$VERSION" "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"

TARDIR="SimpleCutVideo-$VERSION-linux-x86_64"
TAR="$ROOT/dist/$TARDIR.tar.gz"
rm -f "$TAR"
tar -czf "$TAR" --transform "s|^\.|$TARDIR|" --exclude ./usr --exclude ./.DirIcon -C "$APPDIR" .

printf '\n\033[32mAppImage: %s (%s)\033[0m\n' "$OUT" "$(du -h "$OUT" | cut -f1)"
printf '\033[32mtar.gz:   %s (%s)\033[0m\n' "$TAR" "$(du -h "$TAR" | cut -f1)"
