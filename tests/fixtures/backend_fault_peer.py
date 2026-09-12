"""Real Backend/serve with one injected worker failure; no network or real profile."""
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.backend import host

scenario = sys.argv[1]
if scenario not in {"worker_construct", "worker_start"}:
    raise ValueError("Unknown worker failure fixture")
injected = False


class FaultThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        global injected
        if scenario == "worker_construct" and kwargs.get("name") == "backend-diagnostics.run" and not injected:
            injected = True
            raise RuntimeError("isolated thread construction failure")
        super().__init__(*args, **kwargs)

    def start(self):
        global injected
        if scenario == "worker_start" and self.name == "backend-diagnostics.run" and not injected:
            injected = True
            raise RuntimeError("can't start new thread")
        return super().start()


with patch.object(host.threading, "Thread", FaultThread), patch.object(
    host, "collect_diagnostics", return_value=SimpleNamespace(items=(), to_redacted_text=lambda: "recovered")
):
    raise SystemExit(host.serve(sys.stdin.buffer, sys.stdout.buffer))
