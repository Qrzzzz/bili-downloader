"""Operation creation/start failures must settle the actual backend protocol."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_backend import backend, finish


@pytest.mark.parametrize("failure", ["construct", "start", "worker", None])
def test_worker_lifecycle_is_transactional(backend, monkeypatch, failure):
    from app.backend import host as h

    host, messages, send = backend
    worker_calls = []

    def diagnostics(*args, **kwargs):
        worker_calls.append(True)
        if failure == "worker":
            raise RuntimeError("isolated worker failure")
        return SimpleNamespace(items=(), to_redacted_text=lambda: "local diagnostic")

    monkeypatch.setattr(h, "collect_diagnostics", diagnostics)
    created = []
    original_thread = h.threading.Thread

    def thread(*args, **kwargs):
        if failure == "construct":
            raise RuntimeError("isolated thread construction failure")
        value = original_thread(*args, **kwargs)
        created.append(value)
        if failure == "start":
            def fail_start():
                raise RuntimeError("can't start new thread")
            monkeypatch.setattr(value, "start", fail_start)
        return value

    with monkeypatch.context() as patch:
        patch.setattr(h.threading, "Thread", thread)
        response = send("diagnostics.run", id="first")
        finish(host)

    responses = [m for m in messages if m.get("id") == "first"]
    terminals = [m for m in messages if m.get("event", "").startswith("operation.")]
    assert responses == [response]
    assert host.active is None
    if failure == "construct":
        assert not response["ok"]
        assert not terminals
    else:
        assert response["ok"] and response["result"]["state"] == "accepted"
        assert len(terminals) == 1
        terminal = terminals[0]
        assert messages.index(response) < messages.index(terminal)
        assert terminal["operation_id"] == response["result"]["operation_id"]
        assert terminal["seq"] == 1
        assert terminal["data"]["method"] == "diagnostics.run"
        assert terminal["event"] == ("operation.failed" if failure else "operation.completed")
        if failure:
            error = terminal["data"]["result"]["error"]
            assert isinstance(error["code"], str) and isinstance(error["message"], str)
            assert type(error["retryable"]) is bool and isinstance(error["detail"], str)
        assert send("operation.cancel", {"operation_id": terminal["operation_id"]})["result"]["state"] == "already_finished"
    assert len(worker_calls) == (0 if failure in {"construct", "start"} else 1)
    if failure == "start":
        assert created[0].ident is None
    host.wait()  # No attempt to join the unstarted thread.

    monkeypatch.setattr(h, "collect_diagnostics", lambda *a, **k:
                        SimpleNamespace(items=(), to_redacted_text=lambda: "recovered"))
    recovered = send("diagnostics.run", id="next")
    finish(host)
    assert recovered["ok"]
    assert messages[-1]["event"] == "operation.completed"
    assert messages[-1]["operation_id"] == recovered["result"]["operation_id"]
    assert host.active is None
    assert send("shutdown")["result"]["state"] == "draining"
    host.wait()


def test_shutdown_after_start_failure_never_joins_unstarted_thread(backend, monkeypatch):
    from app.backend import host as h

    host, messages, send = backend
    def fail_start(_):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(h.threading.Thread, "start", fail_start)
    send("diagnostics.run")
    assert send("shutdown")["result"]["state"] == "draining"
    host.wait()
    assert host.active is None
    assert len([m for m in messages if m.get("event") == "operation.failed"]) == 1
