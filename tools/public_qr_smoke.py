from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth_qr import QrLoginClient, QrLoginError, QrStatus


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate one anonymous Bilibili QR challenge and verify its first waiting status."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    # This evidence intentionally contains no QR URL, key, callback, cookie,
    # token, response body, or request URL.
    result: dict[str, Any] = {
        "ok": False,
        "outcome": "failed",
        "generated": False,
        "first_status": None,
        "diagnostic": None,
    }
    client: QrLoginClient | None = None
    try:
        client = QrLoginClient()
        challenge = client.generate()
        result["generated"] = True
        status = client.poll(challenge).status
        result["first_status"] = status.value
        if status is not QrStatus.WAITING_SCAN:
            result.update(outcome="protocol_mismatch", diagnostic="First poll was not waiting_scan.")
            exit_code = 1
        else:
            result.update(ok=True, outcome="passed")
            exit_code = 0
    except QrLoginError as exc:
        outcome = "environment_blocked_412" if exc.code == "platform_412" else exc.code
        result.update(outcome=outcome, diagnostic=str(exc))
        exit_code = 1
    except Exception as exc:  # noqa: BLE001 - smoke writes bounded typed evidence
        result.update(outcome="unexpected_failure", diagnostic=type(exc).__name__)
        exit_code = 1
    finally:
        if client is not None:
            client.close()

    _write_result(args.output, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
