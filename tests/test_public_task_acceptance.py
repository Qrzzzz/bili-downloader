"""Offline acceptance-report checks; these do not constitute public evidence."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tools.public_task_lifecycle import accepted


def media_result(mode="audio_video", indices=(1,)):
    audio = {"codec_type": "audio", "bit_rate": "192000"}
    return {"state": "completed", "saved_files_consistent": True,
        "parts": [{"index": i, "status": "completed", "file_count": 1} for i in indices],
        "outputs": [{"bytes": 1024, "ffprobe_exit": 0, "full_decode_exit": 0,
            "probe": {"streams": [audio] + ([{"codec_type": "video", "height": 360}] if mode == "audio_video" else []),
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2" if mode == "audio_video" else "mp3"}}} for _ in indices]}


@pytest.mark.parametrize("mode", ["audio_video", "audio_mp3"])
def test_report_requires_every_selected_real_output(mode):
    result = media_result(mode, (1, 2))
    assert accepted(result, [1, 2], mode)
    missing = deepcopy(result)
    missing["parts"].pop()
    missing["outputs"].pop()
    assert not accepted(missing, [1, 2], mode)
    cancelled = deepcopy(result)
    cancelled["parts"][1]["status"] = "cancelled"
    assert not accepted(cancelled, [1, 2], mode)


@pytest.mark.parametrize("field,value", [("ffprobe_exit", 1), ("full_decode_exit", 1), ("bytes", 0)])
def test_completed_label_does_not_hide_invalid_media(field, value):
    result = media_result()
    result["outputs"][0][field] = value
    assert not accepted(result, [1], "audio_video")


def test_report_checks_frozen_height_audio_bitrate_container_and_file_consistency():
    result = media_result()
    assert not accepted(result, [1], "audio_video", 720)
    assert accepted(result, [1], "audio_video", None)
    result["saved_files_consistent"] = False
    assert not accepted(result, [1], "audio_video")
    result = media_result("audio_mp3")
    result["outputs"][0]["probe"]["streams"][0]["bit_rate"] = "128000"
    assert not accepted(result, [1], "audio_mp3")
    result = media_result()
    result["outputs"][0]["probe"]["format"]["format_name"] = "matroska"
    assert not accepted(result, [1], "audio_video")


def test_retry_report_retains_first_failure_and_verifies_final_outputs(tmp_path, monkeypatch):
    import socket
    from types import SimpleNamespace
    from app.downloader import FormatChoice, VideoInfoResult, VideoPart
    from tools import public_task_smoke as smoke
    from tools import public_task_lifecycle as lifecycle
    from app.services import task_service, parse_service
    from app import utils

    class Manager:
        instance = None

        def __init__(self, *args):
            Manager.instance = self
            self.running = {}
            self.tasks = {}
            self.repo = SimpleNamespace(get=lambda key: self.tasks[key])

        def create(self, parsed, indices, mode, choice, directory, token):
            key = str(len(self.tasks))
            self.tasks[key] = {"task_id": key, "state": "failed" if key == "1" else "completed",
                "mode": mode, "attempts": [{}], "result": {"part_results": []}}
            return {"task": self.tasks[key]}

        def start_ready(self):
            self.running = {"0": None, "1": None}

        def command(self, action, key):
            assert action == "retry"
            self.tasks[key]["state"] = "completed"
            self.tasks[key]["attempts"].append({})

        def close(self):
            pass

        def wait(self):
            pass

    def inspect(task, ffmpeg):
        value = media_result(task["mode"])
        value["state"] = task["state"]
        if task["state"] == "failed":
            value["parts"][0]["status"] = "failed"
            value["parts"][0]["file_count"] = 0
            value["outputs"] = []
        return value

    monkeypatch.setattr(task_service, "TaskManager", Manager)
    info = VideoInfoResult("sample", "uploader", 60, "", [VideoPart(1, "part", "", 60)],
        [FormatChoice("360p", "best", 360)])
    monkeypatch.setattr(parse_service, "parse_video", lambda *args: info)
    monkeypatch.setattr(utils, "probe_ffmpeg", lambda: SimpleNamespace(available=True, path="ffmpeg.exe",
        status=SimpleNamespace(value="available")))
    monkeypatch.setattr(lifecycle, "inspect_task", inspect)
    monkeypatch.setattr(smoke.ssl, "create_default_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(smoke.time, "sleep", lambda _: Manager.instance.running.clear())
    report = tmp_path / "report.json"
    assert smoke.worker(report, tmp_path) == 0
    import json
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["initial_outcome"] == "failed" and data["outcome"] == "passed"
    retried = data["tasks"][1]
    assert retried["first_attempt"]["state"] == "failed"
    assert retried["final"]["state"] == "completed" and retried["final"]["outputs"]
    assert retried["retained_attempts"] == 2
    assert "failure_retry" not in data["not_executed"]
