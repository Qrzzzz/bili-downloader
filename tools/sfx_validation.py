"""Exercise the actual GUI SFX executable and verify its embedded payload manifest."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.portable_sfx import prepare_tools, inventory, sha256, verify_payload_manifest


def validate(executable: Path, version: str, output: Path) -> dict:
    import pefile

    executable = executable.resolve()
    expected = f"BiliDownloader.v{version}.win-x64"
    if executable.name != expected + ".exe":
        raise ValueError("Unexpected SFX executable name")
    tools, _ = prepare_tools()
    files = inventory(executable, version, tools)
    pe = pefile.PE(str(executable), fast_load=False)
    try:
        strings = {key.decode(): value.decode() for group in pe.FileInfo for entry in group
                   if hasattr(entry, "StringTable") for table in entry.StringTable for key, value in table.entries.items()}
        if (strings.get("ProductVersion") != version or strings.get("OriginalFilename") != executable.name
            or strings.get("ProductName") != "Bili Downloader Lite"):
            raise ValueError("Outer SFX PE version identity differs")
        manifests = [pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
                     for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries if kind.id == 24
                     for resource in kind.directory.entries for language in resource.directory.entries]
        levels = [node for data in manifests for node in ET.fromstring(data).iter()
                  if node.tag.endswith("requestedExecutionLevel")]
        if len(levels) != 1 or levels[0].get("level") != "asInvoker" or levels[0].get("uiAccess") != "false":
            raise ValueError("Outer SFX must not request elevation")
    finally:
        pe.close()
    destination = output.resolve() / ("中文 空格 " + uuid.uuid4().hex)
    destination.mkdir(parents=True)
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup is not None:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    run = subprocess.run([str(executable), "-y", "-o" + str(destination)], capture_output=True,
                         timeout=180, startupinfo=startup)
    if run.returncode != 0:
        raise RuntimeError(f"Actual SFX extraction failed ({run.returncode})")
    package = destination / expected
    actual = {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file()}
    if set(files) != actual:
        raise ValueError("Actual SFX extraction differs from archive inventory")
    count = verify_payload_manifest(package, version)
    metadata = json.loads((package / "build-info.json").read_text(encoding="utf-8"))
    if metadata["version"] != version:
        raise ValueError("Extracted package version differs")
    result = {"artifact": str(executable), "sha256": sha256(executable), "file_count": count,
              "extraction_exit_code": run.returncode, "payload_manifest_verified": True, "as_invoker": True,
              "frontend": str(package / f"BiliDownloader.v{version}.exe"), "directory": str(package),
              "git_commit": metadata["git_commit"], "dirty": metadata["dirty"]}
    output.mkdir(parents=True, exist_ok=True)
    (output / "sfx-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.executable, args.version, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
