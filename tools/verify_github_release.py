from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _gh_json(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object from gh {' '.join(args)}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tag_commit(repository: str, tag: str) -> str:
    reference = _gh_json("api", f"repos/{repository}/git/ref/tags/{tag}")
    target = reference["object"]
    for _ in range(4):
        target_type = target.get("type")
        target_sha = str(target.get("sha", "")).lower()
        if target_type == "commit":
            return target_sha
        if target_type != "tag" or re.fullmatch(r"[0-9a-f]{40}", target_sha) is None:
            raise ValueError(f"Unexpected Git tag target: {target}")
        tag_object = _gh_json("api", f"repos/{repository}/git/tags/{target_sha}")
        target = tag_object["object"]
    raise ValueError("Git tag indirection is unexpectedly deep")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a published GitHub Release against local asset digests.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-title", required=True)
    parser.add_argument("--asset-directory", type=Path, required=True)
    args = parser.parse_args()

    if re.fullmatch(r"\d+\.\d+", args.expected_version) is None:
        raise ValueError("Expected version must use exactly MAJOR.MINOR")
    if args.tag != f"v{args.expected_version}":
        raise ValueError("Tag and expected version differ")
    expected_commit = args.expected_commit.lower()
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        raise ValueError("Expected commit must be a full SHA-1")

    asset_directory = args.asset_directory.resolve()
    expected_names = {
        f"BiliDownloader.v{args.expected_version}.exe",
        f"BiliDownloader.v{args.expected_version}.sbom.json",
        "SHA256SUMS",
    }
    local_assets = {name: asset_directory / name for name in expected_names}
    missing = sorted(name for name, path in local_assets.items() if not path.is_file())
    if missing:
        raise FileNotFoundError(f"Missing local release assets: {missing}")

    release = _gh_json("api", f"repos/{args.repository}/releases/tags/{args.tag}")
    if release.get("tag_name") != args.tag:
        raise ValueError(f"Release tag mismatch: {release.get('tag_name')!r}")
    if release.get("name") != args.expected_title:
        raise ValueError(f"Release title mismatch: {release.get('name')!r}")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Release must be published and non-prerelease")
    if not release.get("published_at"):
        raise ValueError("Release has no publication timestamp")

    tag_commit = _tag_commit(args.repository, args.tag)
    if tag_commit != expected_commit:
        raise ValueError(f"Tag resolves to {tag_commit}, expected {expected_commit}")

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Release assets payload is not a list")
    remote_names = {str(asset.get("name")) for asset in assets}
    if remote_names != expected_names or len(assets) != len(expected_names):
        raise ValueError(f"Release assets mismatch: {sorted(remote_names)}")

    verified_assets: list[dict[str, object]] = []
    for asset in assets:
        name = str(asset["name"])
        local_path = local_assets[name]
        local_size = local_path.stat().st_size
        local_digest = f"sha256:{_sha256(local_path)}"
        if asset.get("state") != "uploaded":
            raise ValueError(f"Asset {name} is not uploaded: {asset.get('state')!r}")
        if asset.get("size") != local_size:
            raise ValueError(f"Asset {name} size mismatch: {asset.get('size')} != {local_size}")
        if asset.get("digest") != local_digest:
            raise ValueError(f"Asset {name} digest mismatch: {asset.get('digest')!r} != {local_digest}")
        verified_assets.append(
            {
                "id": asset.get("id"),
                "name": name,
                "size": local_size,
                "digest": local_digest,
            }
        )

    result = {
        "release_id": release.get("id"),
        "release_url": release.get("html_url"),
        "tag": args.tag,
        "tag_commit": tag_commit,
        "asset_count": len(verified_assets),
        "assets": sorted(verified_assets, key=lambda item: str(item["name"])),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
