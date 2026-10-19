# -*- mode: python ; coding: utf-8 -*-
# Configurazione PyInstaller: versione "cartella" (onedir), avvio rapido e
# meno segnalazioni degli antivirus rispetto all'exe singolo.
# FFmpeg viene copiato dopo la build da tools/build.ps1 nella sottocartella ffmpeg.

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
    icon="simplecutvideo/assets/icon.ico",
    version="tools/version_info.txt",
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SimpleCutVideo",
    upx=False,
)
