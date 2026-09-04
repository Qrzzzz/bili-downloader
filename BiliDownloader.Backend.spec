# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
version_file = Path(os.environ.get("BILI_VERSION_FILE", root / "build/metadata/BiliDownloader.version"))
build_metadata = Path(os.environ.get("BILI_BUILD_METADATA", root / "build/metadata/build-info.json"))
if not version_file.is_file() or not build_metadata.is_file():
    raise RuntimeError("Build metadata is missing. Run build.ps1.")
datas = collect_data_files("certifi")
for package in ("yt-dlp", "certifi", "segno"):
    datas += copy_metadata(package)
datas.append((str(build_metadata), "."))
a = Analysis(
    ["app/main.py"], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=collect_submodules("yt_dlp") + collect_submodules("websockets"),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6", "shiboken6", "PyQt6", "PyQt5", "tkinter"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="BiliDownloader.Backend",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, contents_directory="backend-runtime", icon=str(root / "assets/icon.ico"),
    version=str(version_file),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BiliDownloader.Backend")
