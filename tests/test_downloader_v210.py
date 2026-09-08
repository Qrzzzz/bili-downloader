"""Real local media regressions for #24-26; never contact Bilibili."""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Real media regression requires FFmpeg on PATH")
    root = tmp_path_factory.mktemp("v210-media")
    video = root / "complete.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "4", "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(video)],
        check=True, capture_output=True, timeout=20,
    )
    broken = root / "truncated.mp4"
    data = video.read_bytes()
    broken.write_bytes(data[:len(data) // 2])
    for rate in (64, 192):
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "sine=frequency=440", "-t", "1", "-b:a", f"{rate}k", str(root / f"{rate}.mp3")],
            check=True, capture_output=True, timeout=20,
        )
    return SimpleNamespace(ffmpeg=ffmpeg, root=root, video=video, broken=broken)


@pytest.fixture
def batch(monkeypatch, tmp_path, media):
    d = importlib.import_module("app.downloader")
    service = importlib.import_module("app.services.download_service")
    parts = tuple(d.VideoPart(i, "audit", f"https://www.bilibili.com/video/BV1Synthetic99?p={i}",
                              id=f"audit{i}") for i in range(1, 4))
    plan = d.DownloadPlan(tuple(d.PlannedPart(p, "bestvideo[height=240]+bestaudio", 100_000)
                                for p in parts), 240)
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(d, "require_ffmpeg", lambda: media.ffmpeg)
    monkeypatch.setattr(d, "prepare_download_plan", lambda selected, *a, **k:
                        d.DownloadPlan(tuple(p for p in plan.parts if p.part in selected), 240))
    downloaded = []

    class LocalDownload:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert download
            target = Path(self.opts["outtmpl"]["default"].replace("%(ext)s", "mp4"))
            shutil.copyfile(media.video, target)
            downloaded.append(url)
            return {"filepath": str(target)}

    monkeypatch.setattr(d, "_youtube_dl", LocalDownload)
    request = service.DownloadRequest(parts[0].url, "audit", parts, d.AppConfig(), str(output),
                                      plan.parts[0].selector, "240p", d.CredentialMode.ANONYMOUS,
                                      d.DownloadMode.AUDIO_VIDEO)
    controller = d.DownloadController()
    events = []

    def run(selected=parts):
        return service.run_download(request, controller, events.append, lambda _: None, selected)

    def target(index, collision=1):
        return output / (d._output_stem(plan.parts[index - 1], plan, collision) + ".mp4")

    return SimpleNamespace(d=d, plan=plan, parts=parts, target=target, run=run,
                           downloaded=downloaded, events=events, controller=controller)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("filename,mode,valid", [
    ("complete.mp4", "audio_video", True), ("truncated.mp4", "audio_video", False),
    ("192.mp3", "audio_mp3", True), ("64.mp3", "audio_mp3", False),
])
def test_real_media_validation(media, monkeypatch, fallback, filename, mode, valid):
    d = importlib.import_module("app.downloader")
    if fallback:
        original = Path.is_file
        monkeypatch.setattr(Path, "is_file", lambda path:
                            False if path.stem.lower() == "ffprobe" else original(path))
    plan = d.DownloadPlan((), 240, d.DownloadMode(mode))
    assert d._matches_output_spec(media.root / filename, plan, media.ffmpeg) is valid


def test_truncated_existing_is_preserved_and_redownloaded(batch, media):
    original = batch.target(1)
    shutil.copyfile(media.broken, original)
    result = batch.run(batch.parts[:1])
    assert len(result.completed) == 1
    assert result.saved_files == (str(batch.target(1, 2).resolve()),)
    assert original.read_bytes() == media.broken.read_bytes()
    assert batch.downloaded == [batch.parts[0].url]


def test_new_truncated_output_cannot_report_success(batch, media, monkeypatch):
    original_copy = shutil.copyfile
    monkeypatch.setattr(shutil, "copyfile", lambda src, dst: original_copy(media.broken, dst))
    result = batch.run(batch.parts[:1])
    assert not result.saved_files
    assert len(result.failed) == 1


@pytest.mark.parametrize("failure", ["collision", "permission", "ordinary"])
def test_preparation_failure_retains_success_and_retry(batch, media, monkeypatch, failure):
    d = batch.d
    shutil.copyfile(media.video, batch.target(1))
    if failure == "collision":
        for n in range(1, d.OUTPUT_COLLISION_LIMIT + 1):
            batch.target(2, n).mkdir()
    else:
        original = d._choose_output_target

        def choose(target, planned, *args):
            if planned.part.index == 2:
                if failure == "permission":
                    raise PermissionError("isolated output preparation denied")
                raise RuntimeError("isolated per-item preparation failure")
            return original(target, planned, *args)

        monkeypatch.setattr(d, "_choose_output_target", choose)
    result = batch.run()
    expected = ["completed", "failed", "completed" if failure == "ordinary" else "failed"]
    assert [r.status.value for r in result.part_results] == expected
    assert str(batch.target(1).resolve()) in result.saved_files
    assert batch.parts[0].url not in batch.downloaded
    if failure == "collision":
        # Free one candidate; leave the other 999 occupied to exercise actual retry selection.
        batch.target(2, 1).rmdir()
    else:
        monkeypatch.setattr(d, "_choose_output_target", original)
    retry = batch.run(tuple(item.part for item in result.failed))
    merged = result.merged_with_retry(retry)
    assert len(merged.completed) == 3
    assert len(merged.saved_files) == 3
    assert merged.part_results[0] == result.part_results[0]


@pytest.mark.parametrize("reused,free,completed", [(3, 10_000, 3), (1, 10_000, 1),
                                                  (1, 17_000_000, 3), (0, 10_000, 0),
                                                  (0, 17_000_000, 3)])
def test_space_budget_counts_only_new_sequential_items(batch, media, monkeypatch, reused, free, completed):
    checks = []
    for i in range(1, reused + 1):
        shutil.copyfile(media.video, batch.target(i))

    def usage(_):
        checks.append(True)
        return SimpleNamespace(free=free)

    monkeypatch.setattr(batch.d.shutil, "disk_usage", usage)
    result = batch.run()
    assert len(result.completed) == completed
    assert len(batch.downloaded) == max(0, completed - reused)
    assert all(r.error.kind is batch.d.ErrorKind.DISK_FULL for r in result.failed)
    assert len(checks) == (0 if reused == 3 else (3 - reused if completed == 3 else 1))


@pytest.mark.parametrize("cancel", [False, True])
def test_media_process_timeout_or_cancel_reaps_real_child(monkeypatch, cancel):
    d = importlib.import_module("app.downloader")
    controller = d.DownloadController()
    children = []
    original = subprocess.Popen
    started = threading.Event()

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)

    def request_cancel():
        if started.wait(2):
            controller.cancel()

    thread = threading.Thread(target=request_cancel)
    if cancel:
        thread.start()
    before = time.monotonic()
    try:
        command = [sys.executable, "-c", "import time; time.sleep(60)"]
        if cancel:
            with pytest.raises(d.DownloadCancelled):
                d._run_media_probe(command, controller, timeout=3)
        else:
            assert d._run_media_probe(command, controller, timeout=0.1) is None
    finally:
        if cancel:
            thread.join(timeout=3)
    assert time.monotonic() - before < 3
    assert children and all(child.poll() is not None for child in children)


def test_cancel_after_reuse_preserves_previous_completed_item(batch, media):
    for i in (1, 2):
        shutil.copyfile(media.video, batch.target(i))
    original = batch.events.append

    def cancel_after_first(event):
        original(event)
        if event.get("phase") == "completed":
            batch.controller.cancel()

    d = batch.d
    with pytest.raises(d.DownloadBatchCancelled) as caught:
        d.download_videos(list(batch.parts), d.AppConfig(), str(batch.target(1).parent),
                          batch.plan.parts[0].selector, cancel_after_first,
                          controller=batch.controller, credential_mode="anonymous")
    assert [r.status.value for r in caught.value.result.part_results] == ["completed", "cancelled", "cancelled"]
    assert caught.value.result.saved_files == (str(batch.target(1).resolve()),)
