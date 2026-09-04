from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import PurePosixPath
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


BANNED_MEMBER_PATTERNS = (
    re.compile(r"(^|[\\/.])(PySide6|PyQt[56]|shiboken6)([\\/.]|$)", re.IGNORECASE),
    re.compile(r"(^|[\\/])Qt[56](Core|Gui|Widgets|WebEngine[^\\/]*)\.dll$", re.IGNORECASE),
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
    re.compile(r"(^|[\\/])(app|crash|frontend)\.log$", re.IGNORECASE),
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
    expected_root = f"BiliDownloader.v{expected_version}.win-x64"
    if executable.name not in {expected_root, expected_root + ".zip"}:
        raise ValueError(f"Artifact must be named {expected_root!r} or its ZIP")
    with tempfile.TemporaryDirectory(prefix="bili-package-audit-") as temporary:
        if executable.is_dir():
            packaged = {p.relative_to(executable).as_posix(): p for p in executable.rglob("*") if p.is_file()}
            read = lambda name: packaged[name].read_bytes()
            backend = executable / "BiliDownloader.Backend.exe"
            result = _audit_contents(packaged, read, backend, expected_version, expected_commit, require_clean)
            result.update(artifact=executable.name, size_bytes=sum(p.stat().st_size for p in packaged.values()))
            return result
        with zipfile.ZipFile(executable) as zipped:
            packaged = {}
            for item in zipped.infolist():
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or not path.parts or path.parts[0] != expected_root:
                    raise ValueError("Unsafe or unexpected ZIP member path")
                if item.is_dir():
                    continue
                relative = str(PurePosixPath(*path.parts[1:]))
                if relative.casefold() in {name.casefold() for name in packaged}:
                    raise ValueError("Duplicate ZIP member")
                packaged[relative] = item
            if len(packaged) > 20000 or sum(i.file_size for i in packaged.values()) > 2 * 1024**3:
                raise ValueError("Package exceeds audit resource limit")
            read = lambda name: zipped.read(packaged[name])
            backend = Path(temporary) / "BiliDownloader.Backend.exe"
            backend.write_bytes(read("BiliDownloader.Backend.exe"))
            result = _audit_contents(packaged, read, backend, expected_version, expected_commit, require_clean)
    result.update(artifact=executable.name, size_bytes=executable.stat().st_size, sha256=_sha256(executable))
    return result


def _audit_contents(packaged, read, backend: Path, expected_version: str, expected_commit: str | None, require_clean: bool) -> dict:
    required = {f"BiliDownloader.v{expected_version}.exe", f"BiliDownloader.v{expected_version}.dll",
                f"BiliDownloader.v{expected_version}.pri", "Assets/AppIcon.ico",
                "BiliDownloader.Backend.exe", "Microsoft.ui.xaml.dll", "build-info.json", "backend-runtime/build-info.json"}
    if not required <= set(packaged):
        raise ValueError(f"Missing native package files: {sorted(required - set(packaged))}")
    archive = CArchiveReader(str(backend))
    members = sorted(str(name) for name in archive.toc)
    audited_names: list[tuple[str, str]] = [(member, member) for member in [*members, *packaged]]
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

    build_info = json.loads(read("build-info.json").decode("utf-8"))
    if build_info != json.loads(read("backend-runtime/build-info.json").decode("utf-8")):
        raise ValueError("Frontend/backend build metadata differ")
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
        "package_file_count": len(packaged),
        "archive_member_count": len(members),
        "python_module_count": python_module_count,
        "prohibited_member_count": 0,
        "build_version": build_info.get("version"),
        "build_commit": build_info.get("git_commit"),
        "build_dirty": build_info.get("dirty"),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the WinUI + Python directory or ZIP, including embedded Python modules.")
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
