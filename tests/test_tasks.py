from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def until(predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "scheduler did not settle"


@pytest.fixture
def queue_env(isolated_paths, monkeypatch):
    from app.config import AppConfig
    from app.cookies import CredentialMode
    from app.downloader import VideoInfoResult, VideoPart, FormatChoice
    from app.services.task_service import TaskManager

    folder = isolated_paths.root / "downloads"
    folder.mkdir()
    events = []
    manager = TaskManager(events.append, AppConfig(download_dir=str(folder)), "test-owner")

    def parsed(name="BV1234567890", mode=CredentialMode.ANONYMOUS, generation=None, count=1):
        url = "https://www.bilibili.com/video/" + name
        info = VideoInfoResult(name, "fixture", 1, "", [VideoPart(i, f"part-{i}", url + f"?p={i}", 1, f"cid-{i}") for i in range(1, count + 1)],
                               [FormatChoice("最高可用", "bestvideo+bestaudio/best")], raw_id=name, source_url=url)
        return SimpleNamespace(info=info, mode=mode, generation=generation)

    monkeypatch.setattr("app.services.task_service.parse_video", lambda value, *args: parsed(value.split("/")[-1]).info)

    def create(name="BV1234567890", **kwargs):
        p = parsed(name, **kwargs)
        return manager.create(p, [p.info.current_part_index], "audio_video", "0", str(folder), name)["task"]

    yield manager, create, parsed, folder, events
    manager.close()
    manager.wait()


def test_parallel_cancel_isolated_and_queue_refills(queue_env, monkeypatch):
    from app.downloader import DownloadBatchResult, PartDownloadResult, PartDownloadStatus
    manager, create, _, _, _ = queue_env
    entered, released = set(), threading.Event()

    def download(request, controller, progress, log, parts):
        entered.add(request.video_title)
        progress({"phase": "downloading", "overall_percent": 25})
        while not released.wait(0.01) and not controller.cancelled:
            pass
        status = PartDownloadStatus.CANCELLED if controller.cancelled else PartDownloadStatus.COMPLETED
        return DownloadBatchResult(PartDownloadResult(p, status) for p in parts)

    monkeypatch.setattr("app.services.task_service.run_download", download)
    a, b, c = [create(f"BV123456789{i}") for i in range(3)]
    manager.start_ready()
    until(lambda: len(entered) == 2)
    manager.command("cancel", a["task_id"])
    until(lambda: len(entered) == 3)
    with manager.lock:
        assert manager.repo.get(a["task_id"])["state"] == "cancelled"
        assert b["task_id"] in manager.running
        assert c["task_id"] in manager.running
    released.set()
    until(lambda: not manager.running)
    states = {t["task_id"]: t["state"] for t in manager.snapshot()["tasks"]}
    assert states[b["task_id"]] == states[c["task_id"]] == "completed"


def test_pause_and_lower_limit_do_not_interrupt_active_tasks(queue_env, monkeypatch):
    from dataclasses import replace
    from app.downloader import DownloadBatchResult
    manager, create, _, _, _ = queue_env
    stop = threading.Event()
    entered = []
    def download(request, *args):
        entered.append(request.video_title)
        stop.wait(3)
        return DownloadBatchResult([])
    monkeypatch.setattr("app.services.task_service.run_download", download)
    for i in range(3):
        create(f"BV123456789{i}")
    manager.paused = True
    manager.start_ready()
    assert not manager.running
    manager.paused = False
    manager.start_ready()
    until(lambda: len(entered) == 2)
    manager.config = replace(manager.config, max_parallel=1)
    manager.paused = True
    assert all(not c.cancelled for c, _ in manager.running.values())
    stop.set()
    until(lambda: not manager.running)
    assert len(entered) == 2
    manager.paused = False
    manager.start_ready()
    until(lambda: len(entered) == 3 and not manager.running)


def test_durable_idempotency_and_foreign_owner(queue_env):
    from app.backend.protocol import ProtocolError
    from app.services.task_service import TaskManager
    manager, create, parsed, folder, _ = queue_env
    task = create()
    duplicate = manager.create(parsed(), [1], "audio_video", "0", str(folder), "second-token")
    assert duplicate["duplicate"] and duplicate["task"]["task_id"] == task["task_id"]
    with pytest.raises(ProtocolError, match="另一任务"):
        manager.create(parsed("BV1234567891"), [1], "audio_video", "0", str(folder), "BV1234567890")
    other = TaskManager(lambda _: None, manager.config, "second-owner")
    try:
        assert other.snapshot()["tasks"][0]["foreign"]
        with pytest.raises(ProtocolError, match="另一个"):
            other.command("resume", task["task_id"])
        manager.close(); manager.wait()
        recovered = other.snapshot()["tasks"][0]
        assert recovered["state"] == "interrupted" and not recovered["foreign"]
        assert not other.running
        other.command("resume", task["task_id"])
        assert other.snapshot()["tasks"][0]["state"] == "queued"
    finally:
        other.close(); other.wait()


def test_generation_change_blocks_before_network_and_requires_confirmation(queue_env, monkeypatch):
    from app.cookies import CredentialMode
    from app.backend.protocol import ProtocolError
    manager, create, _, _, _ = queue_env
    task = create(mode=CredentialMode.SAVED, generation="old")
    monkeypatch.setattr("app.services.task_service.session_status", lambda: {"generation": "new"})
    calls = []
    monkeypatch.setattr("app.services.task_service.parse_video", lambda *a: calls.append(a))
    manager.start_ready()
    until(lambda: not manager.running)
    assert not calls
    assert manager.snapshot()["tasks"][0]["state"] == "blocked"
    with pytest.raises(ProtocolError, match="确认"):
        manager.command("resume", task["task_id"])
    manager.command("resume", task["task_id"], True)
    with manager.lock:
        assert manager.repo.get(task["task_id"])["spec"]["generation"] == "new"


def test_worker_start_failure_settles_and_releases_slot(queue_env, monkeypatch):
    manager, create, _, _, _ = queue_env
    create()
    monkeypatch.setattr(threading.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("cannot start")))
    manager.start_ready()
    assert not manager.running
    assert manager.snapshot()["tasks"][0]["state"] == "failed"


def test_deduplicated_submission_token_remains_idempotent_after_completion(queue_env):
    manager, create, parsed, folder, _ = queue_env
    original = create()
    manager.create(parsed(), [1], "audio_video", "0", str(folder), "alias")
    with manager.lock, manager.repo.db:
        task = manager.repo.get(original["task_id"])
        task["state"] = "completed"
        manager._save(task)
    reply = manager.create(parsed(), [1], "audio_video", "0", str(folder), "alias")
    assert reply["duplicate"] and reply["task"]["task_id"] == original["task_id"]
    assert len(manager.snapshot()["tasks"]) == 1


def test_retry_preserves_success_and_does_not_depend_on_input(queue_env, monkeypatch):
    from app.downloader import DownloadBatchResult, PartDownloadResult, PartDownloadStatus
    manager, _, parsed, folder, _ = queue_env
    p = parsed(count=2)
    monkeypatch.setattr("app.services.task_service.parse_video", lambda *args: p.info)
    task = manager.create(p, [1, 2], "audio_video", "0", str(folder), "multi")["task"]
    runs = []
    def run(request, controller, progress, log, parts):
        runs.append([p.index for p in parts])
        return DownloadBatchResult(PartDownloadResult(p, PartDownloadStatus.FAILED if len(runs) == 1 and p.index == 2 else PartDownloadStatus.COMPLETED,
                                                      () if p.index == 2 and len(runs) == 1 else (str(folder / f"p{p.index}.mp4"),)) for p in parts)
    monkeypatch.setattr("app.services.task_service.run_download", run)
    manager.start_ready(); until(lambda: not manager.running)
    first = manager.snapshot()["tasks"][0]
    assert first["state"] == "partial"
    manager.command("retry", task["task_id"]); manager.start_ready(); until(lambda: not manager.running)
    result = manager.repo.get(task["task_id"])
    assert runs == [[1, 2], [2]]
    assert result["state"] == "completed" and len(result["result"]["saved_files"]) == 2
    assert result["attempt_id"] != first["attempt_id"]


def test_batch_extraction_keeps_invalid_inputs_outside_valid_urls():
    from app.video_urls import extract_video_inputs
    rows = extract_video_inputs("one https://www.bilibili.com/video/BV1234567890?p=2&share=x\n"
                                "[same](https://www.bilibili.com/video/BV1234567890?p=2)\n"
                                "https://evil.example/?url=https://www.bilibili.com/video/BV9999999999\nBV1234567891")
    assert len(rows) == 3
    assert rows[0]["input"].endswith("?p=2")
    assert rows[1]["message"] and rows[1]["input"] == "无效链接"
    assert rows[2]["input"].endswith("BV1234567891")
    with pytest.raises(ValueError):
        extract_video_inputs("\n".join(f"BV12345678{i:03}" for i in range(51)))


def test_resource_lease_cancellation_and_owner_release():
    from app.downloader import DownloadController
    from app.services.task_resources import FileLease, owner_alive, resource_lease
    from yt_dlp.utils import DownloadCancelled
    lease = FileLease("instance:resource-test")
    assert lease.acquire() and owner_alive("resource-test")
    controller = DownloadController(); controller.cancel()
    with pytest.raises(DownloadCancelled):
        with resource_lease("instance:resource-test", controller):
            pytest.fail("acquired a live lease")
    lease.close()
    assert not owner_alive("resource-test")


def test_space_reservations_are_shared_and_released(queue_env, monkeypatch):
    from app.services.task_resources import reserve_space
    from app.downloader import DownloadController
    from app.utils import AppError
    manager, _, _, folder, _ = queue_env
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=300 * 1024 * 1024))
    error = []
    def competitor():
        try:
            with reserve_space(str(folder), 100 * 1024 * 1024, manager.owner, DownloadController()):
                error.append("overallocated")
        except AppError:
            error.append("blocked")
    with reserve_space(str(folder), 100 * 1024 * 1024, manager.owner, DownloadController()):
        thread = threading.Thread(target=competitor); thread.start(); thread.join(2)
        assert not thread.is_alive()
    assert error == ["blocked"]
    with reserve_space(str(folder), 100 * 1024 * 1024, manager.owner, DownloadController()):
        pass
    with reserve_space(str(folder), None, manager.owner, DownloadController()) as grow:
        grow(100 * 1024 * 1024)
        with pytest.raises(AppError):
            grow(200 * 1024 * 1024)


@pytest.mark.parametrize("changed", ["parts", "quality"])
def test_changed_metadata_blocks_instead_of_changing_spec(queue_env, monkeypatch, changed):
    manager, _, parsed, folder, _ = queue_env
    p = parsed()
    task = manager.create(p, [1], "audio_video", "0", str(folder), "strict")["task"]
    if changed == "parts":
        p.info.parts[0].id = "replacement-cid"
    else:
        p.info.formats.clear()
    monkeypatch.setattr("app.services.task_service.parse_video", lambda *args: p.info)
    calls = []
    monkeypatch.setattr("app.services.task_service.run_download", lambda *args: calls.append(args))
    manager.start_ready(); until(lambda: not manager.running)
    assert not calls and manager.repo.get(task["task_id"])["state"] == "blocked"


def test_removing_record_preserves_output_and_snapshot_hides_details(queue_env):
    manager, create, _, folder, _ = queue_env
    task = create()
    file = folder / "keep.mp4"; file.write_bytes(b"kept")
    with manager.lock, manager.repo.db:
        stored = manager.repo.get(task["task_id"])
        stored.update(state="completed", logs="private task log", result={"saved_files": [str(file)]})
        manager._save(stored)
    public = manager.snapshot()["tasks"][0]
    assert public["logs"] == "" and public["result"] is None
    assert not {"spec", "token", "owner", "identity"} & public.keys()
    manager.command("remove", task["task_id"])
    assert file.read_bytes() == b"kept" and not manager.snapshot()["tasks"]


def test_account_gate_holds_saved_tasks_but_allows_anonymous(queue_env, monkeypatch):
    from app.cookies import CredentialMode
    from app.downloader import DownloadBatchResult
    manager, create, _, _, _ = queue_env
    saved = create("BV1234567891", mode=CredentialMode.SAVED, generation="old")
    anon = create("BV1234567892")
    manager.account_gate(True)
    calls = []
    def run(request, *args):
        calls.append(request.video_title)
        return DownloadBatchResult([])
    monkeypatch.setattr("app.services.task_service.run_download", run)
    manager.start_ready(); until(lambda: not manager.running)
    assert calls == ["BV1234567892"]
    assert manager.repo.get(saved["task_id"])["state"] == "queued"
    assert manager.repo.get(anon["task_id"])["state"] == "completed"


def test_terminal_storage_failure_stops_dispatch_and_releases_worker(queue_env, monkeypatch):
    import sqlite3
    from app.downloader import DownloadBatchResult
    manager, create, _, _, _ = queue_env
    create()
    monkeypatch.setattr("app.services.task_service.run_download", lambda *a: DownloadBatchResult([]))
    save = manager._save
    def fail_terminal(task):
        if task["state"] == "completed":
            raise sqlite3.OperationalError("disk full")
        save(task)
    monkeypatch.setattr(manager, "_save", fail_terminal)
    manager.start_ready(); until(lambda: not manager.running)
    assert manager.storage_failed and manager.paused
    assert manager.snapshot()["tasks"][0]["state"] == "interrupted"


def test_media_slot_serializes_and_waiting_cancel_does_not_cancel_owner():
    from app.downloader import DownloadController, _media_slot
    from yt_dlp.utils import DownloadCancelled
    first, second = DownloadController(), DownloadController()
    first.task_id, second.task_id = "first", "second"
    attempted, ended = threading.Event(), threading.Event()
    def other():
        attempted.set()
        try:
            with _media_slot(second):
                pytest.fail("simultaneous media slot")
        except DownloadCancelled:
            ended.set()
    with _media_slot(first):
        thread = threading.Thread(target=other); thread.start()
        assert attempted.wait(2)
        second.cancel()
        thread.join(2)
        assert ended.is_set() and first.media_held and not first.cancelled
    assert not first.media_held


def test_batch_registry_survives_new_editor_input(isolated_paths, monkeypatch):
    from app.backend.host import Backend
    from app import __version__
    from app.downloader import VideoInfoResult, VideoPart, FormatChoice
    calls, messages = [], []
    def parse(value, *a):
        calls.append(value)
        return VideoInfoResult(value, "", 1, "", [VideoPart(1, "p1", value, 1, "cid")],
                               [FormatChoice("最高可用", "bestvideo+bestaudio/best")], source_url=value, raw_id=value)
    monkeypatch.setattr("app.backend.host.parse_video", parse)
    host = Backend(lambda value, lossy=False: messages.append(value))
    def send(method, params=None):
        id = "r" + str(len(host.seen))
        host.request({"id": id, "method": method, "params": params or {}})
        return next(m for m in reversed(messages) if m.get("id") == id)
    try:
        send("hello", {"protocol_version": 2, "frontend_version": __version__})
        send("parse.batch", {"input": "https://www.bilibili.com/video/BV1234567890\nhttps://evil.example/video/BV1234567899\nhttps://www.bilibili.com/video/BV1234567891", "credential_mode": "anonymous"})
        until(lambda: host.active is None)
        assert len(calls) == 2
        terminal = next(m for m in messages if m.get("event") == "operation.completed")
        items = terminal["data"]["result"]["items"]
        assert items[1]["message"] and "video" not in items[1]
        send("parse.invalidate")
        send("queue.pause")
        response = send("tasks.create", {"parse_id": items[0]["video"]["parse_id"], "part_indices": [1], "download_mode": "audio_video", "format_id": "0", "download_dir": str(isolated_paths.root), "token": "from-batch"})
        assert response["ok"] and response["result"]["task"]["state"] == "queued"
        send("parse.invalidate")
        assert len(send("tasks.list")["result"]["tasks"]) == 1
    finally:
        host.close(); host.wait()
