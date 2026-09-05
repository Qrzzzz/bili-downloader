from __future__ import annotations

import contextlib
import copy
import errno
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


@dataclass
class FakeResponse:
    url: str
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    chunks: tuple[bytes, ...] = ()
    stream_error: Exception | None = None
    closed: bool = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 1) -> object:
        _ = chunk_size
        for chunk in self.chunks:
            yield chunk
        if self.stream_error is not None:
            raise self.stream_error

    def close(self) -> None:
        self.closed = True


DownloadAction = Callable[["FakeYoutubeDL", str], dict[str, Any]]


@dataclass
class YdlScenario:
    formats_by_url: dict[str, list[dict[str, Any]]]
    download_actions: dict[str, DownloadAction] = field(default_factory=dict)
    calls: list[tuple[str, bool]] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)

    def factory(self, options: dict[str, Any]) -> "FakeYoutubeDL":
        self.options.append(options)
        return FakeYoutubeDL(options, self)


class FakeYoutubeDL:
    def __init__(self, options: dict[str, Any], scenario: YdlScenario) -> None:
        self.options = options
        self.scenario = scenario

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_info(
        self,
        url: str,
        download: bool = False,
        *,
        process: bool = True,
    ) -> dict[str, Any]:
        self.scenario.calls.append((url, download))
        if not download:
            if url not in self.scenario.formats_by_url:
                raise AssertionError(f"unexpected preflight URL: {url}")
            info = {
                "id": Path(url).name,
                "title": Path(url).name,
                "extractor_key": "Synthetic",
                "formats": copy.deepcopy(self.scenario.formats_by_url[url]),
            }
            return self.process_ie_result(info, download=False) if process else info
        if url not in self.scenario.download_actions:
            raise AssertionError(f"unexpected download URL: {url}")
        return self.scenario.download_actions[url](self, url)

    def process_ie_result(self, info: dict[str, Any], download: bool = False) -> dict[str, Any]:
        _ = download
        return info


@pytest.fixture
def downloader(isolated_app_environment: object, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import only after conftest has redirected every profile location."""

    module = importlib.import_module("app.downloader")

    def unexpected_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a downloader unit test attempted real network access")

    def unexpected_ytdlp(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a downloader unit test attempted an unmocked YoutubeDL call")

    monkeypatch.setattr(module.requests, "get", unexpected_network)
    monkeypatch.setattr(module, "YoutubeDL", unexpected_ytdlp)
    monkeypatch.setattr(module, "require_ffmpeg", lambda: "X:/synthetic/ffmpeg.exe")
    monkeypatch.setattr(module, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(module, "_matches_output_spec", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module,
        "cookiefile_lease",
        lambda *_args, **_kwargs: contextlib.nullcontext(None),
    )
    return module


def _parts(module: Any, count: int = 3) -> list[Any]:
    return [
        module.VideoPart(
            index=index,
            title=f"Synthetic part {index}",
            url=f"https://unit.invalid/p{index}",
            id=f"synthetic-p{index}",
        )
        for index in range(1, count + 1)
    ]


def _formats(height: int, *, sized: bool = False) -> list[dict[str, Any]]:
    video: dict[str, Any] = {
        "format_id": f"video-{height}",
        "height": height,
        "vcodec": "avc1",
        "acodec": "none",
    }
    audio: dict[str, Any] = {
        "format_id": "audio",
        "vcodec": "none",
        "acodec": "mp4a",
    }
    if sized:
        video["filesize"] = 8 * 1024 * 1024
        audio["filesize"] = 2 * 1024 * 1024
    return [video, audio]


def _install_scenario(module: Any, monkeypatch: pytest.MonkeyPatch, scenario: YdlScenario) -> None:
    monkeypatch.setattr(module, "YoutubeDL", scenario.factory)


def _run_hooks(options: dict[str, Any], key: str, payload: object) -> None:
    for hook in options.get(key, []):
        hook(copy.deepcopy(payload))


def _render_output_path(options: dict[str, Any], extension: str) -> Path:
    template = options["outtmpl"]["default"]
    placeholder = "\0PERCENT\0"
    rendered = template.replace("%%", placeholder).replace("%(ext)s", extension).replace(placeholder, "%")
    return Path(rendered)


def _success_action(
    filename: str,
    *,
    download_updates: tuple[tuple[int, int], ...] = ((100, 100),),
    postprocess: bool = True,
) -> DownloadAction:
    def action(ydl: FakeYoutubeDL, url: str) -> dict[str, Any]:
        for downloaded, total in download_updates:
            _run_hooks(
                ydl.options,
                "progress_hooks",
                {
                    "status": "downloading",
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                },
            )
        if postprocess:
            for status in ("started", "processing", "finished"):
                _run_hooks(ydl.options, "postprocessor_hooks", {"status": status})

        output = _render_output_path(ydl.options, Path(filename).suffix.lstrip(".") or "mp4")
        output.write_bytes(f"synthetic output for {url}".encode("utf-8"))
        _run_hooks(ydl.options, "post_hooks", str(output))
        return {
            "id": Path(url).name,
            "filepath": str(output),
            "requested_downloads": [{"filepath": str(output)}],
        }

    return action


def _config(module: Any, tmp_path: Path) -> Any:
    return module.AppConfig(download_dir=str(tmp_path / "downloads"))


def test_b23_redirect_preserves_target_page_and_drops_tracking_query(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        "https://b23.tv/synthetic",
        status_code=302,
        headers={
            "Location": "https://www.bilibili.com/video/BV1Synthetic99"
            "?spm_id_from=333.999&p=3&utm_source=unit-test#comments"
        },
    )
    captured: dict[str, Any] = {}

    def redirect(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr(downloader.requests, "get", redirect)

    resolved = downloader.resolve_bilibili_url("https://b23.tv/synthetic?share_source=test")

    assert resolved.canonical_url == "https://www.bilibili.com/video/BV1Synthetic99?p=3"
    assert resolved.requested_page == 3
    assert resolved.bvid == "BV1Synthetic99"
    assert captured["url"] == "https://b23.tv/synthetic?share_source=test"
    assert captured["allow_redirects"] is False
    assert captured["timeout"] == downloader.B23_TIMEOUT
    assert response.closed is True


def test_b23_external_redirect_is_rejected(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        "https://b23.tv/synthetic",
        status_code=302,
        headers={"Location": "https://attacker.invalid/video/BV1Synthetic99?p=3"},
    )
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError):
        downloader.resolve_bilibili_url("https://b23.tv/synthetic")

    assert response.closed is True


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_b23_accepts_only_explicit_redirect_statuses(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    response = FakeResponse(
        "https://b23.tv/synthetic",
        status_code=status,
        headers={"Location": "https://www.bilibili.com/video/BV1Synthetic99?p=2"},
    )
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: response)

    resolved = downloader.resolve_bilibili_url("https://b23.tv/synthetic")

    assert resolved.canonical_url == "https://www.bilibili.com/video/BV1Synthetic99?p=2"
    assert response.closed


def test_b23_relative_redirects_are_validated_hop_by_hop(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            FakeResponse("https://b23.tv/first", 302, {"Location": "/second"}),
            FakeResponse(
                "https://b23.tv/second",
                302,
                {"Location": "https://www.bilibili.com/video/av170001?p=4"},
            ),
        ]
    )
    requested: list[str] = []

    def redirect(url: str, **kwargs: object) -> FakeResponse:
        assert kwargs["allow_redirects"] is False
        requested.append(url)
        return next(responses)

    monkeypatch.setattr(downloader.requests, "get", redirect)

    resolved = downloader.resolve_bilibili_url("https://b23.tv/first")

    assert requested == ["https://b23.tv/first", "https://b23.tv/second"]
    assert resolved.canonical_url == "https://www.bilibili.com/video/av170001?p=4"


def test_b23_rejects_loops_and_redirect_limit(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = FakeResponse("https://b23.tv/loop", 302, {"Location": "/loop"})
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: loop)
    with pytest.raises(ValueError, match="循环"):
        downloader.resolve_bilibili_url("https://b23.tv/loop")

    calls = 0

    def endless(url: str, **_kwargs: object) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse(url, 302, {"Location": f"/hop-{calls}"})

    monkeypatch.setattr(downloader.requests, "get", endless)
    with pytest.raises(ValueError, match="次数过多"):
        downloader.resolve_bilibili_url("https://b23.tv/start")
    assert calls == downloader.B23_MAX_REDIRECTS


@pytest.mark.parametrize(
    "target",
    [
        "http://www.bilibili.com/video/BV1Synthetic99",
        "https://127.0.0.1/video/BV1Synthetic99",
        "https://[::1]/video/BV1Synthetic99",
        "https://localhost/video/BV1Synthetic99",
        "https://user:secret@www.bilibili.com/video/BV1Synthetic99",
        "https://www.bilibili.com:444/video/BV1Synthetic99",
        "https://attacker.invalid/video/BV1Synthetic99",
        "https://[malformed/video/BV1Synthetic99",
    ],
)
def test_b23_rejects_unsafe_redirect_targets(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    response = FakeResponse("https://b23.tv/synthetic", 302, {"Location": target})
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError):
        downloader.resolve_bilibili_url("https://b23.tv/synthetic")
    assert response.closed


@pytest.mark.parametrize(
    ("value", "canonical", "page"),
    [
        ("BV1Synthetic99", "https://www.bilibili.com/video/BV1Synthetic99", 1),
        ("av170001", "https://www.bilibili.com/video/av170001", 1),
        (
            "https://www.bilibili.com/video/BV1Synthetic99?p=6&utm_source=test#comments",
            "https://www.bilibili.com/video/BV1Synthetic99?p=6",
            6,
        ),
    ],
)
def test_input_and_canonical_video_url_share_the_strict_boundary(
    downloader: Any,
    value: str,
    canonical: str,
    page: int,
) -> None:
    normalized = downloader.canonicalize_video_url(
        value if value.startswith("https://") else f"https://www.bilibili.com/video/{value}"
    )
    assert normalized.canonical_url == canonical
    assert normalized.requested_page == page


def test_internal_api_video_reference_is_revalidated_before_use(downloader: Any) -> None:
    assert downloader._canonical_part_url({"bvid": "BV1Synthetic99"}, 3) == (
        "https://www.bilibili.com/video/BV1Synthetic99?p=3"
    )
    assert downloader._canonical_part_url({"aid": 170001}, 1) == "https://www.bilibili.com/video/av170001"
    assert downloader._canonical_part_url({"bvid": "BV1Synthetic99/../../private"}, 1) == ""
    assert downloader._canonical_part_url({"aid": "170001?token=secret"}, 1) == ""


def test_b23_errors_do_not_echo_sensitive_query(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    secret = "synthetic-secret-query"
    monkeypatch.setattr(
        downloader.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout(f"https://b23.tv/x?token={secret}")),
    )
    with pytest.raises(ValueError) as caught:
        downloader.resolve_bilibili_url(f"https://b23.tv/x?token={secret}")
    assert secret not in str(caught.value)


def test_thumbnail_streaming_accepts_small_image_and_closes_response(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        "https://i0.hdslb.com/bfs/archive/cover.jpg",
        headers={"Content-Type": "image/jpeg", "Content-Length": "6"},
        chunks=(b"abc", b"def"),
    )
    captured: dict[str, object] = {}

    def get(url: str, **kwargs: object) -> FakeResponse:
        captured.update(kwargs)
        return response

    monkeypatch.setattr(downloader.requests, "get", get)

    assert downloader.fetch_thumbnail(response.url) == b"abcdef"
    assert captured["stream"] is True
    assert captured["allow_redirects"] is False
    assert captured["timeout"] == downloader.THUMBNAIL_TIMEOUT
    assert response.closed


def test_thumbnail_upgrades_trusted_http_cdn_before_request(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "http://i1.hdslb.com/bfs/archive/cover.jpg"
    upgraded = "https://i1.hdslb.com/bfs/archive/cover.jpg"
    response = FakeResponse(
        upgraded,
        headers={"Content-Type": "image/jpeg", "Content-Length": "6"},
        chunks=(b"abc", b"def"),
    )
    requested: list[str] = []

    def get(url: str, **_kwargs: object) -> FakeResponse:
        requested.append(url)
        return response

    monkeypatch.setattr(downloader.requests, "get", get)

    assert downloader.fetch_thumbnail(source) == b"abcdef"
    assert requested == [upgraded]
    assert response.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/cover.jpg",
        "http://user@i1.hdslb.com/cover.jpg",
        "http://i1.hdslb.com:80/cover.jpg",
        "http://127.0.0.1/cover.jpg",
    ],
)
def test_thumbnail_does_not_upgrade_untrusted_or_ambiguous_http_url(
    downloader: Any,
    url: str,
) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        downloader.fetch_thumbnail(url)


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/html", "Content-Length": "4"},
        {"Content-Type": "image/jpeg", "Content-Length": "not-an-integer"},
        {"Content-Type": "image/jpeg", "Content-Length": str(6 * 1024 * 1024)},
    ],
)
def test_thumbnail_rejects_type_and_declared_size_before_reading(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    response = FakeResponse("https://i0.hdslb.com/cover", headers=headers, chunks=(b"data",))
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError):
        downloader.fetch_thumbnail(response.url)
    assert response.closed


def test_thumbnail_enforces_actual_byte_limit_without_trusting_length(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(downloader, "THUMBNAIL_MAX_BYTES", 5)
    for headers in ({"Content-Type": "image/png"}, {"Content-Type": "image/png", "Content-Length": "1"}):
        response = FakeResponse("https://i0.hdslb.com/cover", headers=headers, chunks=(b"123", b"456"))
        monkeypatch.setattr(downloader.requests, "get", lambda *_args, _response=response, **_kwargs: _response)
        with pytest.raises(ValueError, match="大小限制"):
            downloader.fetch_thumbnail(response.url)
        assert response.closed


def test_thumbnail_redirect_timeout_and_midstream_failure_are_non_secret(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    external = FakeResponse(
        "https://i0.hdslb.com/cover",
        302,
        {"Location": "https://127.0.0.1/private.jpg"},
    )
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: external)
    with pytest.raises(ValueError):
        downloader.fetch_thumbnail(external.url)
    assert external.closed

    secret = "synthetic-cover-secret"
    monkeypatch.setattr(
        downloader.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout(f"?token={secret}")),
    )
    with pytest.raises(ValueError) as timeout_error:
        downloader.fetch_thumbnail("https://i0.hdslb.com/cover")
    assert secret not in str(timeout_error.value)

    interrupted = FakeResponse(
        "https://i0.hdslb.com/cover",
        headers={"Content-Type": "image/webp"},
        chunks=(b"first",),
        stream_error=requests.ConnectionError(f"?token={secret}"),
    )
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: interrupted)
    with pytest.raises(ValueError) as stream_error:
        downloader.fetch_thumbnail(interrupted.url)
    assert secret not in str(stream_error.value)
    assert interrupted.closed


def test_thumbnail_follows_only_validated_cdn_redirects(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeResponse(
        "https://i0.hdslb.com/cover",
        302,
        {"Location": "https://archive.biliimg.com/cover.webp"},
    )
    final = FakeResponse(
        "https://archive.biliimg.com/cover.webp",
        headers={"Content-Type": "image/webp"},
        chunks=(b"small-image",),
    )
    responses = iter((first, final))
    requested: list[str] = []

    def get(url: str, **_kwargs: object) -> FakeResponse:
        requested.append(url)
        return next(responses)

    monkeypatch.setattr(downloader.requests, "get", get)

    assert downloader.fetch_thumbnail(first.url) == b"small-image"
    assert requested == [first.url, final.url]
    assert first.closed and final.closed


def test_format_labels_distinguish_8k_and_2880p(downloader: Any) -> None:
    choices = downloader.build_format_choices(
        [
            {"height": 4320, "vcodec": "av01", "acodec": "none"},
            {"height": 2880, "vcodec": "av01", "acodec": "none"},
            {"height": 2160, "vcodec": "av01", "acodec": "none"},
        ]
    )
    by_height = {choice.height: choice for choice in choices if choice.height is not None}

    assert "4320p" in by_height[4320].label
    assert "8K" in by_height[4320].label
    assert "4K" not in by_height[4320].label
    assert "2880p" in by_height[2880].label
    assert "8K" not in by_height[2880].label
    assert "4K" not in by_height[2880].label
    assert "2160p" in by_height[2160].label and "4K" in by_height[2160].label


def test_audio_mp3_mode_uses_best_audio_and_ffmpeg_conversion(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _parts(downloader, 1)[0]
    controller = downloader.DownloadController()
    scenario = YdlScenario({part.url: _formats(1080, sized=True)})
    events: list[dict[str, Any]] = []

    def download_audio(ydl: FakeYoutubeDL, url: str) -> dict[str, Any]:
        assert ydl.options["format"] == "bestaudio/best"
        assert ydl.options["ffmpeg_location"] == "X:/synthetic/ffmpeg.exe"
        assert "merge_output_format" not in ydl.options
        assert ydl.options["postprocessors"] == [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
        _run_hooks(
            ydl.options,
            "progress_hooks",
            {"status": "finished", "downloaded_bytes": 100, "total_bytes": 100},
        )
        _run_hooks(ydl.options, "postprocessor_hooks", {"status": "started"})
        assert controller.phase == "converting"
        output = _render_output_path(ydl.options, "mp3")
        output.write_bytes(b"synthetic mp3")
        _run_hooks(ydl.options, "postprocessor_hooks", {"status": "finished"})
        _run_hooks(ydl.options, "post_hooks", str(output))
        return {"id": Path(url).name, "filepath": str(output)}

    scenario.download_actions = {part.url: download_audio}
    _install_scenario(downloader, monkeypatch, scenario)

    result = downloader.download_videos(
        [part],
        _config(downloader, tmp_path),
        str(tmp_path / "output"),
        "height<=1080",
        events.append,
        controller=controller,
        mode=downloader.DownloadMode.AUDIO_MP3,
    )

    assert len(result.saved_files) == 1
    assert Path(result.saved_files[0]).suffix == ".mp3"
    assert "[audio-mp3-192k]" in Path(result.saved_files[0]).stem
    assert any(event["phase"] == "converting" for event in events)
    assert scenario.options[0]["skip_download"] is True
    assert controller.phase == "finished"


def test_default_download_mode_keeps_the_existing_mp4_merge_options(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _parts(downloader, 1)[0]
    scenario = YdlScenario({part.url: _formats(1080)})
    scenario.download_actions = {part.url: _success_action("video.mp4")}
    _install_scenario(downloader, monkeypatch, scenario)

    downloader.download_videos(
        [part],
        _config(downloader, tmp_path),
        str(tmp_path / "output"),
        "bestvideo[height=1080]+bestaudio/best[height=1080]",
        lambda _status: None,
    )

    options = scenario.options[-1]
    assert options["format"] == "bestvideo[height=1080]+bestaudio/best[height=1080]"
    assert options["merge_output_format"] == "mp4"
    assert "postprocessors" not in options


def test_audio_mp3_preflight_checks_every_part_before_zero_downloads(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader, 2)
    video_only = [{"format_id": "video", "height": 1080, "vcodec": "avc1", "acodec": "none"}]
    scenario = YdlScenario({parts[0].url: _formats(1080), parts[1].url: video_only})
    _install_scenario(downloader, monkeypatch, scenario)

    with pytest.raises(downloader.FormatPreflightError) as caught:
        downloader.download_videos(
            parts,
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "height<=1080",
            lambda _status: None,
            mode=downloader.DownloadMode.AUDIO_MP3,
        )

    assert caught.value.height is None
    assert [item.part.index for item in caught.value.missing] == [2]
    assert "可下载音频格式" in str(caught.value)
    assert scenario.calls == [(part.url, False) for part in parts]


def test_audio_mp3_plan_estimates_only_the_audio_stream(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    part = _parts(downloader, 1)[0]
    scenario = YdlScenario({part.url: _formats(1080, sized=True)})
    _install_scenario(downloader, monkeypatch, scenario)

    plan = downloader.prepare_download_plan(
        [part],
        _config(downloader, tmp_path),
        "height<=1080",
        mode=downloader.DownloadMode.AUDIO_MP3,
    )

    assert plan.mode is downloader.DownloadMode.AUDIO_MP3
    assert plan.requested_height is None
    assert plan.parts[0].selector == "bestaudio/best"
    assert plan.parts[0].estimated_bytes == 2 * 1024 * 1024


def test_exact_height_preflight_checks_every_part_before_zero_downloads(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader)
    scenario = YdlScenario(
        {
            parts[0].url: _formats(1080),
            parts[1].url: _formats(720),
            parts[2].url: _formats(1080),
        }
    )
    _install_scenario(downloader, monkeypatch, scenario)

    with pytest.raises(downloader.FormatPreflightError) as caught:
        downloader.download_videos(
            parts,
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "bestvideo[height=1080]+bestaudio/best[height=1080]",
            lambda _status: None,
        )

    assert caught.value.height == 1080
    assert [item.part.index for item in caught.value.missing] == [2]
    assert caught.value.missing[0].available_heights == (720,)
    assert scenario.calls == [(part.url, False) for part in parts]
    assert not any(download for _url, download in scenario.calls)


def test_overall_progress_never_regresses(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader, 2)
    scenario = YdlScenario({part.url: _formats(1080) for part in parts})
    scenario.download_actions = {
        parts[0].url: _success_action(
            "p1.mp4",
            download_updates=((100, 1000), (900, 1000), (400, 1000)),
        ),
        parts[1].url: _success_action(
            "p2.mp4",
            download_updates=((800, 1000), (200, 1000), (1000, 1000)),
        ),
    }
    _install_scenario(downloader, monkeypatch, scenario)
    events: list[dict[str, Any]] = []

    result = downloader.download_videos(
        parts,
        _config(downloader, tmp_path),
        str(tmp_path / "output"),
        "bestvideo[height=1080]+bestaudio/best[height=1080]",
        events.append,
    )

    percentages = [event["overall_percent"] for event in events]
    assert len(result.completed) == 2
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100.0
    assert all(0.0 <= percentage <= 100.0 for percentage in percentages)


def test_partial_failure_retains_completed_parts_and_real_saved_paths(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader)
    scenario = YdlScenario({part.url: _formats(1080) for part in parts})

    def fail_part_two(_ydl: FakeYoutubeDL, _url: str) -> dict[str, Any]:
        raise RuntimeError("synthetic decoder failure")

    scenario.download_actions = {
        parts[0].url: _success_action("p1-final.mp4"),
        parts[1].url: fail_part_two,
        parts[2].url: _success_action("p3-final.mp4"),
    }
    _install_scenario(downloader, monkeypatch, scenario)

    result = downloader.download_videos(
        parts,
        _config(downloader, tmp_path),
        str(tmp_path / "output"),
        "bestvideo[height=1080]+bestaudio/best[height=1080]",
        lambda _status: None,
    )

    assert [item.status for item in result.part_results] == [
        downloader.PartDownloadStatus.COMPLETED,
        downloader.PartDownloadStatus.FAILED,
        downloader.PartDownloadStatus.COMPLETED,
    ]
    assert [item.part.index for item in result.completed] == [1, 3]
    assert [item.part.index for item in result.failed] == [2]
    assert len(result.saved_files) == 2
    assert all(Path(path).is_file() for path in result.saved_files)
    assert all("[video-mp4-1080p]" in Path(path).stem for path in result.saved_files)


def test_retry_result_merge_preserves_order_and_replaces_only_retried_parts(
    downloader: Any,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader)
    first_file = tmp_path / "p1.mp4"
    retried_file = tmp_path / "p2.mp4"
    first_file.write_bytes(b"first")
    retried_file.write_bytes(b"retried")
    failure = downloader.ErrorClassification(downloader.ErrorKind.TIMEOUT, "超时", True)
    original = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(parts[0], downloader.PartDownloadStatus.COMPLETED, (str(first_file),)),
            downloader.PartDownloadResult(parts[1], downloader.PartDownloadStatus.FAILED, error=failure),
            downloader.PartDownloadResult(parts[2], downloader.PartDownloadStatus.CANCELLED),
        )
    )
    retry = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(parts[1], downloader.PartDownloadStatus.COMPLETED, (str(retried_file),)),
        )
    )

    merged = original.merged_with_retry(retry)

    assert [item.part.url for item in merged.part_results] == [part.url for part in parts]
    assert [item.status for item in merged.part_results] == [
        downloader.PartDownloadStatus.COMPLETED,
        downloader.PartDownloadStatus.COMPLETED,
        downloader.PartDownloadStatus.CANCELLED,
    ]
    assert merged.saved_files == (str(first_file), str(retried_file))


def test_cancel_before_download_marks_every_part_cancelled(
    downloader: Any,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader)
    controller = downloader.DownloadController()
    controller.cancel()

    with pytest.raises(downloader.DownloadBatchCancelled) as caught:
        downloader.download_videos(
            parts,
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "bestvideo+bestaudio/best",
            lambda _status: None,
            controller=controller,
        )

    assert [item.status for item in caught.value.result.part_results] == [
        downloader.PartDownloadStatus.CANCELLED,
        downloader.PartDownloadStatus.CANCELLED,
        downloader.PartDownloadStatus.CANCELLED,
    ]
    assert caught.value.result.saved_files == ()


def test_cancel_during_download_marks_current_and_remaining_parts_cancelled(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader)
    controller = downloader.DownloadController()
    scenario = YdlScenario({part.url: _formats(1080) for part in parts})

    def cancel_in_download(ydl: FakeYoutubeDL, _url: str) -> dict[str, Any]:
        _run_hooks(
            ydl.options,
            "progress_hooks",
            {"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100},
        )
        controller.cancel()
        _run_hooks(
            ydl.options,
            "progress_hooks",
            {"status": "downloading", "downloaded_bytes": 20, "total_bytes": 100},
        )
        raise AssertionError("the cancellation hook should have interrupted YoutubeDL")

    scenario.download_actions = {parts[0].url: cancel_in_download}
    _install_scenario(downloader, monkeypatch, scenario)

    with pytest.raises(downloader.DownloadBatchCancelled) as caught:
        downloader.download_videos(
            parts,
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "bestvideo[height=1080]+bestaudio/best[height=1080]",
            lambda _status: None,
            controller=controller,
        )

    assert [item.part.index for item in caught.value.result.part_results] == [1, 2, 3]
    assert all(
        item.status is downloader.PartDownloadStatus.CANCELLED
        for item in caught.value.result.part_results
    )
    assert [call for call in scenario.calls if call[1]] == [(parts[0].url, True)]
    assert controller.phase == "cancelled"


def test_cancel_during_merge_finishes_current_part_and_preserves_completed_files(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader)
    controller = downloader.DownloadController()
    scenario = YdlScenario({part.url: _formats(1080) for part in parts})

    def cancel_after_merge_started(ydl: FakeYoutubeDL, url: str) -> dict[str, Any]:
        _run_hooks(
            ydl.options,
            "progress_hooks",
            {"status": "finished", "downloaded_bytes": 100, "total_bytes": 100},
        )
        _run_hooks(ydl.options, "postprocessor_hooks", {"status": "started"})
        assert controller.phase == "merging"
        controller.cancel()
        _run_hooks(ydl.options, "postprocessor_hooks", {"status": "processing"})
        _run_hooks(ydl.options, "postprocessor_hooks", {"status": "finished"})
        output = _render_output_path(ydl.options, "mp4")
        output.write_bytes(f"synthetic merged output for {url}".encode("utf-8"))
        _run_hooks(ydl.options, "post_hooks", str(output))
        return {"id": "p2", "filepath": str(output)}

    scenario.download_actions = {
        parts[0].url: _success_action("p1-complete.mp4"),
        parts[1].url: cancel_after_merge_started,
    }
    _install_scenario(downloader, monkeypatch, scenario)

    with pytest.raises(downloader.DownloadBatchCancelled) as caught:
        downloader.download_videos(
            parts,
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "bestvideo[height=1080]+bestaudio/best[height=1080]",
            lambda _status: None,
            controller=controller,
        )

    result = caught.value.result
    assert [(item.part.index, item.status) for item in result.part_results] == [
        (1, downloader.PartDownloadStatus.COMPLETED),
        (2, downloader.PartDownloadStatus.COMPLETED),
        (3, downloader.PartDownloadStatus.CANCELLED),
    ]
    assert len({Path(path).name for path in result.saved_files}) == 2
    assert all("[video-mp4-1080p]" in Path(path).stem for path in result.saved_files)
    assert all(Path(path).is_file() for path in result.saved_files)
    assert (parts[2].url, True) not in scenario.calls
    assert controller.phase == "cancelled"


@pytest.mark.parametrize(
    ("message", "expected_kind"),
    [
        ("HTTP Error 412: Precondition Failed", "PLATFORM_412"),
        ("HTTP Error 403: Forbidden", "ACCESS_403"),
    ],
)
def test_http_failures_are_classified_per_part(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    message: str,
    expected_kind: str,
) -> None:
    part = _parts(downloader, 1)[0]
    scenario = YdlScenario({part.url: _formats(1080)})

    def fail(_ydl: FakeYoutubeDL, _url: str) -> dict[str, Any]:
        raise RuntimeError(message)

    scenario.download_actions = {part.url: fail}
    _install_scenario(downloader, monkeypatch, scenario)

    result = downloader.download_videos(
        [part],
        _config(downloader, tmp_path),
        str(tmp_path / "output"),
        "bestvideo[height=1080]+bestaudio/best[height=1080]",
        lambda _status: None,
    )

    assert len(result.failed) == 1
    assert result.failed[0].error is not None
    assert result.failed[0].error.kind is getattr(downloader.ErrorKind, expected_kind)


def test_unwritable_output_is_classified_before_ffmpeg_or_ytdlp(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        downloader,
        "ensure_dir",
        lambda _path: (_ for _ in ()).throw(PermissionError(errno.EACCES, "permission denied")),
    )
    monkeypatch.setattr(
        downloader,
        "require_ffmpeg",
        lambda: pytest.fail("FFmpeg must not be probed for an unwritable output directory"),
    )

    with pytest.raises(downloader.AppError) as caught:
        downloader.download_videos(
            _parts(downloader, 1),
            _config(downloader, tmp_path),
            str(tmp_path / "forbidden"),
            "bestvideo+bestaudio/best",
            lambda _status: None,
        )

    assert caught.value.kind is downloader.ErrorKind.OUTPUT_PERMISSION


def test_insufficient_disk_space_is_classified_before_any_download(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parts = _parts(downloader, 2)
    scenario = YdlScenario({part.url: _formats(1080, sized=True) for part in parts})
    _install_scenario(downloader, monkeypatch, scenario)
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))

    with pytest.raises(downloader.AppError) as caught:
        downloader.download_videos(
            parts,
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "bestvideo[height=1080]+bestaudio/best[height=1080]",
            lambda _status: None,
        )

    assert caught.value.kind is downloader.ErrorKind.DISK_FULL
    assert scenario.calls == [(part.url, False) for part in parts]


@pytest.mark.parametrize("kind_name", ["FFMPEG_MISSING", "FFMPEG_BROKEN"])
def test_ffmpeg_probe_failures_keep_their_classification_and_skip_ytdlp(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind_name: str,
) -> None:
    kind = getattr(downloader.ErrorKind, kind_name)

    def unavailable() -> str:
        raise downloader.AppError(kind, "synthetic FFmpeg probe failure")

    monkeypatch.setattr(downloader, "require_ffmpeg", unavailable)

    with pytest.raises(downloader.AppError) as caught:
        downloader.download_videos(
            _parts(downloader, 1),
            _config(downloader, tmp_path),
            str(tmp_path / "output"),
            "bestvideo+bestaudio/best",
            lambda _status: None,
        )

    assert caught.value.kind is kind
