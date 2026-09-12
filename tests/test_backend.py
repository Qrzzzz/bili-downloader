from __future__ import annotations

import importlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def test_protocol_rejects_ambiguous_and_unbounded_json():
    from app.backend.protocol import ProtocolError, decode_request
    for line in (b'{"v":1,"v":1}', b'{"n":NaN}', b'[]', b'\xff', b'x' * 65537):
        with pytest.raises(ProtocolError):
            decode_request(line)
    valid = {"v": 1, "type": "request", "id": "r1", "method": "hello", "params": {}}
    assert decode_request(json.dumps(valid).encode()) == valid
    valid["v"] = True
    with pytest.raises(ProtocolError):
        decode_request(json.dumps(valid).encode())


@pytest.fixture
def backend():
    from app.backend.host import Backend
    messages = []
    host = Backend(lambda value, lossy=False: messages.append(value))
    def send(method, params=None, id=None):
        request_id = id or f"r{len(host.seen) + 1}"
        host.request({"id": request_id, "method": method, "params": params or {}})
        return next(m for m in reversed(messages) if m.get("id") == request_id)
    send("hello", {"protocol_version": 1, "frontend_version": "2.12"})
    yield host, messages, send
    host.close()
    host.wait()


def finish(host):
    with host.lock:
        thread = host.active.thread if host.active else None
    if thread:
        thread.join(3)
        assert not thread.is_alive()


@pytest.mark.parametrize("share", [
    "【华强卖瓜-大厂版】\nhttps://www.bilibili.com/video/BV1kkbC6eEgm/?share_source=test&p=2",
    "[视频](https://www.bilibili.com/video/BV1kkbC6eEgm/?p=2&share_source=test)",
])
def test_parse_share_text_through_backend(backend, monkeypatch, share):
    from app.downloader import VideoInfoResult, VideoPart

    host, messages, send = backend
    url = "https://www.bilibili.com/video/BV1kkbC6eEgm?p=2"
    calls = []

    def parse(value, *args):
        calls.append(value)
        return VideoInfoResult("分享视频", "", 1, "", [VideoPart(2, "第二部分", url)], [],
                               source_url=url, current_part_index=2)

    monkeypatch.setattr("app.services.parse_service.parse_video_info", parse)
    response = send("parse.start", {"input": share, "credential_mode": "anonymous"})
    assert response["ok"]
    finish(host)
    assert calls == [url]
    terminal = next(m for m in messages if m.get("event") == "operation.completed")
    assert terminal["data"]["result"]["source_url"] == url
    assert terminal["data"]["result"]["current_part_index"] == 2
    assert host.parsed is not None and host.active is None


def test_handshake_and_duplicate_requests_fail_closed(backend):
    host, messages, send = backend
    assert not send("hello", {"protocol_version": 2, "frontend_version": "2.12"})["ok"]
    assert send("settings.get", id="unique")["ok"]
    assert send("settings.get", id="unique")["error"]["code"] == "duplicate_request"
    assert send("download.start", {"parse_id": "untrusted"})["ok"] is False
    assert host.active is None


def test_parse_cancel_discards_late_result_and_does_not_block_reader(backend, monkeypatch):
    from app.downloader import VideoInfoResult
    host, messages, send = backend
    entered, release = threading.Event(), threading.Event()
    def parse(*args):
        entered.set()
        release.wait(2)
        return VideoInfoResult("old", "", 1, "", [], [])
    monkeypatch.setattr("app.backend.host.parse_video", parse)
    started = send("parse.start", {"input": "BV1TEST", "credential_mode": "anonymous"})
    assert entered.wait(1)
    assert send("settings.get")["ok"]
    assert send("auth.qr.start", {"consent": True})["error"]["code"] == "backend_busy"
    opid = started["result"]["operation_id"]
    assert send("operation.cancel", {"operation_id": opid})["result"]["state"] == "cancel_requested"
    release.set()
    finish(host)
    assert host.parsed is None
    terminals = [m for m in messages if m.get("operation_id") == opid and m.get("event", "").startswith("operation.")]
    assert [m["event"] for m in terminals] == ["operation.cancelled"]


def test_download_retry_keeps_snapshot_and_rejects_stale_input(backend, monkeypatch, tmp_path):
    from app.cookies import CredentialMode
    from app.backend.host import ParsedVideo
    from app.downloader import VideoInfoResult, VideoPart, FormatChoice, DownloadBatchResult, PartDownloadResult, PartDownloadStatus
    from app.utils import ErrorClassification, ErrorKind
    host, messages, send = backend
    parts = [VideoPart(1, "first", "https://www.bilibili.com/video/BV1TEST?p=1"), VideoPart(2, "second", "https://www.bilibili.com/video/BV1TEST?p=2")]
    info = VideoInfoResult("test", "", 1, "", parts, [FormatChoice("1080p", "bestvideo[height=1080]+bestaudio/best", 1080)])
    host.parsed = ParsedVideo("p1", host.revision, info, CredentialMode.ANONYMOUS, None)
    calls = []
    def download(request, controller, progress, log, selected):
        calls.append((request, selected))
        if len(calls) == 1:
            return DownloadBatchResult([PartDownloadResult(parts[0], PartDownloadStatus.COMPLETED, (str(tmp_path / 'a.mp4'),)),
                                        PartDownloadResult(parts[1], PartDownloadStatus.FAILED, error=ErrorClassification(ErrorKind.TIMEOUT, 'timeout', True))])
        return DownloadBatchResult([PartDownloadResult(parts[1], PartDownloadStatus.COMPLETED, (str(tmp_path / 'b.mp4'),))])
    monkeypatch.setattr("app.backend.host.run_download", download)
    send("download.start", {"parse_id": "p1", "part_indices": [1, 2], "format_id": "0", "download_mode": "audio_video", "credential_mode": "anonymous", "download_dir": str(tmp_path)})
    finish(host)
    batch_id = next(iter(host.batches))
    send("settings.update", {"download_dir": str(tmp_path / 'new')})
    assert send("download.retry", {"batch_id": batch_id})["ok"]
    finish(host)
    assert calls[0][0] is calls[1][0]
    assert calls[1][1] == (parts[1],)
    assert len(host.batches[batch_id][1].completed) == 2
    send("parse.invalidate")
    assert send("download.retry", {"batch_id": batch_id})["error"]["code"] == "stale_batch"


def test_progress_dto_never_exposes_media_headers_or_nonfinite_values():
    from app.backend.dto import progress_dto
    result = progress_dto({"phase": "downloading", "info_dict": {"url": "secret"}, "http_headers": {"Cookie": "secret"}, "speed": float('nan'), "total_bytes": True})
    assert "secret" not in json.dumps(result)
    assert result["speed_bytes_per_second"] is None
    assert result["total_bytes"] is None


def test_fresh_backend_process_rejects_any_qt_import(isolated_paths):
    root = Path(__file__).resolve().parents[1]
    code = '''import sys, importlib.abc, runpy
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(("PySide", "PyQt", "shiboken")):
            raise AssertionError("Forbidden UI import: " + fullname)
sys.meta_path.insert(0, NoQt())
sys.path.insert(0, sys.argv[1])
runpy.run_module("app.backend", run_name="__main__")
'''
    requests = [dict(v=1, type='request', id='r1', method='hello', params={'protocol_version': 1, 'frontend_version': '2.12'}),
                dict(v=1, type='request', id='r2', method='shutdown', params={})]
    process = subprocess.run([sys.executable, '-I', '-c', code, str(root)], input=''.join(json.dumps(r)+'\n' for r in requests), capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert process.returncode == 0, process.stderr
    output = [json.loads(line) for line in process.stdout.splitlines()]
    assert output[0]['result']['backend_version'] == '2.12'
    assert output[-1]['event'] == 'shutdown.ready'
