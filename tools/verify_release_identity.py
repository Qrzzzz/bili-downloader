from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


VERSION_RE = re.compile(r"\d+\.\d+")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _source_version(root: Path) -> str:
    source = (root / "app" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']$', source, re.MULTILINE)
    if match is None:
        raise ValueError("Unable to read source version")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind a release tag, source version, and commit.")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-tag", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    root = args.root.resolve()
    version = args.expected_version
    tag = args.expected_tag
    expected_commit = args.expected_commit.lower()
    if VERSION_RE.fullmatch(version) is None:
        raise ValueError(f"Release version must use MAJOR.MINOR: {version!r}")
    if tag != f"v{version}":
        raise ValueError(f"Release tag must be exactly v{version}: {tag!r}")
    if COMMIT_RE.fullmatch(expected_commit) is None:
        raise ValueError("Expected commit must be a full 40-character SHA-1")

    source_version = _source_version(root)
    head_commit = _git(root, "rev-parse", "HEAD").lower()
    tag_commit = _git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}").lower()
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    if source_version != version:
        raise ValueError(f"Source version {source_version!r} does not match {version!r}")
    if head_commit != expected_commit or tag_commit != expected_commit:
        raise ValueError(
            f"Commit mismatch: HEAD={head_commit}, tag={tag_commit}, expected={expected_commit}"
        )
    if dirty:
        raise ValueError("Release identity check requires a clean worktree")

    github_ref_type = os.environ.get("GITHUB_REF_TYPE")
    github_ref_name = os.environ.get("GITHUB_REF_NAME")
    if github_ref_type is not None and github_ref_type != "tag":
        raise ValueError(f"Release workflow must run from a tag, not {github_ref_type!r}")
    if github_ref_name is not None and github_ref_name != tag:
        raise ValueError(f"Workflow ref {github_ref_name!r} does not match {tag!r}")

    print(
        json.dumps(
            {
                "version": version,
                "tag": tag,
                "commit": expected_commit,
                "asset": f"BiliDownloader.v{version}.exe",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
