from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.backend.host import serve
from app.crash import acquire_running_lock, install_exception_hooks, release_running_lock
from app.logger import setup_logging


def main() -> int:
    install_exception_hooks()
    setup_logging()
    safe_mode = acquire_running_lock()
    # Keep protocol stdout separate even if a third-party library uses print().
    protocol_output = sys.stdout.buffer
    sys.stdout = sys.stderr
    try:
        return serve(sys.stdin.buffer, protocol_output, safe_mode)
    finally:
        release_running_lock()


if __name__ == "__main__":
    raise SystemExit(main())
