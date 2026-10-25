from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import portable_sfx as sfx

ROOT = "BiliDownloader.v3.9.win-x64"


@pytest.mark.parametrize("path", [f"{ROOT}/中文 空格/a.dll", f"{ROOT}\\licenses\\7zip\\License.txt", ROOT])
def test_safe_member_paths(path: str) -> None:
    assert sfx.validate_member(path, ROOT) == path.replace("\\", "/")


@pytest.mark.parametrize("path", ["../bad", "/absolute", "C:/bad", "other/a", f"{ROOT}/../escape",
    f"{ROOT}/./file", f"{ROOT}//file", f"{ROOT}/a:stream", f"{ROOT}/CON.txt", f"{ROOT}/COM1",
    f"{ROOT}/file.", f"{ROOT}/file ", f"{ROOT}/line\nname", f"{ROOT}/folder/NUL"])
def test_extraction_rejects_unsafe_windows_paths(path: str) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        sfx.validate_member(path, ROOT)


def test_inventory_rejects_duplicates_links_and_resource_abuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    safe = f"Path = {ROOT}/one.dll\nSize = 12\nFolder = -\nAttributes = A\n"
    monkeypatch.setattr(sfx, "run_tool", lambda *a, **k: safe)
    assert list(sfx.inventory(tmp_path / "a.exe", "3.9", tmp_path)) == ["one.dll"]
    for listing in (safe + "\n" + safe, safe + "Symbolic Link = target\n",
                    safe.replace("12", str(3 * 1024**3)), safe.replace("one.dll", "../escape"), ""):
        monkeypatch.setattr(sfx, "run_tool", lambda *a, value=listing, **k: value)
        with pytest.raises(ValueError):
            sfx.inventory(tmp_path / "a.exe", "3.9", tmp_path)


def test_no_extraction_process_started_for_unsafe_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    def run(args, **_):
        calls.append(args)
        return f"Path = {ROOT}/../../escape\nSize = 1\nFolder = -\n"
    monkeypatch.setattr(sfx, "run_tool", run)
    with pytest.raises(ValueError):
        sfx.extract_for_audit(tmp_path / "bad.exe", tmp_path, "3.9", tmp_path)
    assert len(calls) == 1 and calls[0][1] == "l"


def test_tool_cache_tampering_stops_before_running_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"downloads": {"7zr.exe": {"url": "https://invalid", "sha256": "0" * 64}}}))
    monkeypatch.setattr(sfx, "LOCK", lock)
    downloads = tmp_path / "tools/downloads"
    downloads.mkdir(parents=True)
    (downloads / "7zr.exe").write_bytes(b"tampered")
    monkeypatch.setattr(sfx, "run_tool", lambda *a, **k: pytest.fail("must not execute unverified tools"))
    with pytest.raises(ValueError, match="Cached tool digest mismatch"):
        sfx.prepare_tools(tmp_path / "tools")


def test_failed_download_is_not_promoted_to_verified_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"downloads": {"7zr.exe": {"url": "https://invalid", "sha256": "0" * 64}}}))
    monkeypatch.setattr(sfx, "LOCK", lock)
    def fail(*_, **__):
        raise OSError("download unavailable")
    monkeypatch.setattr(sfx.urllib.request, "urlopen", fail)
    with pytest.raises(OSError):
        sfx.prepare_tools(tmp_path / "tools")
    assert not list((tmp_path / "tools/downloads").iterdir())


@pytest.mark.parametrize("failure", [None, "bytes", "missing", "extra", "version", "path", "self"])
def test_manifest_requires_identical_extracted_bytes(tmp_path: Path, failure: str | None) -> None:
    package = tmp_path / ROOT
    package.mkdir()
    (package / "中文.txt").write_bytes(b"same payload")
    sfx.write_payload_manifest(package, "3.9")
    manifest_path = package / "payload-files.json"
    if failure == "bytes":
        (package / "中文.txt").write_bytes(b"changed")
    elif failure == "missing":
        (package / "中文.txt").unlink()
    elif failure == "extra":
        (package / "extra.dll").write_bytes(b"extra")
    elif failure in {"version", "path", "self"}:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if failure == "version":
            manifest["version"] = "wrong"
        else:
            manifest["files"]["../escape" if failure == "path" else "payload-files.json"] = manifest["files"].pop("中文.txt")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if failure is None:
        assert sfx.verify_payload_manifest(package, "3.9") == 2
    else:
        with pytest.raises(ValueError):
            sfx.verify_payload_manifest(package, "3.9")


def test_sfx_tools_are_pinned_and_extract_only() -> None:
    lock = json.loads(sfx.LOCK.read_text(encoding="utf-8"))
    assert lock["version"] == "26.05"
    for name, item in lock["downloads"].items():
        assert item["url"].startswith(("https://github.com/ip7z/7zip/", "https://raw.githubusercontent.com/ip7z/7zip/"))
        assert len(bytes.fromhex(item["sha256"])) == 32
    assert "7z.sfx" in lock["extracted"]
    assert "7zS.sfx" not in lock["extracted"]  # Installer modules use temporary execution directories.


def test_checksums_include_only_exe_and_sbom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from tools import prepare_release_assets
    names = [ROOT + ".exe", "BiliDownloader.v3.9.sbom.json"]
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(sys, "argv", ["prepare_release_assets", "--version", "3.9", "--directory", str(tmp_path)])
    assert prepare_release_assets.main() == 0
    for name in names:
        assert f"{hashlib.sha256(name.encode()).hexdigest()}  {name}" in (tmp_path / "SHA256SUMS").read_text()
    assert ".zip" not in (tmp_path / "SHA256SUMS").read_text()
    (tmp_path / (ROOT + ".zip")).write_bytes(b"stale ZIP")
    with pytest.raises(FileExistsError, match="Stale ZIP"):
        prepare_release_assets.main()


def test_dialog_translation_preserves_native_structure() -> None:
    import struct
    def word(text):
        return text.encode("utf-16-le") + b"\0\0"
    template = bytearray(struct.pack("<LLHhhhh", 0x40, 0, 1, 0, 0, 256, 80))
    template += b"\0\0\0\0" + word("7-Zip self-extracting archive") + struct.pack("<H", 8) + word("MS Shell Dlg")
    template += b"\0" * (-len(template) % 4)
    template += struct.pack("<LLhhhhH", 0, 0, 0, 0, 30, 14, 2) + b"\xff\xff\x80\0" + word("Cancel") + b"\0\0"
    translated = sfx.localize_dialog(bytes(template), "3.9")
    assert translated[:18] == template[:18]
    assert word("取消") in translated
    assert word("Bili Downloader Lite 3.9 解压") in translated
    assert word("Microsoft YaHei UI") in translated
    assert sfx.localize_dialog(translated, "3.9") == translated


def test_sbom_accounts_for_outer_stub_and_its_license(tmp_path: Path) -> None:
    from tools.generate_windows_sbom import generate
    package = tmp_path / ROOT
    package.mkdir()
    (package / "build-info.json").write_text(json.dumps({"version": "3.9", "git_commit": "a" * 40, "dirty": True}))
    (package / "BiliDownloader.v3.9.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"includedFrameworks": []}}))
    (package / "distribution-info.json").write_text(json.dumps({"extractor_version": "26.05", "stub_sha256": "b" * 64,
                                                             "extractor_source": "https://github.com/ip7z/7zip/tree/26.05"}))
    python_bom = tmp_path / "python.json"
    python_bom.write_text(json.dumps({"components": [], "dependencies": []}))
    assets = tmp_path / "assets.json"
    assets.write_text(json.dumps({"libraries": {}, "targets": {"net10.0/win-x64": {}}}))
    bom = generate(python_bom, assets, package, "3.9")
    stub = next(c for c in bom["components"] if c["bom-ref"] == "extractor:7zip@26.05")
    assert stub["hashes"][0]["content"] == "b" * 64
    assert stub["licenses"][0]["license"]["id"] == "LGPL-2.1-or-later"
    assert "extractor:7zip@26.05" in next(d for d in bom["dependencies"] if d["ref"] == "bili-downloader@3.9")["dependsOn"]


def test_extractor_explicitly_uses_callers_permissions() -> None:
    import xml.etree.ElementTree as ET
    manifest = sfx.as_invoker_manifest(b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1"></assembly>')
    level = next(node for node in ET.fromstring(manifest).iter() if node.tag.endswith("requestedExecutionLevel"))
    assert level.attrib == {"level": "asInvoker", "uiAccess": "false"}
    with pytest.raises(ValueError, match="needs review"):
        sfx.as_invoker_manifest(manifest)


def test_directory_assembly_does_not_create_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import assemble_windows_package as assembly
    frontend, backend = tmp_path / "frontend", tmp_path / "backend"
    frontend.mkdir(); backend.mkdir()
    (frontend / "BiliDownloader.v3.9.exe").write_bytes(b"frontend fixture")
    (backend / "BiliDownloader.Backend.exe").write_bytes(b"backend fixture")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"version": "3.9"}))
    monkeypatch.setattr(assembly, "collect_licenses", lambda *_: None)
    output = tmp_path / ROOT
    assert assembly.assemble(frontend, backend, metadata, output, "3.9") == output
    assert (output / "BiliDownloader.v3.9.exe").is_file()
    assert not list(tmp_path.glob("*.zip"))
