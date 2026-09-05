from __future__ import annotations

import copy
import importlib
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.downloader.http import HttpFD
from yt_dlp.downloader.hls import HlsFD
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import Popen
from yt_dlp.version import __version__ as YTDLP_VERSION


INFO = {
    "id": "fixture", "title": "fixture", "extractor": "Fixture", "extractor_key": "Fixture",
    "webpage_url": "https://www.bilibili.com/video/BV1Synthetic99",
    "formats": [
        {"format_id": "video", "url": "https://media.invalid/video.mp4", "ext": "mp4",
         "height": 1080, "width": 1920, "vcodec": "avc1", "acodec": "none", "protocol": "https"},
        {"format_id": "audio", "url": "https://media.invalid/audio.m4a", "ext": "m4a",
         "vcodec": "none", "acodec": "mp4a", "protocol": "https"},
        {"format_id": "combined", "url": "https://media.invalid/combined.mp4", "ext": "mp4",
         "height": 720, "vcodec": "avc1", "acodec": "mp4a", "protocol": "https"},
    ],
}


@pytest.fixture
def boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    module = importlib.import_module("app.downloader")
    utils = importlib.import_module("app.utils")
    app_dir, resource_dir, path_dir, cwd = [tmp_path / p for p in ("应用 空格", "resources", "path", "cwd")]
    for directory in (app_dir / "tools", resource_dir / "tools", path_dir, cwd / "relative-bin"):
        directory.mkdir(parents=True)
    # These are inert bytes. Every subprocess entry is intercepted before execution.
    for directory in (cwd, cwd / "relative-bin"):
        for program in ("ffmpeg.exe", "ffprobe.exe"):
            (directory / program).write_bytes(b"inert untrusted-location fixture")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(utils, "executable_dir", lambda: app_dir)
    monkeypatch.setattr(utils, "resource_root", lambda: resource_dir)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative-bin", str(path_dir)]))
    monkeypatch.setattr(FFmpegPostProcessor, "_version_cache", {None: None})
    monkeypatch.setattr(FFmpegPostProcessor, "_features_cache", {})
    commands: list[list[str]] = []
    options: list[dict] = []
    broken: set[Path] = set()

    def deny_process(*_args: object, **_kwargs: object) -> None:
        pytest.fail("test attempted an unintercepted subprocess")

    monkeypatch.setattr(subprocess.Popen, "__init__", deny_process)

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        executable = Path(command[0])
        assert executable.is_absolute()
        assert executable.parent in {app_dir / "tools", resource_dir / "tools", path_dir}
        if executable in broken:
            return SimpleNamespace(returncode=1, stdout="", stderr="synthetic broken executable")
        if "-show_entries" in command:
            output = Path(command[-1])
            if output.suffix.lower() == ".mp3":
                payload = '{"format":{"format_name":"mp3"},"streams":[{"codec_type":"audio"}]}'
            else:
                payload = ('{"format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2"},'
                           '"streams":[{"codec_type":"video","height":1080},{"codec_type":"audio"}]}')
            return SimpleNamespace(returncode=0, stdout=payload, stderr="")
        if "-i" in command:
            output = Path(command[-1])
            if output.suffix.lower() == ".mp3":
                stderr = "Input #0, mp3, from fixture:\n  Stream #0:0: Audio: mp3"
            else:
                stderr = ("Input #0, mov,mp4,m4a,3gp,3g2,mj2, from fixture:\n"
                          "  Stream #0:0: Video: h264, yuv420p, 1920x1080, 30 fps\n"
                          "  Stream #0:1: Audio: aac")
            return SimpleNamespace(returncode=1, stdout="", stderr=stderr)
        return SimpleNamespace(returncode=0, stdout="ffmpeg version 8.0", stderr="")

    def popen_run(command: list[str], **_kwargs: object) -> tuple[str, str, int]:
        commands.append(command)
        executable = Path(command[0])
        assert executable.is_absolute(), "yt-dlp attempted implicit executable search"
        assert executable.parent in {app_dir / "tools", resource_dir / "tools", path_dir}
        if not executable.is_file() or executable in broken:
            raise FileNotFoundError("synthetic unavailable executable")
        if "-bsfs" in command:
            return f"{executable.stem} version 8.0\n  libavformat 62.0.0 / 62.0.0", "", 0
        if "-show_streams" in command:
            return "codec_name=aac\ncodec_type=audio\n", "", 0
        if "-y" not in command and "-i" in command:
            return "", "Stream #0:0: Audio: aac", 1
        output = Path(command[-1].removeprefix("file:"))
        output.write_bytes(b"synthetic postprocessor output, not media acceptance")
        return "", "", 0

    monkeypatch.setattr(utils.subprocess, "run", run)
    monkeypatch.setattr(Popen, "run", popen_run)

    class LocalYoutubeDL(YoutubeDL):
        def __init__(self, opts: dict) -> None:
            options.append(opts.copy())
            super().__init__(opts)

        def extract_info(
            self,
            url: str,
            download: bool = True,
            *,
            process: bool = True,
            **kwargs: object,
        ) -> dict:
            # Replace extraction/network only, keeping the library's default
            # format selection, downloader dispatch and postprocessors.
            _ = url, kwargs
            info = copy.deepcopy(INFO)
            return self.process_ie_result(info, download=download) if process else info

    def local_download(_fd: HttpFD, filename: str, info: dict) -> bool:
        Path(filename).write_bytes(b"synthetic transport bytes, not real downloaded media")
        return True

    monkeypatch.setattr(HttpFD, "real_download", local_download)
    monkeypatch.setattr(module, "YoutubeDL", LocalYoutubeDL)
    monkeypatch.setattr(module, "_fetch_bilibili_view", lambda _resolved: {
        "bvid": "BV1Synthetic99", "title": "fixture", "owner": {"name": "fixture"},
        "pages": [{"page": 1, "part": "fixture", "duration": 10}],
    })
    return SimpleNamespace(module=module, utils=utils, app_dir=app_dir, resource_dir=resource_dir,
                           path_dir=path_dir, cwd=cwd, commands=commands, options=options,
                           broken=broken, output=tmp_path / "outputs")


def install(directory: Path, *, probe: bool = True) -> Path:
    executable = directory / "ffmpeg.exe"
    executable.write_bytes(b"inert approved-location fixture")
    if probe:
        (directory / "ffprobe.exe").write_bytes(b"inert approved-location fixture")
    return executable


def parse_and_preflight(boundary: SimpleNamespace) -> None:
    module = boundary.module
    parsed = module.parse_video_info(INFO["webpage_url"], module.AppConfig(), credential_mode="anonymous")
    assert parsed.parts
    plan = module.prepare_download_plan(parsed.parts, module.AppConfig(), "bestvideo+bestaudio/best")
    assert plan.parts


def test_missing_ffmpeg_keeps_real_parse_and_preflight_without_any_probe(boundary: SimpleNamespace) -> None:
    assert YTDLP_VERSION == "2026.08.19"
    parse_and_preflight(boundary)
    assert not boundary.commands
    assert all(opts["ffmpeg_location"] == "" for opts in boundary.options)
    with pytest.raises(boundary.utils.AppError) as error:
        boundary.module.download_videos(
            [boundary.module.VideoPart(1, "fixture", INFO["webpage_url"])], boundary.module.AppConfig(),
            str(boundary.output), "bestvideo+bestaudio/best", lambda _event: None, credential_mode="anonymous")
    assert error.value.kind is boundary.utils.ErrorKind.FFMPEG_MISSING
    assert not boundary.commands


@pytest.mark.parametrize("location", ["adjacent", "resource", "absolute_path"])
@pytest.mark.parametrize("probe", ["available", "missing", "broken"])
def test_all_production_ydl_paths_use_approved_location_including_audio_fallback(
    boundary: SimpleNamespace, location: str, probe: str,
) -> None:
    directory = {"adjacent": boundary.app_dir / "tools", "resource": boundary.resource_dir / "tools",
                 "absolute_path": boundary.path_dir}[location]
    executable = install(directory, probe=probe != "missing")
    if probe == "broken":
        boundary.broken.add(directory / "ffprobe.exe")
    parse_and_preflight(boundary)
    module = boundary.module
    parts = [module.VideoPart(1, "fixture", INFO["webpage_url"])]
    for mode in (module.DownloadMode.AUDIO_VIDEO, module.DownloadMode.AUDIO_MP3):
        result = module.download_videos(parts, module.AppConfig(), str(boundary.output / mode.value),
                                        "bestvideo+bestaudio/best", lambda _event: None,
                                        credential_mode="anonymous", mode=mode)
        assert result.part_results[0].status is module.PartDownloadStatus.COMPLETED
    assert len(boundary.options) == 6  # parse, standalone preflight, two preflight/download pairs
    assert all(opts["ffmpeg_location"] == str(executable) for opts in boundary.options)
    assert all(Path(cmd[0]).parent == directory for cmd in boundary.commands)
    assert any("-bsfs" in cmd for cmd in boundary.commands)
    assert any("-acodec" in cmd and "libmp3lame" in cmd for cmd in boundary.commands)
    assert any("-c" in cmd and "copy" in cmd for cmd in boundary.commands)
    assert any(("-show_streams" in cmd) if probe == "available" else ("-i" in cmd and "-y" not in cmd)
               for cmd in boundary.commands)


def test_broken_ffmpeg_disables_parse_probe_and_keeps_download_error(boundary: SimpleNamespace) -> None:
    executable = install(boundary.app_dir / "tools")
    boundary.broken.add(executable)
    parse_and_preflight(boundary)
    assert all(cmd == [str(executable), "-hide_banner", "-version"] for cmd in boundary.commands)
    with pytest.raises(boundary.utils.AppError) as error:
        boundary.module.download_videos(
            [boundary.module.VideoPart(1, "fixture", INFO["webpage_url"])], boundary.module.AppConfig(),
            str(boundary.output), "bestvideo+bestaudio/best", lambda _event: None, credential_mode="anonymous")
    assert error.value.kind is boundary.utils.ErrorKind.FFMPEG_BROKEN


def test_broken_adjacent_falls_through_to_absolute_path(boundary: SimpleNamespace) -> None:
    boundary.broken.add(install(boundary.app_dir / "tools"))
    expected = install(boundary.path_dir)
    parse_and_preflight(boundary)
    assert all(opts["ffmpeg_location"] == str(expected) for opts in boundary.options)


def test_external_ffmpeg_downloader_final_spawn_is_absolute(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = importlib.import_module("yt_dlp.downloader.external")
    executable = install(boundary.path_dir)
    calls: list[list[str]] = []

    class SpawnIntercepted(Exception):
        pass

    def intercept(args: list[str], **_kwargs: object) -> None:
        calls.append(args)
        raise SpawnIntercepted

    monkeypatch.setattr(external, "Popen", intercept)
    module = boundary.module
    with module._youtube_dl(module.base_ydl_options(module.AppConfig())) as ydl:
        with pytest.raises(SpawnIntercepted):
            FFmpegFD(ydl, ydl.params)._call_downloader(
                str(boundary.output / "fixture.mp4"), {**INFO["formats"][0], "http_headers": {}})
    assert len(calls) == 1 and calls[0][0] == str(executable)


@pytest.mark.parametrize("present", [False, True])
def test_native_hls_internal_probe_uses_the_operation_context(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, present: bool,
) -> None:
    executable = install(boundary.path_dir) if present else None

    class BeforeFragments(Exception):
        pass

    def stop_before_fragments(*_args: object, **_kwargs: object) -> None:
        raise BeforeFragments

    monkeypatch.setattr(HlsFD, "_prepare_and_start_frag_download", stop_before_fragments)
    info = {**INFO["formats"][0], "protocol": "m3u8_native", "http_headers": {},
            "hls_media_playlist_data": "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nfixture.ts\n#EXT-X-ENDLIST\n"}
    module = boundary.module
    with module._youtube_dl(module.base_ydl_options(module.AppConfig())) as ydl:
        with pytest.raises(BeforeFragments):
            HlsFD(ydl, ydl.params).real_download(str(boundary.output / "fixture.mp4"), info)
    if present:
        assert any(cmd == [str(executable), "-bsfs"] for cmd in boundary.commands)
    else:
        assert not boundary.commands


@pytest.mark.parametrize("failure", ["none", "constructor", "operation"])
def test_ffmpeg_context_restores_after_success_or_failure(
    boundary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    context = FFmpegPostProcessor._ffmpeg_location
    token = context.set("outer-context-marker")
    module = boundary.module
    original = module.YoutubeDL

    def construct(options: dict) -> YoutubeDL:
        assert not FFmpegFD.available()
        if failure == "constructor":
            raise RuntimeError("synthetic constructor failure")
        return original(options)

    monkeypatch.setattr(module, "YoutubeDL", construct)
    try:
        try:
            with module._youtube_dl(module.base_ydl_options(module.AppConfig())):
                assert not FFmpegFD.available()
                if failure == "operation":
                    raise RuntimeError("synthetic operation failure")
        except RuntimeError:
            assert failure != "none"
        assert context.get() == "outer-context-marker"
        assert not boundary.commands
    finally:
        context.reset(token)


def test_concurrent_ffmpeg_contexts_do_not_share_locations(boundary: SimpleNamespace) -> None:
    executable = install(boundary.path_dir)
    barrier = threading.Barrier(2, timeout=5)

    def available(location: str) -> bool:
        options = {"quiet": True, "ffmpeg_location": location}
        with boundary.module._youtube_dl(options):
            barrier.wait()
            return FFmpegFD.available()

    with ThreadPoolExecutor(max_workers=2) as executor:
        absent = executor.submit(available, "")
        present = executor.submit(available, str(executable))
        assert not absent.result(timeout=5)
        assert present.result(timeout=5)


def test_relative_explicit_location_is_rejected_before_probe(boundary: SimpleNamespace) -> None:
    with pytest.raises(boundary.utils.AppError) as error:
        boundary.module.base_ydl_options(boundary.module.AppConfig(), ffmpeg_path="relative-bin/ffmpeg.exe")
    assert error.value.kind is boundary.utils.ErrorKind.FFMPEG_BROKEN
    assert not boundary.commands
