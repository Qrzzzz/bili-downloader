from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
from pathlib import Path
from urllib.parse import quote


def generate(python_sbom: Path, assets_path: Path, package: Path, version: str) -> dict:
    """Combine the existing CycloneDX Python inventory with resolved NuGet and shipped files.

    Dependency inventories include build inputs, explicitly labelled as such. File
    components are the measured shipped bytes; no assertion of package reachability.
    """
    bom = json.loads(python_sbom.read_text(encoding="utf-8"))
    assets = json.loads(assets_path.read_text(encoding="utf-8"))
    metadata = json.loads((package / "build-info.json").read_text(encoding="utf-8"))
    if metadata["version"] != version:
        raise ValueError("SBOM version differs from package metadata")
    components = bom.setdefault("components", [])
    for item in components:
        item.setdefault("properties", []).append({"name": "bili:inventory-basis", "value": "hash-locked Python build environment"})
    references = [item["bom-ref"] for item in components]
    nuget = {}
    for identity, library in sorted(assets["libraries"].items()):
        if library["type"] != "package":
            continue
        name, resolved = identity.rsplit("/", 1)
        ref = f"pkg:nuget/{quote(name)}@{quote(resolved)}"
        nuget[identity] = ref
        component = {"type": "library", "name": name, "version": resolved, "bom-ref": ref, "purl": ref,
                     "properties": [{"name": "bili:inventory-basis", "value": "locked NuGet build dependency"}]}
        if library.get("sha512"):
            component["hashes"] = [{"alg": "SHA-512", "content": base64.b64decode(library["sha512"]).hex()}]
        components.append(component)
        references.append(ref)
    dependencies = bom.setdefault("dependencies", [])
    target = next((value for key, value in assets["targets"].items() if key.endswith("/win-x64")), None)
    if target is None:
        raise ValueError("Missing restored Windows x64 dependency graph")
    name_refs = {key.rsplit("/", 1)[0]: value for key, value in nuget.items()}
    for identity, item in target.items():
        if identity in nuget:
            dependencies.append({"ref": nuget[identity], "dependsOn": sorted(name_refs[n] for n in item.get("dependencies", {}) if n in name_refs)})
    runtime = json.loads((package / f"BiliDownloader.v{version}.runtimeconfig.json").read_text(encoding="utf-8"))
    for framework in runtime["runtimeOptions"]["includedFrameworks"]:
        ref = f"framework:{framework['name']}@{framework['version']}"
        components.append({"type": "framework", "name": framework["name"], "version": framework["version"], "bom-ref": ref,
                           "properties": [{"name": "bili:inventory-basis", "value": "self-contained runtimeconfig includedFrameworks"}]})
        references.append(ref)
    python_ref = f"runtime:CPython@{platform.python_version()}"
    components.append({"type": "framework", "name": "CPython", "version": platform.python_version(), "bom-ref": python_ref})
    references.append(python_ref)
    for file in sorted(package.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(package).as_posix()
        ref = "file:" + relative
        components.append({"type": "file", "name": relative, "bom-ref": ref,
                           "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(file.read_bytes()).hexdigest()}]})
        references.append(ref)
    root_ref = f"bili-downloader@{version}"
    bom.setdefault("metadata", {})["component"] = {"type": "application", "name": "Bili Downloader Lite", "version": version, "bom-ref": root_ref,
        "properties": [{"name": "bili:git-commit", "value": metadata["git_commit"]}, {"name": "bili:build-dirty", "value": str(metadata["dirty"]).lower()}]}
    # The previous Python-only root may no longer exist after replacing metadata.component.
    valid = set(references) | {root_ref}
    dependencies = [d for d in dependencies if d["ref"] in valid]
    for d in dependencies:
        d["dependsOn"] = [ref for ref in d.get("dependsOn", []) if ref in valid]
    dependencies.append({"ref": root_ref, "dependsOn": sorted(references)})
    bom["dependencies"] = dependencies
    bom["components"] = sorted(components, key=lambda item: item["bom-ref"])
    return bom


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a combined WinUI/.NET/Python/files CycloneDX SBOM.")
    for name in ("python-sbom", "assets", "package", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    bom = generate(args.python_sbom, args.assets, args.package, args.version)
    args.output.write_text(json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SBOM components: {len(bom['components'])}; {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
