from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_PUBLIC_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
DIAGNOSTIC_LIMIT = 12_000


def _tail(text: str) -> str:
    if len(text) <= DIAGNOSTIC_LIMIT:
        return text
    return "[truncated]\n" + text[-DIAGNOSTIC_LIMIT:]


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one anonymous public Bilibili parse smoke without retries.")
    parser.add_argument("--url", default=DEFAULT_PUBLIC_URL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "outcome": "failed",
        "url": args.url,
        "returncode": None,
        "parse_result": None,
        "diagnostic": None,
    }

    with tempfile.TemporaryDirectory(prefix="bili-public-parse-") as temporary:
        root = Path(temporary)
        appdata = root / "AppData" / "Roaming"
        localappdata = root / "AppData" / "Local"
        profile = root / "UserProfile"
        parse_output = root / "parse-result.json"
        for path in (appdata, localappdata, profile):
            path.mkdir(parents=True, exist_ok=True)

        environment = os.environ.copy()
        environment.update(
            {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(localappdata),
                "USERPROFILE": str(profile),
                "PYTHONUTF8": "1",
            }
        )
        command = [
            sys.executable,
            "-m",
            "app.main",
            "--parse-test",
            args.url,
            "--parse-output",
            str(parse_output),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            diagnostic = f"Public parse timed out after {args.timeout}s.\n{exc.stdout or ''}\n{exc.stderr or ''}"
            result["diagnostic"] = _tail(diagnostic)
            _write_result(args.output, result)
            return 1

        diagnostic = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        result["returncode"] = completed.returncode
        result["diagnostic"] = _tail(diagnostic)
        parse_payload: dict[str, Any] | None = None
        if parse_output.is_file():
            try:
                candidate = json.loads(parse_output.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    parse_payload = candidate
                    result["parse_result"] = candidate
            except (OSError, ValueError) as exc:
                result["diagnostic"] = _tail(f"Invalid parse result: {exc}\n{diagnostic}")

        if completed.returncode == 0 and parse_payload and parse_payload.get("ok", True):
            result.update(
                ok=True,
                outcome="passed",
                parse_result=parse_payload,
            )
            exit_code = 0
        elif (parse_payload and parse_payload.get("error_code") == "platform_412") or re.search(
            r"(?<!\d)412(?!\d)", diagnostic
        ):
            result.update(
                ok=False,
                outcome="environment_blocked_412",
                diagnostic=_tail("Bilibili returned HTTP 412; recorded as an external environment dependency.\n" + diagnostic),
            )
            exit_code = 0
        else:
            exit_code = 1

    _write_result(args.output, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
