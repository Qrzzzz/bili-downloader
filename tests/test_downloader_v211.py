"""Deferred cancellation with real metadata and full decode verification (#29)."""
from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

import pytest

from tests.test_downloader_v210 import media


@pytest.mark.parametrize("mode_name", ["audio_video", "audio_mp3"])
@pytest.mark.parametrize("scenario", ["normal", "deferred", "corrupt", "timeout", "before_processing", "during_verification"])
def test_cancel_preserves_only_verified_outputs(media, monkeypatch, tmp_path, mode_name, scenario):
    from app import downloader as d
    from app.backend.dto import batch_dto
    from app.services.download_service import DownloadRequest, run_download

    mode = d.DownloadMode(mode_name)
    audio = mode is d.DownloadMode.AUDIO_MP3
    fixture = media.root / "192.mp3" if audio else media.video
    parts = tuple(d.VideoPart(i, f"part{i}", f"https://www.bilibili.com/video/BV1Synthetic99?p={i}",
                              id=f"part{i}") for i in (1, 2, 3))
    selector = "bestaudio/best" if audio else "bestvideo[height=240]+bestaudio"
    plan = d.DownloadPlan(tuple(d.PlannedPart(p, selector) for p in parts), None if audio else 240, mode)
    controller = d.DownloadController()
    calls, waiting, events, probes = [], [], [], []
    monkeypatch.setattr(d, "require_ffmpeg", lambda: media.ffmpeg)
    monkeypatch.setattr(d, "prepare_download_plan", lambda *a, **k: plan)
    original_probe = d._run_media_probe

    def probe(command, cancel=None, **kwargs):
        probes.append(command)
        if "-xerror" in command and any("P002-" in str(arg) for arg in command):
            slow = [sys.executable, "-c", "import time; time.sleep(30)"]
            if scenario == "timeout":
                return original_probe(slow, cancel, timeout=0.1)
            if scenario == "during_verification":
                timer = threading.Timer(0.1, controller.cancel)
                timer.start()
                try:
                    return original_probe(slow, cancel, timeout=5)
                finally:
                    timer.cancel()
                    timer.join()
        return original_probe(command, cancel, **kwargs)

    monkeypatch.setattr(d, "_run_media_probe", probe)

    class LocalDownload:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert download
            index = next(p.index for p in parts if p.url == url)
            calls.append(index)
            if index == 2 and scenario == "before_processing":
                controller.cancel()
            for hook in self.options["postprocessor_hooks"]:
                hook({"status": "started"})
            if index == 2 and scenario in {"deferred", "corrupt", "timeout"}:
                controller.cancel()
                waiting.append(controller.waiting_for_postprocessing)
            target = Path(self.options["outtmpl"]["default"].replace("%(ext)s", fixture.suffix[1:]))
            if index == 2 and scenario == "corrupt":
                if audio:
                    target.write_bytes(b"not an MP3 file")
                else:
                    shutil.copyfile(media.broken, target)
            else:
                shutil.copyfile(fixture, target)
            for hook in self.options["postprocessor_hooks"]:
                hook({"status": "finished"})
            for hook in self.options["post_hooks"]:
                hook(str(target))
            return {"filepath": str(target)}

    monkeypatch.setattr(d, "_youtube_dl", LocalDownload)
    request = DownloadRequest(parts[0].url, "local fixture", parts, d.AppConfig(), str(tmp_path),
                              selector, "fixture", d.CredentialMode.ANONYMOUS, mode)
    result = run_download(request, controller, events.append, lambda _: None)
    expected = {
        "normal": ["completed", "completed", "completed"],
        "deferred": ["completed", "completed", "cancelled"],
        "corrupt": ["completed", "failed", "cancelled"],
        "timeout": ["completed", "failed", "cancelled"],
        "before_processing": ["completed", "cancelled", "cancelled"],
        "during_verification": ["completed", "cancelled", "cancelled"],
    }[scenario]
    assert [p.status.value for p in result.part_results] == expected
    assert calls == ([1, 2, 3] if scenario == "normal" else [1, 2])
    assert len(result.saved_files) == expected.count("completed")
    if scenario in {"deferred", "corrupt", "timeout"}:
        assert waiting == [True]
    # The production IPC DTO must expose the same retained paths and statuses.
    dto = batch_dto(result, "batch", True, str(tmp_path))
    assert dto["saved_files"] == list(result.saved_files)
    assert [p["status"] for p in dto["part_results"]] == expected
    for path in result.saved_files:
        assert d._matches_output_spec(Path(path), plan, media.ffmpeg)
        assert any("-xerror" in command and path in command for command in probes)
    if scenario != "normal":
        assert controller.cancelled and controller.phase == "cancelled"
