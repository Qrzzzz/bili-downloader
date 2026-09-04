from __future__ import annotations

import argparse
import json
import shutil
import zipfile
import importlib.metadata
import sys
from pathlib import Path


def collect_licenses(output: Path, assets: Path) -> None:
    licenses = output / "licenses"
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError("CPython LICENSE.txt is required for distribution")
    (licenses / "CPython").mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, licenses / "CPython/LICENSE.txt")
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        for file in sorted(distribution.files or []):
            if not any(token in Path(file).name.lower() for token in ("license", "notice", "copying")):
                continue
            source = Path(distribution.locate_file(file))
            if not source.is_file():
                continue
            target = licenses / "python" / name / Path(file).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != source.read_bytes():
                import hashlib
                target = target.with_name(hashlib.sha256(source.read_bytes()).hexdigest()[:12] + "-" + target.name)
            shutil.copy2(source, target)
    resolved = json.loads(assets.read_text(encoding="utf-8"))
    roots = [Path(path) for path in resolved["packageFolders"]]
    inputs = {identity: library["path"] for identity, library in resolved["libraries"].items() if library["type"] == "package"}
    runtime = json.loads(next(output.glob("*.runtimeconfig.json")).read_text(encoding="utf-8"))
    for framework in runtime["runtimeOptions"]["includedFrameworks"]:
        name = f"{framework['name']}.Runtime.win-x64/{framework['version']}"
        inputs[name] = name.lower()
    for identity, relative in sorted(inputs.items()):
        directory = next((root / relative for root in roots if (root / relative).is_dir()), None)
        if directory is None:
            raise FileNotFoundError(f"Missing restored license input: {identity}")
        for source in directory.iterdir():
            if source.is_file() and (source.suffix == ".nuspec" or any(token in source.name.lower() for token in ("license", "notice", "copying"))):
                target = licenses / "nuget" / identity / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


def assemble(frontend: Path, backend: Path, metadata: Path, output: Path, version: str) -> Path:
    import re
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Version must use MAJOR.MINOR")
    if output.exists():
        raise FileExistsError("Assembly output must be fresh")
    if not (frontend / f"BiliDownloader.v{version}.exe").is_file() or not (backend / "BiliDownloader.Backend.exe").is_file():
        raise FileNotFoundError("Both WinUI and Python executables are required")
    info = json.loads(metadata.read_text(encoding="utf-8"))
    if info["version"] != version:
        raise ValueError("Build metadata version mismatch")
    collisions = {p.relative_to(frontend).as_posix().casefold() for p in frontend.rglob("*") if p.is_file()} & {
        p.relative_to(backend).as_posix().casefold() for p in backend.rglob("*") if p.is_file()}
    if collisions:
        raise ValueError(f"Frontend/backend file collisions: {sorted(collisions)}")
    shutil.copytree(frontend, output)
    shutil.copytree(backend, output, dirs_exist_ok=True)
    shutil.copy2(metadata, output / "build-info.json")
    root = Path(__file__).resolve().parents[1]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(root / name, output / name)
    collect_licenses(output, root / "BiliDownloader.WinUI/obj/project.assets.json")
    archive = output.with_suffix(output.suffix + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                zipped.write(path, Path(output.name) / path.relative_to(output))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble the self-contained WinUI + Python distribution.")
    parser.add_argument("--version", required=True)
    for name in ("frontend", "backend", "metadata", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    print(assemble(args.frontend.resolve(), args.backend.resolve(), args.metadata.resolve(), args.output.resolve(), args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
