"""Bounded budgets, full decode integrity and cross-process fair admission."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("duration,size,expected", [(None, 0, 300), (10, 0, 300),
    (3600, 0, 1800), (1200, 0, 630), (1200, 160 * 1024 * 1024, 640), (1000000, 0, 1800)])
def test_verification_budget_is_bounded(duration, size, expected):
    d = importlib.import_module("app.downloader")
    metadata = d._MediaMetadata(frozenset({"mp4"}), True, (360,), True, duration_seconds=duration)
    assert d._verification_budget(metadata, size) == expected


@pytest.mark.parametrize("value", [None, True, False, "NaN", "inf", "-inf", "bad", -1, 0])
def test_invalid_duration_never_expands_budget(value):
    d = importlib.import_module("app.downloader")
    assert d._media_duration(value) is None


@pytest.fixture(scope="module")
def long_media(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Local complete decode requires FFmpeg")
    root = tmp_path_factory.mktemp("v36-long")
    seed, output = root / "seed.mp4", root / "long.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", "libx264", "-c:a", "aac",
        str(seed)], check=True, capture_output=True, timeout=30)
    subprocess.run([ffmpeg, "-v", "error", "-stream_loop", "-1", "-i", str(seed), "-t", "1200",
        "-c", "copy", "-movflags", "+faststart", str(output)], check=True, capture_output=True, timeout=30)
    return ffmpeg, output


@pytest.mark.parametrize("fallback", [False, True])
def test_long_media_full_decode_and_truncation(long_media, tmp_path, monkeypatch, fallback):
    d = importlib.import_module("app.downloader")
    ffmpeg, output = long_media
    original = d._run_media_probe
    budgets = []
    def observe(command, *args, **kwargs):
        if fallback and "-show_entries" in command:
            return None
        if "-xerror" in command:
            budgets.append(kwargs["timeout"])
        return original(command, *args, **kwargs)
    monkeypatch.setattr(d, "_run_media_probe", observe)
    plan = d.DownloadPlan((), 240)
    assert d._matches_output_spec(output, plan, ffmpeg)
    assert 600 < budgets[-1] < 700
    broken = tmp_path / "truncated.mp4"
    data = output.read_bytes()
    broken.write_bytes(data[:len(data) // 2])
    assert not d._matches_output_spec(broken, plan, ffmpeg)


@pytest.mark.parametrize("cancel", [False, True])
def test_probe_timeout_and_cancel_reap_real_child(monkeypatch, cancel):
    d = importlib.import_module("app.downloader")
    original = d.subprocess.Popen
    children = []
    def capture(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(d.subprocess, "Popen", capture)
    controller = d.DownloadController()
    timer = threading.Timer(0.15, controller.cancel) if cancel else None
    started = time.monotonic()
    try:
        if timer:
            timer.start()
            with pytest.raises(d.DownloadCancelled):
                d._run_media_probe([sys.executable, "-c", "import time; time.sleep(30)"], controller, timeout=5)
        else:
            assert d._run_media_probe([sys.executable, "-c", "import time; time.sleep(30)"], controller, timeout=0.15) is None
    finally:
        if timer:
            timer.cancel(); timer.join()
    assert time.monotonic() - started < 2
    assert len(children) == 1 and children[0].poll() is not None


def test_fifo_stages_cancel_and_crashed_waiter(tmp_path):
    from app.config import app_data_dir
    peer = Path(__file__).parent / "fixtures" / "media_peer.py"
    root = app_data_dir() / "media-waiters"
    children = []
    order = tmp_path / "order.txt"
    def start(name):
        child = subprocess.Popen([sys.executable, str(peer), name], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "BILI_MEDIA_ORDER": str(order)})
        children.append(child)
        assert child.stdout.readline().strip() == "requested"
        return child
    def tickets(count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(list(root.glob("*.ticket"))) == count:
                return
            time.sleep(0.01)
        pytest.fail("waiter not registered")
    try:
        owner = start("merge")
        assert owner.stdout.readline().strip() == "acquired"
        abandoned = start("abandoned"); tickets(1)
        first = start("first"); tickets(2)
        second = start("second"); tickets(3)
        cancelled = start("cancelled"); tickets(4)
        cancelled.stdin.write("cancel\n"); cancelled.stdin.flush()
        assert cancelled.communicate(timeout=3)[0].strip() == "cancelled"
        abandoned.kill(); abandoned.communicate(timeout=3)
        started = time.monotonic()
        owner.stdin.write("release\n"); owner.stdin.flush()
        assert first.stdout.readline().strip() == "acquired"
        first.communicate(timeout=3)
        assert second.stdout.readline().strip() == "acquired"
        second.communicate(timeout=3)
        assert owner.communicate(timeout=3)[0].strip() == "verification-acquired"
        assert time.monotonic() - started < 2
        assert not list(root.glob("*.ticket"))
        assert order.read_text(encoding="utf-8").splitlines() == ["merge", "first", "second", "merge-verification"]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)
