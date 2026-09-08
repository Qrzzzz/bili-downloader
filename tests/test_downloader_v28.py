from __future__ import annotations

import contextlib
import copy
import importlib
import socket
from pathlib import Path
from typing import Any

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.downloader.http import HttpFD
from yt_dlp.extractor.bilibili import BilibiliBaseIE


class LocalInfoYDL(YoutubeDL):
    infos: dict[str, dict[str, Any]] = {}

    def extract_info(
        self,
        url: str,
        download: bool = True,
        *,
        process: bool = True,
        **kwargs: object,
    ) -> dict[str, Any]:
        _ = kwargs
        info = copy.deepcopy(self.infos[url])
        return self.process_ie_result(info, download=download) if process else info


@pytest.fixture
def module(isolated_app_environment: object, monkeypatch: pytest.MonkeyPatch) -> Any:
    downloader = importlib.import_module("app.downloader")
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("v2.8 downloader test attempted network access"),
    )
    monkeypatch.setattr(downloader, "YoutubeDL", LocalInfoYDL)
    monkeypatch.setattr(downloader, "require_ffmpeg", lambda: "X:/approved/ffmpeg.exe")
    monkeypatch.setattr(downloader, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(
        downloader,
        "cookiefile_lease",
        lambda *_args, **_kwargs: contextlib.nullcontext(None),
    )
    ffmpeg = importlib.import_module("yt_dlp.postprocessor.ffmpeg")
    monkeypatch.setattr(ffmpeg, "_get_exe_version_output", lambda *_args, **_kwargs: None)
    return downloader


def _part(module: Any, index: int = 1, *, url: str | None = None) -> Any:
    return module.VideoPart(
        index,
        "shared 100% title",
        url or f"https://www.bilibili.com/video/BV1Synthetic99?p={index}",
        id="BV1Synthetic99",
    )


def _muxed_info(*heights: int) -> dict[str, Any]:
    return {
        "id": "BV1Synthetic99",
        "title": "shared 100% title",
        "extractor": "Synthetic",
        "extractor_key": "Synthetic",
        "formats": [
            {
                "format_id": str(height),
                "url": f"https://media.invalid/{height}.mp4",
                "ext": "mp4",
                "height": height,
                "width": height * 16 // 9,
                "vcodec": "avc1",
                "acodec": "mp4a",
                "filesize": height * 100,
                "protocol": "https",
            }
            for height in heights
        ],
    }


def _download(
    module: Any,
    part: Any,
    output: Path,
    height: int,
    logs: list[str] | None = None,
) -> Any:
    return module.download_videos(
        [part],
        module.AppConfig(),
        str(output),
        f"bestvideo[height={height}]+bestaudio/best[height={height}]",
        lambda _status: None,
        (logs if logs is not None else []).append,
        credential_mode="anonymous",
    )


def test_real_ytdlp_output_identity_separates_quality_and_reuses_same_spec(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _part(module)
    LocalInfoYDL.infos = {part.url: _muxed_info(720, 1080)}
    downloads: list[tuple[Path, int]] = []

    def local_download(_downloader: HttpFD, filename: str, info: dict[str, Any]) -> bool:
        path = Path(filename)
        path.write_bytes(f"valid-mp4-{info['height']}".encode())
        downloads.append((path, info["height"]))
        return True

    def matches(path: Path, plan: Any, _ffmpeg: str, _controller: Any = None) -> bool:
        expected = f"valid-mp4-{plan.requested_height}".encode()
        return path.is_file() and path.read_bytes() == expected

    monkeypatch.setattr(HttpFD, "real_download", local_download)
    monkeypatch.setattr(module, "_matches_output_spec", matches)
    output = tmp_path / "outputs"

    low = _download(module, part, output, 720)
    high = _download(module, part, output, 1080)
    logs: list[str] = []
    repeated = _download(module, part, output, 1080, logs)

    low_path = Path(low.saved_files[0])
    high_path = Path(high.saved_files[0])
    assert low_path != high_path
    assert "[video-mp4-720p]" in low_path.stem
    assert "[video-mp4-1080p]" in high_path.stem
    assert repeated.saved_files == high.saved_files
    assert downloads == [(low_path, 720), (high_path, 1080)]
    assert any("已验证并复用相同规格" in line for line in logs)
    assert low_path.read_bytes() == b"valid-mp4-720"
    assert high_path.read_bytes() == b"valid-mp4-1080"


def test_locked_ytdlp_native_existing_file_semantics_match_current_output_identity(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _part(module)
    LocalInfoYDL.infos = {part.url: _muxed_info(1080)}
    planned = module.PlannedPart(
        part,
        "bestvideo[height=1080]+bestaudio/best[height=1080]",
        output_title=part.title,
        output_id=part.id,
    )
    plan = module.DownloadPlan((planned,), 1080, module.DownloadMode.AUDIO_VIDEO)
    output = tmp_path / "outputs"
    output.mkdir()
    stem = module._output_stem(planned, plan)
    existing = output / f"{stem}.mp4"
    original = b"pre-existing yt-dlp target"
    existing.write_bytes(original)
    logs: list[str] = []
    progress: list[dict[str, Any]] = []

    class CaptureLogger:
        def debug(self, message: str) -> None:
            logs.append(message)

        info = debug
        warning = debug
        error = debug

    monkeypatch.setattr(
        HttpFD,
        "real_download",
        lambda *_args, **_kwargs: pytest.fail("yt-dlp attempted transport for an existing target"),
    )
    options = {
        "quiet": True,
        "no_warnings": True,
        "format": planned.selector,
        "outtmpl": {"default": str(output / f"{stem.replace('%', '%%')}.%(ext)s")},
        "logger": CaptureLogger(),
        "progress_hooks": [progress.append],
    }

    with LocalInfoYDL(options) as ydl:
        selected = ydl.extract_info(part.url, download=True)

    assert selected["requested_downloads"][0]["filepath"] == str(existing)
    assert existing.read_bytes() == original
    assert any("already been downloaded" in line.lower() for line in logs)
    assert any(event.get("status") == "finished" for event in progress)


def test_verified_existing_mp3_is_reused_without_downloading_source_media(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _part(module)
    LocalInfoYDL.infos = {part.url: _muxed_info(1080)}
    selector = "bestvideo[height=1080]+bestaudio/best[height=1080]"
    plan = module.prepare_download_plan(
        [part],
        module.AppConfig(),
        selector,
        mode=module.DownloadMode.AUDIO_MP3,
    )
    output = tmp_path / "outputs"
    output.mkdir()
    existing = output / f"{module._output_stem(plan.parts[0], plan)}.mp3"
    original = b"valid-mp3"
    existing.write_bytes(original)
    logs: list[str] = []

    monkeypatch.setattr(
        module,
        "_matches_output_spec",
        lambda path, candidate, _ffmpeg, _controller=None: (
            candidate.mode is module.DownloadMode.AUDIO_MP3
            and path.is_file()
            and path.read_bytes() == original
        ),
    )
    monkeypatch.setattr(
        HttpFD,
        "real_download",
        lambda *_args, **_kwargs: pytest.fail("verified MP3 reuse attempted source transport"),
    )

    result = module.download_videos(
        [part],
        module.AppConfig(),
        str(output),
        selector,
        lambda _status: None,
        logs.append,
        credential_mode="anonymous",
        mode=module.DownloadMode.AUDIO_MP3,
    )

    assert result.saved_files == (str(existing.resolve()),)
    assert existing.read_bytes() == original
    assert any("已验证并复用相同规格" in line for line in logs)


def test_unrelated_same_identity_file_is_preserved_and_uses_collision_suffix(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _part(module)
    LocalInfoYDL.infos = {part.url: _muxed_info(1080)}
    output = tmp_path / "outputs"
    output.mkdir()
    primary = output / "P001-shared 100% title-BV1Synthetic99-[video-mp4-1080p].mp4"
    unrelated = b"unrelated or damaged bytes; not media"
    primary.write_bytes(unrelated)
    downloads: list[Path] = []

    def local_download(_downloader: HttpFD, filename: str, info: dict[str, Any]) -> bool:
        path = Path(filename)
        path.write_bytes(f"valid-mp4-{info['height']}".encode())
        downloads.append(path)
        return True

    monkeypatch.setattr(HttpFD, "real_download", local_download)
    monkeypatch.setattr(
        module,
        "_matches_output_spec",
        lambda path, plan, _ffmpeg, _controller=None: path.read_bytes() == f"valid-mp4-{plan.requested_height}".encode(),
    )
    logs: list[str] = []

    result = _download(module, part, output, 1080, logs)

    saved = Path(result.saved_files[0])
    assert primary.read_bytes() == unrelated
    assert saved == output / "P001-shared 100% title-BV1Synthetic99-[video-mp4-1080p]-2.mp4"
    assert downloads == [saved]
    assert any("保留原文件" in line for line in logs)


def test_failed_attempt_keeps_the_same_spec_target_for_retry(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _part(module)
    LocalInfoYDL.infos = {part.url: _muxed_info(1080)}
    output = tmp_path / "outputs"
    attempted: list[Path] = []

    def fail_with_part(_downloader: HttpFD, filename: str, _info: dict[str, Any]) -> bool:
        path = Path(filename)
        attempted.append(path)
        Path(str(path) + ".part").write_bytes(b"partial synthetic bytes")
        raise RuntimeError("synthetic interrupted transport")

    monkeypatch.setattr(HttpFD, "real_download", fail_with_part)
    monkeypatch.setattr(module, "_matches_output_spec", lambda path, *_args: path.read_bytes() == b"valid")
    first = _download(module, part, output, 1080)
    assert len(first.failed) == 1

    def complete(_downloader: HttpFD, filename: str, _info: dict[str, Any]) -> bool:
        path = Path(filename)
        attempted.append(path)
        path.write_bytes(b"valid")
        return True

    monkeypatch.setattr(HttpFD, "real_download", complete)
    retried = _download(module, part, output, 1080)

    assert len(retried.completed) == 1
    assert attempted[0] == attempted[1] == Path(retried.saved_files[0])
    assert Path(str(attempted[0]) + ".part").is_file()


def test_multi_part_and_mode_identities_are_stable_and_readable(module: Any) -> None:
    parts = [_part(module, 1), _part(module, 2)]
    planned = [module.PlannedPart(part, "bestvideo+bestaudio/best", output_title=part.title, output_id=part.id) for part in parts]
    video = module.DownloadPlan(tuple(planned), None, module.DownloadMode.AUDIO_VIDEO)
    audio = module.DownloadPlan(tuple(planned), None, module.DownloadMode.AUDIO_MP3)

    video_stems = [module._output_stem(item, video) for item in planned]
    audio_stems = [module._output_stem(item, audio) for item in planned]

    assert video_stems[0].startswith("P001-") and video_stems[1].startswith("P002-")
    assert all("[video-mp4-best]" in stem for stem in video_stems)
    assert all("[audio-mp3-192k]" in stem for stem in audio_stems)
    assert set(video_stems).isdisjoint(audio_stems)


def test_output_title_is_sanitized_truncated_and_percent_is_literal(module: Any) -> None:
    part = module.VideoPart(1, "bad<>:\"/\\|?* 100% " + "长" * 100, "https://unit.invalid/part")
    planned = module.PlannedPart(part, "bestvideo+bestaudio/best", output_title=part.title, output_id="CON")
    plan = module.DownloadPlan((planned,), None, module.DownloadMode.AUDIO_VIDEO)

    stem = module._output_stem(planned, plan)

    assert not any(character in stem for character in '<>:"/\\|?*')
    assert "100%" in stem
    assert "-_CON-" in stem
    assert len(module._truncate_filename_component(part.title, module.OUTPUT_TITLE_MAX_BYTES).encode()) <= 120


def _durl_info() -> dict[str, Any]:
    play_info = {
        "quality": 16,
        "timelength": 10000,
        "support_formats": [{"quality": 16, "new_description": "360P"}],
        "durl": [{"url": "https://media.invalid/muxed.mp4", "length": 10000, "size": 1024}],
    }
    return {
        "id": "durl",
        "title": "durl",
        "extractor": "BiliBili",
        "extractor_key": "BiliBili",
        "formats": BilibiliBaseIE().extract_formats(play_info),
    }


@pytest.mark.parametrize("mode", ["audio_video", "audio_mp3"])
def test_real_bilibili_durl_extractor_and_ytdlp_normalization_pass_preflight(
    module: Any,
    mode: str,
) -> None:
    part = _part(module, url="https://www.bilibili.com/video/BV1DurlFixture")
    raw = _durl_info()
    LocalInfoYDL.infos = {part.url: raw}
    selector = "bestvideo[height=360]+bestaudio/best[height=360]"

    selected = YoutubeDL({"quiet": True, "skip_download": True, "format": selector}).process_ie_result(
        copy.deepcopy(raw), download=False
    )
    plan = module.prepare_download_plan(
        [part],
        module.AppConfig(),
        selector,
        mode=module.DownloadMode(mode),
    )

    assert selected["format_id"] == "16" and selected["ext"] == "mp4"
    assert selected.get("vcodec") is None and selected.get("acodec") is None
    assert plan.parts[0].estimated_bytes == 1024
    assert plan.requested_height == (None if mode == "audio_mp3" else 360)


@pytest.mark.parametrize(
    ("formats", "audio_video", "audio_mp3"),
    [
        ([{"format_id": "mux", "url": "https://media.invalid/m.mp4", "ext": "mp4", "height": 360,
           "vcodec": "avc1", "acodec": "mp4a"}], True, True),
        ([{"format_id": "video", "url": "https://media.invalid/v.mp4", "ext": "mp4", "height": 360,
           "vcodec": "avc1", "acodec": "none"}], False, False),
        ([{"format_id": "audio", "url": "https://media.invalid/a.m4a", "ext": "m4a",
           "vcodec": "none", "acodec": "mp4a"}], False, True),
        ([{"format_id": "unknown", "url": "https://media.invalid/u.mp4", "ext": "mp4", "height": 360}],
         False, False),
        ([{"format_id": "video", "url": "https://media.invalid/v.mp4", "ext": "mp4", "height": 360,
           "vcodec": "avc1", "acodec": "none", "filesize": 800},
          {"format_id": "audio", "url": "https://media.invalid/a.m4a", "ext": "m4a",
           "vcodec": "none", "acodec": "mp4a", "filesize": 200}], True, True),
    ],
)
def test_codec_classification_preserves_explicit_and_unknown_boundaries(
    module: Any,
    formats: list[dict[str, Any]],
    audio_video: bool,
    audio_mp3: bool,
) -> None:
    part = _part(module)
    LocalInfoYDL.infos = {
        part.url: {
            "id": "classification",
            "title": "classification",
            "extractor": "Synthetic",
            "extractor_key": "Synthetic",
            "formats": formats,
        }
    }

    for mode, expected in ((module.DownloadMode.AUDIO_VIDEO, audio_video), (module.DownloadMode.AUDIO_MP3, audio_mp3)):
        selector = "bestvideo[height=360]+bestaudio/best[height=360]"
        if expected:
            module.prepare_download_plan([part], module.AppConfig(), selector, mode=mode)
        else:
            with pytest.raises(module.FormatPreflightError):
                module.prepare_download_plan([part], module.AppConfig(), selector, mode=mode)


def test_strict_height_and_multi_part_preflight_remain_fail_closed(module: Any) -> None:
    first = _part(module, 1)
    second = _part(module, 2)
    first_info = _durl_info()
    second_info = _durl_info()
    second_info["formats"][0]["height"] = 720
    LocalInfoYDL.infos = {first.url: first_info, second.url: second_info}

    with pytest.raises(module.FormatPreflightError) as caught:
        module.prepare_download_plan(
            [first, second],
            module.AppConfig(),
            "bestvideo[height=360]+bestaudio/best[height=360]",
        )

    assert [item.part.index for item in caught.value.missing] == [2]
    assert caught.value.missing[0].available_heights == (720,)
