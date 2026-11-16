# -*- mode: python ; coding: utf-8 -*-
# Configurazione PyInstaller: versione "cartella" (onedir), avvio rapido e
# meno segnalazioni degli antivirus rispetto all'exe singolo.
# FFmpeg viene copiato dopo la build da tools/build.ps1 (Windows) o
# tools/build_linux.sh (Linux) nella sottocartella ffmpeg.
import sys

IS_WINDOWS = sys.platform.startswith("win")

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[("simplecutvideo/assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SimpleCutVideo",
    console=False,
    icon="simplecutvideo/assets/icon.ico" if IS_WINDOWS else None,
    version="tools/version_info.txt" if IS_WINDOWS else None,
    strip=not IS_WINDOWS,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SimpleCutVideo",
    upx=False,
)
