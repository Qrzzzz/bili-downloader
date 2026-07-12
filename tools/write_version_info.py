from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def _numeric_version(version: str) -> tuple[int, int, int, int]:
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", version)
    if not match:
        raise ValueError(f"Version must start with one to four numeric components: {version!r}")
    parts = [int(value or 0) for value in match.groups()]
    if any(value > 65535 for value in parts):
        raise ValueError("Windows version components must be between 0 and 65535")
    return tuple(parts)  # type: ignore[return-value]


def _build_timestamp() -> datetime:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        return datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _version_resource(
    version: str,
    numeric: tuple[int, int, int, int],
    commit: str,
    dirty: bool,
    built_at: str,
) -> str:
    short_commit = commit[:12]
    trace_version = f"{version}+g{short_commit}{'.dirty' if dirty else ''}"
    comments = f"Git commit {commit}; dirty={str(dirty).lower()}; built_at={built_at}"
    original_filename = f"BiliDownloader.v{version}.exe"
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric!r},
    prodvers={numeric!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Bili Downloader Lite contributors'),
          StringStruct('FileDescription', 'Bili Downloader Lite desktop application'),
          StringStruct('FileVersion', {version!r}),
          StringStruct('InternalName', 'BiliDownloader'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', {original_filename!r}),
          StringStruct('ProductName', 'Bili Downloader Lite'),
          StringStruct('ProductVersion', {trace_version!r}),
          StringStruct('Comments', {comments!r})
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate traceable PyInstaller build metadata.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--dirty", action="store_true")
    parser.add_argument("--version-file", type=Path, required=True)
    parser.add_argument("--metadata-file", type=Path, required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", args.commit):
        parser.error("--commit must be a full hexadecimal Git object id")

    numeric = _numeric_version(args.version)
    built_at = _build_timestamp().isoformat().replace("+00:00", "Z")
    build_id = f"{args.version}+g{args.commit[:12]}{'.dirty' if args.dirty else ''}"
    metadata = {
        "application": "Bili Downloader Lite",
        "version": args.version,
        "git_commit": args.commit.lower(),
        "dirty": args.dirty,
        "build_id": build_id,
        "built_at_utc": built_at,
    }

    _atomic_write(
        args.version_file,
        _version_resource(args.version, numeric, args.commit.lower(), args.dirty, built_at),
    )
    _atomic_write(args.metadata_file, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
