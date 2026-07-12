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
    closed: bool = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

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

    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        self.scenario.calls.append((url, download))
        if not download:
            if url not in self.scenario.formats_by_url:
                raise AssertionError(f"unexpected preflight URL: {url}")
            return {
                "id": Path(url).name,
                "formats": copy.deepcopy(self.scenario.formats_by_url[url]),
            }
        if url not in self.scenario.download_actions:
            raise AssertionError(f"unexpected download URL: {url}")
        return self.scenario.download_actions[url](self, url)


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

        output = Path(ydl.options["paths"]["home"]) / filename
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
        "https://www.bilibili.com/video/BV1Synthetic99"
        "?spm_id_from=333.999&p=3&utm_source=unit-test#comments"
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
    assert captured["allow_redirects"] is True
    assert captured["timeout"] == 15
    assert response.closed is True


def test_b23_external_redirect_is_rejected(
    downloader: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse("https://attacker.invalid/video/BV1Synthetic99?p=3")
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError):
        downloader.resolve_bilibili_url("https://b23.tv/synthetic")

    assert response.closed is True


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
    assert {Path(path).name for path in result.saved_files} == {"p1-final.mp4", "p3-final.mp4"}


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
        output = Path(ydl.options["paths"]["home"]) / "p2-merged.mp4"
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
    assert {Path(path).name for path in result.saved_files} == {
        "p1-complete.mp4",
        "p2-merged.mp4",
    }
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
