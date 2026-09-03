from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def utils() -> object:
    return importlib.import_module("app.utils")


def test_onefile_prefers_exe_adjacent_tools_before_meipass_and_path(
    utils: object,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe_dir = isolated_paths.root / "发布 目录"  # type: ignore[attr-defined]
    meipass = isolated_paths.root / "onefile-extraction"  # type: ignore[attr-defined]
    path_dir = isolated_paths.root / "system-path"  # type: ignore[attr-defined]
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(utils.sys, "executable", str(exe_dir / "BiliDownloader.v2.3.exe"))  # type: ignore[attr-defined]
    monkeypatch.setattr(utils.sys, "_MEIPASS", str(meipass), raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("PATH", str(path_dir))

    candidates = utils._ffmpeg_candidates(None)  # type: ignore[attr-defined]

    assert candidates == [
        (exe_dir / "tools" / "ffmpeg.exe").absolute(),
        (meipass / "tools" / "ffmpeg.exe").absolute(),
        (path_dir / "ffmpeg.exe").absolute(),
    ]


def test_onedir_deduplicates_resource_and_executable_layout(
    utils: object,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe_dir = isolated_paths.root / "onedir"  # type: ignore[attr-defined]
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(utils.sys, "executable", str(exe_dir / "BiliDownloader.v2.3.exe"))  # type: ignore[attr-defined]
    monkeypatch.setattr(utils.sys, "_MEIPASS", str(exe_dir), raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("PATH", "")

    assert utils._ffmpeg_candidates(None) == [(exe_dir / "tools" / "ffmpeg.exe").absolute()]  # type: ignore[attr-defined]


def test_source_run_uses_repository_tools_layout(
    utils: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(utils.sys, "frozen", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delattr(utils.sys, "_MEIPASS", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("PATH", "")
    expected = Path(utils.__file__).resolve().parents[1] / "tools" / "ffmpeg.exe"  # type: ignore[attr-defined]

    assert utils._ffmpeg_candidates(None) == [expected.absolute()]  # type: ignore[attr-defined]


def test_path_search_ignores_cwd_blank_and_relative_entries(
    utils: object,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = Path(utils.__file__).resolve().parents[1]  # type: ignore[attr-defined]
    safe = isolated_paths.root / "safe-bin"  # type: ignore[attr-defined]
    monkeypatch.delattr(utils.sys, "frozen", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delattr(utils.sys, "_MEIPASS", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("PATH", os_pathsep_join(["", ".", "relative-bin", str(safe)]))

    candidates = utils._ffmpeg_candidates(None)  # type: ignore[attr-defined]

    assert candidates == [
        (source_root / "tools" / "ffmpeg.exe").absolute(),
        (safe / "ffmpeg.exe").absolute(),
    ]


def os_pathsep_join(parts: list[str]) -> str:
    import os

    return os.pathsep.join(parts)


def test_probe_uses_absolute_unicode_path_and_classifies_only_broken_candidate(
    utils: object,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = isolated_paths.root / "带 空格" / "ffmpeg.exe"  # type: ignore[attr-defined]
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"synthetic executable placeholder")
    commands: list[list[str]] = []

    def broken(command: list[str], **_kwargs: object) -> object:
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")

    monkeypatch.setattr(utils.subprocess, "run", broken)  # type: ignore[attr-defined]

    result = utils.probe_ffmpeg([candidate])  # type: ignore[attr-defined]

    assert result.status is utils.FFmpegProbeStatus.BROKEN  # type: ignore[attr-defined]
    assert result.path == str(candidate.absolute())
    assert commands == [[str(candidate.absolute()), "-hide_banner", "-version"]]


def test_probe_falls_through_broken_adjacent_candidate_to_valid_absolute_path(
    utils: object,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = isolated_paths.root / "app" / "tools" / "ffmpeg.exe"  # type: ignore[attr-defined]
    valid = isolated_paths.root / "path" / "ffmpeg.exe"  # type: ignore[attr-defined]
    for candidate in (broken, valid):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"synthetic")

    def run(command: list[str], **_kwargs: object) -> object:
        if Path(command[0]) == broken.absolute():
            return SimpleNamespace(returncode=1, stdout="", stderr="broken")
        return SimpleNamespace(returncode=0, stdout="ffmpeg version synthetic", stderr="")

    monkeypatch.setattr(utils.subprocess, "run", run)  # type: ignore[attr-defined]

    result = utils.probe_ffmpeg([broken, valid])  # type: ignore[attr-defined]

    assert result.available
    assert result.path == str(valid.absolute())


def test_input_normalization_drops_short_link_tracking_and_rejects_unsafe_urls(utils: object) -> None:
    assert utils.normalize_bilibili_url("https://b23.tv/Synthetic?token=secret#fragment") == (  # type: ignore[attr-defined]
        "https://b23.tv/Synthetic"
    )
    assert utils.normalize_bilibili_url(  # type: ignore[attr-defined]
        "https://www.bilibili.com/video/BV1Synthetic99?utm_source=test&p=2#comments"
    ) == "https://www.bilibili.com/video/BV1Synthetic99?p=2"
    for value in (
        "http://www.bilibili.com/video/BV1Synthetic99",
        "https://localhost/video/BV1Synthetic99",
        "https://127.0.0.1/video/BV1Synthetic99",
        "https://user:secret@www.bilibili.com/video/BV1Synthetic99",
        "https://www.bilibili.com:444/video/BV1Synthetic99",
        "https://www.bilibili.com/",
    ):
        with pytest.raises(ValueError):
            utils.normalize_bilibili_url(value)  # type: ignore[attr-defined]
