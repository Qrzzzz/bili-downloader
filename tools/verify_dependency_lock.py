from __future__ import annotations

import argparse
import re
from pathlib import Path


PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s*\\)?$")
HASH_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})(?:\s*\\)?$")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _input_pins(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    resolved = path.resolve()
    visited = set() if seen is None else seen
    if resolved in visited:
        return {}
    visited.add(resolved)

    pins: dict[str, str] = {}
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            pins.update(_input_pins(resolved.parent / line[3:].strip(), visited))
            continue
        match = PIN_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"Unpinned direct dependency in {path.name}: {line!r}")
        pins[_normalize(match.group(1))] = match.group(2)
    return pins


def _locked_pins(path: Path) -> dict[str, tuple[str, set[str]]]:
    pins: dict[str, tuple[str, set[str]]] = {}
    current: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pin_match = PIN_RE.fullmatch(line)
        if pin_match is not None:
            current = _normalize(pin_match.group(1))
            if current in pins:
                raise ValueError(f"Duplicate locked dependency in {path.name}: {current}")
            pins[current] = (pin_match.group(2), set())
            continue
        hash_match = HASH_RE.fullmatch(line)
        if hash_match is not None and current is not None:
            pins[current][1].add(hash_match.group(1))
            continue
        if line.startswith("--"):
            raise ValueError(f"Unsupported lock option in {path.name}: {line!r}")

    if not pins:
        raise ValueError(f"No dependencies found in {path.name}")
    missing_hashes = sorted(name for name, (_version, hashes) in pins.items() if not hashes)
    if missing_hashes:
        raise ValueError(f"Dependencies without SHA-256 hashes in {path.name}: {', '.join(missing_hashes)}")
    return pins


def verify_pair(input_path: Path, lock_path: Path) -> None:
    lock_text = lock_path.read_text(encoding="utf-8")
    required_header_parts = (
        "--python-version 3.13",
        "--python-platform x86_64-pc-windows-msvc",
        "--generate-hashes",
        "--no-build",
    )
    if not all(part in lock_text for part in required_header_parts):
        raise ValueError(f"{lock_path.name} does not document the approved lock target")

    direct = _input_pins(input_path)
    locked = _locked_pins(lock_path)
    mismatches = {
        name: (version, locked.get(name, (None, set()))[0])
        for name, version in direct.items()
        if locked.get(name, (None, set()))[0] != version
    }
    if mismatches:
        raise ValueError(f"Direct dependency versions differ from {lock_path.name}: {mismatches}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify pinned, hashed Python dependency locks.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()

    verify_pair(root / "requirements.in", root / "requirements.txt")
    verify_pair(root / "requirements-dev.in", root / "requirements-dev.txt")
    verify_pair(root / "requirements-sbom.in", root / "requirements-sbom.txt")
    print("dependency_lock=verified python=3.13 platform=x86_64-pc-windows-msvc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
