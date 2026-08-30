from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


BANNED_MEMBER_PATTERNS = (
    re.compile(r"(^|[\\/])playwright(?:[\\/.]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])ms-playwright([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.local-browsers([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])chromium(?:-[^\\/]+)?([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])(chrome|chromium|msedge|node)(?:\.exe)?$", re.IGNORECASE),
    re.compile(r"(^|[\\/])chrome_elf\.dll$", re.IGNORECASE),
    re.compile(r"(^|[\\/])(snapshot_blob|v8_context_snapshot)\.bin$", re.IGNORECASE),
    re.compile(r"(^|[\\/])electron(?:[\\/.]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])resources[\\/]electron\.asar$", re.IGNORECASE),
    re.compile(r"(^|[\\/])ff(?:mpeg|probe)\.exe$", re.IGNORECASE),
    re.compile(r"(^|[\\/])icu(?:uc|in|dt\d*)\.dll$", re.IGNORECASE),
    re.compile(r"(^|[\\/])(storage_state(?:\.tmp)?\.json|cookies(?:\.tmp)?\.txt|session\.dat)$", re.IGNORECASE),
    re.compile(r"(^|[\\/])(browser-profile|playwright-profile|user_data|sessions?)([\\/]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])(app|crash)\.log$", re.IGNORECASE),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(
    executable: Path,
    expected_version: str,
    expected_commit: str | None = None,
    require_clean: bool = False,
) -> dict[str, object]:
    if not re.fullmatch(r"\d+\.\d+", expected_version):
        raise ValueError("Expected version must use exactly MAJOR.MINOR")
    expected_name = f"BiliDownloader.v{expected_version}.exe"
    if executable.name != expected_name:
        raise ValueError(f"Artifact must be named {expected_name!r}, not {executable.name!r}")
    if not executable.is_file():
        raise FileNotFoundError(executable)

    archive = CArchiveReader(str(executable))
    members = sorted(str(name) for name in archive.toc)
    audited_names: list[tuple[str, str]] = [(member, member) for member in members]
    python_module_count = 0
    for member in members:
        if not member.lower().endswith(".pyz"):
            continue
        try:
            embedded = archive.open_embedded_archive(member)
        except (KeyError, TypeError, ValueError):
            continue
        modules = sorted(str(name) for name in embedded.toc)
        python_module_count += len(modules)
        audited_names.extend((f"{member}!{module}", module) for module in modules)
    banned = sorted(
        display_name
        for display_name, inspected_name in audited_names
        if any(pattern.search(inspected_name) for pattern in BANNED_MEMBER_PATTERNS)
    )
    if banned:
        raise ValueError(f"Release archive contains prohibited members: {banned}")

    if "build-info.json" not in archive.toc:
        raise ValueError("Release archive does not contain build-info.json")
    build_info = json.loads(archive.extract("build-info.json").decode("utf-8"))
    if build_info.get("version") != expected_version:
        raise ValueError(f"Embedded build version mismatch: {build_info.get('version')!r}")
    if expected_commit is not None:
        expected_commit = expected_commit.lower()
        if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
            raise ValueError("Expected commit must be a full 40-character SHA-1")
        if build_info.get("git_commit") != expected_commit:
            raise ValueError(f"Embedded build commit mismatch: {build_info.get('git_commit')!r}")
    if require_clean and build_info.get("dirty") is not False:
        raise ValueError("Release archive metadata does not identify a clean build")

    result: dict[str, object] = {
        "artifact": executable.name,
        "size_bytes": executable.stat().st_size,
        "sha256": _sha256(executable),
        "archive_member_count": len(members),
        "python_module_count": python_module_count,
        "prohibited_member_count": 0,
        "build_version": build_info.get("version"),
        "build_commit": build_info.get("git_commit"),
        "build_dirty": build_info.get("dirty"),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure and audit a PyInstaller release artifact.")
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = audit(
        args.executable.resolve(),
        args.expected_version,
        expected_commit=args.expected_commit,
        require_clean=args.require_clean,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8", newline="\n")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
