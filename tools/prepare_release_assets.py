from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the version-bound SHA256SUMS release asset.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    if re.fullmatch(r"\d+\.\d+", args.version) is None:
        raise ValueError("Version must use exactly MAJOR.MINOR")

    directory = args.directory.resolve()
    names = [
        f"BiliDownloader.v{args.version}.win-x64.zip",
        f"BiliDownloader.v{args.version}.sbom.json",
    ]
    missing = [name for name in names if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing release inputs: {missing}")

    output = directory / "SHA256SUMS"
    lines = [f"{_sha256(directory / name)}  {name}" for name in names]
    output.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
