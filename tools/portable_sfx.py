"""Build and inspect a one-time, extract-only GUI SFX from official pinned 7-Zip.

No installer is executed: 7zr reads the upstream installer as an archive. Only
7z.sfx is redistributed (with app resources); compiler tools stay under build/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))
LOCK = ROOT / "tools/portable-sfx.lock.json"
DEFAULT_TOOLS = ROOT / "build/sfx-toolchain"


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run_tool(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, encoding="utf-8", timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"7-Zip failed ({result.returncode}): {(result.stdout + result.stderr)[-4000:]}")
    return result.stdout.replace("\r\n", "\n")


def prepare_tools(directory: Path = DEFAULT_TOOLS) -> tuple[Path, dict]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    directory = directory.resolve()
    downloads = directory / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    for name, item in lock["downloads"].items():
        target = downloads / name
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".download")
            try:
                with urllib.request.urlopen(item["url"], timeout=30) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if sha256(temporary) != item["sha256"]:
                    raise ValueError(f"Downloaded tool digest mismatch: {name}")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if sha256(target) != item["sha256"]:
            raise ValueError(f"Cached tool digest mismatch: {name}; remove the corrupted cache explicitly")
    official = directory / "official"
    if not all((official / name).is_file() for name in lock["extracted"]):
        run_tool([str(downloads / "7zr.exe"), "x", str(downloads / "7z2605-x64.exe"),
                  "-o" + str(official), "-y", "-sccUTF-8"])
    for name, digest in lock["extracted"].items():
        if sha256(official / name) != digest:
            raise ValueError(f"Extracted tool digest mismatch: {name}")
    return official, lock


def validate_member(name: str, root: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.parts[0] != root or
        any(part in {".", ".."} or not part or part.endswith((".", " ")) or
            re.search(r'[\x00-\x1f:<>"|?*]', part) or
            re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in name.split("/"))):
        raise ValueError(f"Unsafe SFX member path: {name!r}")
    return name


def write_payload_manifest(package: Path, version: str) -> None:
    files = {file.relative_to(package).as_posix(): {"size": file.stat().st_size, "sha256": sha256(file)}
             for file in sorted(package.rglob("*")) if file.is_file() and file != package / "payload-files.json"}
    (package / "payload-files.json").write_text(
        json.dumps({"schema_version": 1, "version": version, "files": files}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def verify_payload_manifest(package: Path, version: str) -> int:
    manifest = json.loads((package / "payload-files.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("version") != version or not isinstance(manifest.get("files"), dict):
        raise ValueError("Invalid payload manifest identity")
    expected = manifest["files"]
    folded = set()
    for name, item in expected.items():
        if validate_member(package.name + "/" + name, package.name) != package.name + "/" + name:
            raise ValueError("Invalid payload manifest path")
        if name == "payload-files.json" or name.casefold() in folded:
            raise ValueError("Duplicate or self-referencing payload manifest path")
        folded.add(name.casefold())
        if (not isinstance(item, dict) or not isinstance(item.get("size"), int) or item["size"] < 0
            or re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")) is None):
            raise ValueError("Invalid payload manifest entry")
    actual = {p.relative_to(package).as_posix(): p for p in package.rglob("*") if p.is_file() and p != package / "payload-files.json"}
    if set(actual) != set(expected):
        raise ValueError("Extracted file inventory differs from payload manifest")
    for name, file in actual.items():
        if file.is_symlink() or file.stat().st_size != expected[name]["size"] or sha256(file) != expected[name]["sha256"]:
            raise ValueError(f"Extracted file bytes differ from payload manifest: {name}")
    return len(actual) + 1


def inventory(artifact: Path, version: str, tools: Path) -> dict[str, dict]:
    root = f"BiliDownloader.v{version}.win-x64"
    listing = run_tool([str(tools / "7z.exe"), "l", "-slt", "-ba", "-t7z", "-sccUTF-8", str(artifact.resolve())])
    if len(listing) > 8 * 1024 * 1024:
        raise ValueError("SFX inventory exceeds resource limit")
    entries: dict[str, dict] = {}
    seen: set[str] = set()
    for block in listing.strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        name = validate_member(fields["Path"], root)
        if name.casefold() in seen:
            raise ValueError("Duplicate SFX member path")
        seen.add(name.casefold())
        if any("Link" in key for key in fields) or "L" in fields.get("Attributes", ""):
            raise ValueError("SFX links are not allowed")
        if fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D"):
            continue
        if name == root or len(PurePosixPath(name).parts) < 2:
            raise ValueError("SFX contains an unexpected root file")
        entries[str(PurePosixPath(*PurePosixPath(name).parts[1:]))] = fields
    if not entries or len(entries) > 20000 or sum(int(e["Size"]) for e in entries.values()) > 2 * 1024**3:
        raise ValueError("SFX payload missing or exceeds resource limit")
    return entries


def extract_for_audit(artifact: Path, destination: Path, version: str, tools: Path) -> Path:
    inventory(artifact, version, tools)  # Validate all paths before writing any files.
    run_tool([str(tools / "7z.exe"), "x", "-t7z", "-sccUTF-8", "-y", "-o" + str(destination.resolve()), str(artifact.resolve())])
    return destination / f"BiliDownloader.v{version}.win-x64"


def localize_dialog(data: bytes, version: str) -> bytes:
    """Translate the pinned standard DLGTEMPLATE without changing control IDs or layout."""
    style, _, count = struct.unpack_from("<LLH", data)
    offset = 18
    output = bytearray(data[:18])

    def field(translations: dict[str, str] | None = None) -> None:
        nonlocal offset
        start = offset
        first = struct.unpack_from("<H", data, offset)[0]
        if first == 0xffff:
            offset += 4
            output.extend(data[start:offset]); return
        while struct.unpack_from("<H", data, offset)[0] != 0:
            offset += 2
        text = data[start:offset].decode("utf-16-le")
        offset += 2
        text = (translations or {}).get(text, text)
        output.extend(text.encode("utf-16-le") + b"\0\0")

    field(); field()
    field({"7-Zip self-extracting archive": f"Bili Downloader Lite {version} 解压"})
    if style & 0x40:
        output.extend(data[offset:offset + 2]); offset += 2
        field({"MS Shell Dlg": "Microsoft YaHei UI"})
    for _ in range(count):
        offset = (offset + 3) & ~3
        output.extend(b"\0" * ((-len(output)) % 4))
        output.extend(data[offset:offset + 18]); offset += 18
        field()
        field({"Extract to:": "解压到：", "E&xtract to:": "解压到：", "Extract": "解压", "&Extract": "解压(&E)", "Cancel": "取消"})
        size = struct.unpack_from("<H", data, offset)[0]
        if size:
            raise ValueError("Unexpected SFX dialog creation data")
        output.extend(b"\0\0"); offset += 2
    if any(data[offset:]):
        raise ValueError("Unexpected trailing SFX dialog data")
    return bytes(output)


def as_invoker_manifest(data: bytes) -> bytes:
    if b"requestedExecutionLevel" in data or data.count(b"</assembly>") != 1:
        raise ValueError("Upstream SFX manifest needs review")
    return data.replace(b"</assembly>", b'<trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security>'
                        b'<requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/>'
                        b'</requestedPrivileges></security></trustInfo></assembly>')


def patch_resources(stub: Path, version: str, metadata: dict, output_name: str) -> None:
    import ctypes
    import pefile
    from PyInstaller.config import CONF
    from PyInstaller.utils.win32 import icon, versioninfo
    # The backend metadata writer already enforces the project's two-part version contract.
    from tools.write_version_info import _numeric_version, _version_resource

    # Remove all upstream icon/version languages, preserving the asInvoker manifest.
    # Otherwise Explorer can prefer the original 7-Zip identity over neutral resources.
    pe = pefile.PE(str(stub))
    resources = [(kind.id, resource.id, language.id) for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries
                 if kind.id in (3, 14, 16) for resource in kind.directory.entries
                 for language in resource.directory.entries]
    dialogs = [(resource.id, language.id, pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size))
               for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries if kind.id == 5
               for resource in kind.directory.entries if resource.id == 3400 for language in resource.directory.entries]
    manifests = [(resource.id, language.id, pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size))
                 for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries if kind.id == 24
                 for resource in kind.directory.entries for language in resource.directory.entries]
    pe.close()
    handle = versioninfo.win32api.BeginUpdateResource(str(stub), False)
    update = ctypes.WinDLL("kernel32", use_last_error=True).UpdateResourceW
    update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort, ctypes.c_void_p, ctypes.c_ulong]
    update.restype = ctypes.c_int
    try:
        for kind, resource, language in resources:
            if not update(handle, kind, resource, language, None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
        for resource, language, data in dialogs:
            versioninfo.win32api.UpdateResource(handle, 5, resource, localize_dialog(data, version), language)
        for resource, language, data in manifests:
            versioninfo.win32api.UpdateResource(handle, 24, resource, as_invoker_manifest(data), language)
    finally:
        versioninfo.win32api.EndUpdateResource(handle, False)

    info = _version_resource(version, _numeric_version(version), metadata["git_commit"],
                             metadata["dirty"], metadata["built_at_utc"])
    info = info.replace("BiliDownloader.Backend.exe", output_name).replace(
        "Bili Downloader Lite Python backend", "Bili Downloader Lite portable extractor").replace(
        "StringStruct('LegalCopyright', 'MIT License')",
        "StringStruct('LegalCopyright', '7-Zip LGPL-2.1-or-later; Bili Downloader Lite MIT')")
    previous = CONF.get("workpath")
    try:
        CONF["workpath"] = str(stub.parent)
        icon.CopyIcons(str(stub), [str(ROOT / "assets/icon.ico")])
    finally:
        if previous is None:
            CONF.pop("workpath", None)
        else:
            CONF["workpath"] = previous
    version_file = stub.with_suffix(".version.txt")
    version_file.write_text(info, encoding="utf-8")
    versioninfo.write_version_info_to_executable(str(stub), versioninfo.load_version_info_from_text_file(str(version_file)))


def build(package: Path, output: Path, version: str, tool_directory: Path = DEFAULT_TOOLS) -> dict:
    if re.fullmatch(r"\d+\.\d+", version) is None:
        raise ValueError("Version must use MAJOR.MINOR")
    package, output = package.resolve(), output.resolve()
    expected = f"BiliDownloader.v{version}.win-x64"
    if package.name != expected or output.name != expected + ".exe" or output.is_relative_to(package):
        raise ValueError("Unexpected portable package or output path")
    metadata = json.loads((package / "build-info.json").read_text(encoding="utf-8"))
    if metadata["version"] != version:
        raise ValueError("SFX version differs from package")
    tools, lock = prepare_tools(tool_directory)
    licenses = package / "licenses/7zip"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tools / "License.txt", licenses / "License.txt")
    shutil.copy2(tools.parent / "downloads/COPYING.LGPL-2.1.txt", licenses / "COPYING.LGPL-2.1.txt")
    (licenses / "SOURCE.txt").write_text(
        f"7-Zip {lock['version']} GUI SFX (LGPL-2.1-or-later)\nSource: {lock['source']}\n"
        "Changes: icon, VERSIONINFO, asInvoker manifest, and Chinese extraction dialog resources.\n"
        "Reproduce with tools/portable_sfx.py and tools/portable-sfx.lock.json in the Bili Downloader source.\n"
        "Project source: https://github.com/Qrzzzz/bili-downloader\n"
        "Use the Git revision in build-info.json for a clean release; dirty candidates require the working source.\n",
        encoding="utf-8")
    (package / "使用说明.txt").write_text(
        f"Bili Downloader Lite {version}\n\n"
        f"1. 运行本文件夹中的 BiliDownloader.v{version}.exe。请保留旁边的后端和运行库。\n"
        "2. 外层 win-x64.exe 仅负责解压；以后直接运行本文件夹中的程序，无需重复解压。\n"
        "3. 自行准备 FFmpeg，放入本文件夹的 tools/ffmpeg.exe，或配置绝对 PATH。\n"
        "4. 升级请解压到新目录，不要覆盖正在运行的程序。旧目录的 FFmpeg 需要自行复制。\n"
        "5. 取消或解压失败可能留下部分文件，请换用空目录重新解压。\n"
        "配置、登录凭据和任务记录仍保存在原有用户数据目录。\n",
        encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bili-sfx-build-", dir=tool_directory.resolve()) as work:
        work = Path(work)
        stub = work / "7z.sfx"
        shutil.copy2(tools / "7z.sfx", stub)
        patch_resources(stub, version, metadata, output.name)
        distribution = {"format": "7zip-gui-sfx", "app_version": version, "extract_only": True,
                        "extractor_version": lock["version"], "extractor_source": lock["source"],
                        "upstream_stub_sha256": lock["extracted"]["7z.sfx"], "stub_sha256": sha256(stub),
                        "stub_size": stub.stat().st_size, "downloads": lock["downloads"]}
        (package / "distribution-info.json").write_text(json.dumps(distribution, indent=2) + "\n", encoding="utf-8")
        for file in package.rglob("*"):
            if file.is_symlink() or (hasattr(file, "is_junction") and file.is_junction()):
                raise ValueError("Package links are not allowed")
            validate_member(package.name + "/" + file.relative_to(package).as_posix(), package.name)
        write_payload_manifest(package, version)
        payload = work / "payload.7z"
        run_tool([str(tools / "7z.exe"), "a", "-t7z", "-mx=5", "-m0=LZMA2", "-md=32m", "-ms=on",
                  "-mtc=off", "-mta=off", "-sccUTF-8", str(payload), package.name], cwd=package.parent)
        temporary = work / output.name
        with temporary.open("wb") as handle:
            for source in (stub, payload):
                with source.open("rb") as part:
                    shutil.copyfileobj(part, handle)
        inventory(temporary, version, tools)
        run_tool([str(tools / "7z.exe"), "t", "-t7z", "-sccUTF-8", str(temporary)])
        shutil.copy2(temporary, output)
    return {"exe": str(output), "size": output.stat().st_size, "sha256": sha256(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tools", type=Path, default=DEFAULT_TOOLS)
    args = parser.parse_args()
    print(json.dumps(build(args.package, args.output, args.version, args.tools), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
