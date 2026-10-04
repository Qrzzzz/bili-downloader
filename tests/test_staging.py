from __future__ import annotations

import uuid

import pytest

from tests.test_tasks import queue_env, until


def staged(folder, task_id=None, data=b"resume"):
    path = folder / ".bili-tasks" / (task_id or uuid.uuid4().hex)
    path.mkdir(parents=True)
    if data is not None:
        (path / "video.part").write_bytes(data)
    return path


def save_state(manager, task_id, state):
    with manager.lock, manager.repo.db:
        task = manager.repo.get(task_id)
        task["state"] = state
        manager._save(task)


def test_scan_classifies_and_only_quarantines_selected_safe_items(queue_env):
    manager, create, _, folder, _ = queue_env
    expected = {}
    for i, state in enumerate(["queued", "failed", "cancelled", "interrupted", "partial", "completed"]):
        task = create(f"BV123456789{i}")
        save_state(manager, task["task_id"], state)
        path = staged(folder, task["task_id"])
        expected[path.name] = "active" if state == "queued" else "completed" if state == "completed" else "recoverable"
    orphan = staged(folder)
    expected[orphan.name] = "orphan"
    media = folder / "final.mp4"
    media.write_bytes(b"final")
    scan = manager.staging.scan()
    assert {e["task_id"]: e["category"] for e in scan["entries"]} == expected
    assert scan["total_bytes"] == 7 * 6 and scan["cleanable_bytes"] == 12
    result = manager.staging.apply([e["entry_id"] for e in scan["entries"]])
    assert [r["status"] for r in result["results"]].count("recycled") == 2
    assert media.read_bytes() == b"final"
    assert all((folder / ".bili-tasks" / i).exists() for i, c in expected.items() if c in {"active", "recoverable"})
    scan = manager.staging.scan()
    assert scan["total_bytes"] == 42 and scan["recycled_bytes"] == 12
    recycled = next(e for e in scan["entries"] if e["task_id"] == orphan.name)
    assert manager.staging.apply([recycled["entry_id"]], restore=True)["results"][0]["status"] == "restored"
    assert (orphan / "video.part").read_bytes() == b"resume"


def test_removed_record_directory_survives_path_change_and_restart(queue_env):
    from dataclasses import replace
    from app.services.task_service import TaskManager
    manager, create, _, folder, _ = queue_env
    task = create()
    path = staged(folder, task["task_id"])
    save_state(manager, task["task_id"], "failed")
    manager.command("remove", task["task_id"])
    manager.config = replace(manager.config, download_dir=str(folder / "new"))
    db = manager.repo.path
    manager.close(); manager.wait()
    reopened = TaskManager(lambda _: None, manager.config, "reopened", db)
    try:
        entry = reopened.staging.scan()["entries"][0]
        assert entry["path"] == str(path) and entry["category"] == "orphan"
    finally:
        reopened.close(); reopened.wait()


def test_preview_rechecks_task_state_and_changes(queue_env):
    import os
    manager, create, _, folder, _ = queue_env
    task = create()
    save_state(manager, task["task_id"], "completed")
    path = staged(folder, task["task_id"])
    entry = manager.staging.scan()["entries"][0]
    save_state(manager, task["task_id"], "queued")
    assert manager.staging.apply([entry["entry_id"]])["results"][0]["status"] == "protected"
    save_state(manager, task["task_id"], "completed")
    entry = manager.staging.scan()["entries"][0]
    (path / "more.part").write_bytes(b"changed")
    assert "发生变化" in manager.staging.apply([entry["entry_id"]])["results"][0]["message"]
    assert path.exists()
    nested = path / "nested"
    nested.mkdir()
    file = nested / "fragment.part"
    file.write_bytes(b"old")
    entry = manager.staging.scan()["entries"][0]
    before = file.stat()
    file.write_bytes(b"new")  # Same byte count, nested parent does not change.
    os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert "发生变化" in manager.staging.apply([entry["entry_id"]])["results"][0]["message"]


def test_foreign_staging_lock_protects_orphan(queue_env):
    from app.services.task_staging import stage_lease
    manager, _, _, folder, _ = queue_env
    path = staged(folder)
    entry = manager.staging.scan()["entries"][0]
    lock = stage_lease(str(folder), path.name)
    assert lock.acquire()
    try:
        assert manager.staging.scan()["entries"][0]["category"] == "active"
        # Rebuild the original preview to check a lock acquired after scanning.
        manager.staging.preview[entry["entry_id"]] = {"row": entry, "root": folder, "path": path,
            "fingerprint": (), "recycled": False}
        assert "占用" in manager.staging.apply([entry["entry_id"]])["results"][0]["message"]
    finally:
        lock.close()
    assert path.exists()


def test_cleanup_errors_keep_files_and_report_failure(queue_env, monkeypatch):
    import app.services.task_staging as service
    manager, _, _, folder, _ = queue_env
    path = staged(folder)
    entry = manager.staging.scan()["entries"][0]
    monkeypatch.setattr(service.os, "rename", lambda *a: (_ for _ in ()).throw(PermissionError("locked file")))
    result = manager.staging.apply([entry["entry_id"]])["results"][0]
    assert result["status"] == "protected" and "locked file" in result["message"]
    assert (path / "video.part").read_bytes() == b"resume"
    monkeypatch.setattr(service, "inventory", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    scan = manager.staging.scan()
    assert not scan["entries"][0]["can_clean"] and "denied" in scan["entries"][0]["message"]


def test_symlink_or_junction_is_never_followed(queue_env):
    import os
    import subprocess
    manager, _, _, folder, _ = queue_env
    outside = folder / "finals"
    outside.mkdir()
    (outside / "keep.mp4").write_bytes(b"final")
    path = staged(folder, data=None)
    link = path / "escape"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    scan = manager.staging.scan()
    assert not scan["entries"][0]["can_clean"] and "链接" in scan["entries"][0]["message"]
    assert (outside / "keep.mp4").read_bytes() == b"final"


def test_empty_success_reclaimed_but_cancelled_partial_kept(queue_env, monkeypatch):
    from app.downloader import DownloadBatchResult, PartDownloadResult, PartDownloadStatus
    manager, create, _, folder, _ = queue_env
    paths = []
    def download(request, controller, progress, log, parts):
        paths.append(staged(folder, controller.task_id, data=None if request.video_title.endswith("0") else b"resume"))
        status = PartDownloadStatus.COMPLETED if request.video_title.endswith("0") else PartDownloadStatus.CANCELLED
        return DownloadBatchResult(PartDownloadResult(p, status) for p in parts)
    monkeypatch.setattr("app.services.task_service.run_download", download)
    create("BV1234567890"); create("BV1234567891")
    manager.start_ready(); until(lambda: not manager.running)
    assert not next(p for p in paths if not (p / "video.part").exists()).exists()
    assert len(list((folder / ".bili-tasks").iterdir())) == 1
    assert manager.staging.scan()["entries"][0]["category"] == "recoverable"


def test_restore_does_not_overwrite_new_staging(queue_env):
    manager, _, _, folder, _ = queue_env
    path = staged(folder)
    entry = manager.staging.scan()["entries"][0]
    manager.staging.apply([entry["entry_id"]])
    recycled = manager.staging.scan()["entries"][0]
    staged(folder, path.name, b"new")
    result = manager.staging.apply([recycled["entry_id"]], restore=True)["results"][0]
    assert result["status"] == "protected" and "未覆盖" in result["message"]
    assert (path / "video.part").read_bytes() == b"new"


def test_invalid_and_expired_preview_rejected(queue_env):
    from app.backend.protocol import ProtocolError
    manager, _, _, folder, _ = queue_env
    staged(folder)
    entry = manager.staging.scan()["entries"][0]
    for ids in [[], "path", [1], [entry["entry_id"]] * 2, ["unknown"], [entry["entry_id"], "unknown"]]:
        with pytest.raises(ProtocolError):
            manager.staging.apply(ids)
    assert (folder / ".bili-tasks" / entry["task_id"]).exists()
    manager.staging.scanned_at -= 301
    with pytest.raises(ProtocolError, match="过期"):
        manager.staging.apply([entry["entry_id"]])


def test_path_changes_and_scan_limit_protect_files(queue_env, monkeypatch):
    from app.services import task_staging
    manager, create, _, folder, _ = queue_env
    task = create()
    path = staged(folder, task["task_id"])
    save_state(manager, task["task_id"], "completed")
    with manager.lock, manager.repo.db:
        value = manager.repo.get(task["task_id"])
        value["spec"]["directory"] = str(folder / "moved")
        manager._save(value)
    entry = manager.staging.scan()["entries"][0]
    assert entry["category"] == "protected" and "路径已变化" in entry["message"]
    staged(folder)
    monkeypatch.setattr(task_staging, "FILE_LIMIT", 1)
    scan = manager.staging.scan()
    assert any("上限" in e["message"] and not e["can_clean"] for e in scan["entries"])
    assert path.exists()


def test_unknown_items_do_not_hide_other_safe_staging(queue_env):
    manager, _, _, folder, _ = queue_env
    unknown = folder / ".bili-tasks" / "user-folder"
    unknown.mkdir(parents=True)
    (unknown / "keep.txt").write_text("keep")
    staged(folder)
    scan = manager.staging.scan()
    assert {e["category"] for e in scan["entries"]} == {"protected", "orphan"}
    orphan = next(e for e in scan["entries"] if e["category"] == "orphan")
    assert manager.staging.apply([orphan["entry_id"]])["results"][0]["status"] == "recycled"
    assert (unknown / "keep.txt").read_text() == "keep"
