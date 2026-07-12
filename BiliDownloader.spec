# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


block_cipher = None
root = Path(SPECPATH)

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

icon_file = root / "assets" / "icon.ico"
if icon_file.exists():
    datas.append((str(icon_file), "assets"))

ffmpeg_file = root / "tools" / "ffmpeg.exe"
if ffmpeg_file.exists():
    datas.append((str(ffmpeg_file), "tools"))

browser_root = root / "ms-playwright"
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BiliDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BiliDownloader",
)
