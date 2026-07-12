# -*- mode: python ; coding: utf-8 -*-

import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


block_cipher = None
root = Path(SPECPATH)
version_source = (root / "app" / "__init__.py").read_text(encoding="utf-8")
version_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', version_source, re.MULTILINE)
if version_match is None:
    raise RuntimeError("Unable to read application version from app/__init__.py")
artifact_name = os.environ.get("BILI_ARTIFACT_BASENAME", f"BiliDownloader.v{version_match.group(1)}")
if not re.fullmatch(r"BiliDownloader\.v\d+\.\d+(?:\.\d+){0,2}", artifact_name):
    raise RuntimeError(f"Invalid versioned artifact name: {artifact_name!r}")
onefile = os.environ.get("BILI_BUILD_ONEFILE") == "1"
version_file = Path(
    os.environ.get("BILI_VERSION_FILE", root / "build" / "metadata" / "BiliDownloader.version")
)
build_metadata = Path(
    os.environ.get("BILI_BUILD_METADATA", root / "build" / "metadata" / "build-info.json")
)
if not version_file.is_file() or not build_metadata.is_file():
    raise RuntimeError("Build metadata is missing. Run build.ps1 instead of invoking the spec directly.")

hiddenimports = []
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("websockets")
hiddenimports += collect_submodules("playwright")
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
]

datas = []
datas += collect_data_files("certifi")
datas += collect_data_files("playwright")
datas += copy_metadata("yt-dlp")
datas += copy_metadata("certifi")
datas += copy_metadata("playwright")
datas.append((str(build_metadata), "."))

icon_file = root / "assets" / "icon.ico"
if icon_file.exists():
    datas.append((str(icon_file), "assets"))

ffmpeg_file = root / "tools" / "ffmpeg.exe"
if ffmpeg_file.exists():
    datas.append((str(ffmpeg_file), "tools"))

browser_root = Path(os.environ.get("BILI_BROWSER_ROOT", root / "ms-playwright"))
if browser_root.exists():
    for item in browser_root.rglob("*"):
        if item.is_file():
            relative_parent = item.relative_to(browser_root).parent
            datas.append((str(item), str(Path("ms-playwright") / relative_parent)))


a = Analysis(
    ["app/main.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_options = dict(
    name=artifact_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
    version=str(version_file),
)

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        **exe_options,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_options,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="BiliDownloader",
    )
