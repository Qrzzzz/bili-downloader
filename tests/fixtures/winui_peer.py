"""Deterministic IPC peer for native frontend state checks; never used by the app backend."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import __version__

settings = {"download_dir": str(Path.cwd()), "theme": "system", "schema_version": 1}
status = {"code": "none", "text": "无本地登录凭据", "generation": None}
active = None
counter = 0
cancellations = 0


def send(value):
    print(json.dumps({"v": 1, **value}, ensure_ascii=False), flush=True)


def event(name, data):
    active["sequence"] += 1
    send({"type": "event", "operation_id": active["id"], "seq": active["sequence"], "event": name, "data": data})


for line in sys.stdin:
    request = json.loads(line)
    method, params = request["method"], request.get("params", {})
    result, error = {}, None
    if method == "hello":
        result = {"protocol_version": 1, "backend_version": __version__, "session_id": "ui-fixture", "safe_mode": False,
                  "settings": settings, "status": status, "config_diagnostics": []}
    elif method == "settings.get":
        result = settings
    elif method == "settings.update":
        if params.get("download_dir") == "reject-save":
            error = {"code": "fixture_save_failed", "message": "无法保存设置", "retryable": False, "detail": "test fixture"}
        else:
            settings = {**settings, **params}
            result = settings
    elif method in {"parse.start", "download.start", "download.retry", "diagnostics.run", "auth.qr.start", "session.validate", "fixture.malformed_terminal"}:
        counter += 1
        active = {"id": f"ui-{counter}", "sequence": 0, "method": method}
        result = {"operation_id": active["id"]}
    elif method == "operation.cancel":
        cancellations += 1
        result = {"waiting_for_postprocessing": True}
    elif method == "fixture.progress":
        event("download.progress", params)
    elif method == "fixture.complete":
        event("operation.completed", {"method": active["method"], "result": params})
        active = None
    elif method == "fixture.cancel_count":
        result = {"count": cancellations}
    elif method == "shutdown":
        result = {"state": "draining"}
    send({"type": "response", "id": request["id"], "ok": error is None, **({"result": result} if error is None else {"error": error})})
    if method == "fixture.malformed_terminal":
        event("operation.completed", {"method": method})
    if method == "shutdown":
        break
