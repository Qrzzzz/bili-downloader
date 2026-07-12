from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def test_release_version_is_1_0_and_windows_compatible() -> None:
    app = importlib.import_module("app")
    version_tool = importlib.import_module("tools.write_version_info")

    assert app.__app_name__ == "Bili Downloader Lite"
    assert app.__version__ == "1.0"
    assert version_tool._numeric_version(app.__version__) == (1, 0, 0, 0)
    resource = version_tool._version_resource(
        app.__version__,
        (1, 0, 0, 0),
        "a" * 40,
        False,
        "2026-07-12T00:00:00Z",
    )
    assert "StringStruct('ProductName', 'Bili Downloader Lite')" in resource
    assert "StringStruct('OriginalFilename', 'BiliDownloader.v1.0.exe')" in resource


def test_parse_smoke_writes_structured_412_without_traceback(
    isolated_paths: object,
    monkeypatch,
    capsys,
) -> None:
    main_module = importlib.import_module("app.main")
    downloader = importlib.import_module("app.downloader")
    utils = importlib.import_module("app.utils")
    output = isolated_paths.root / "parse-result.json"  # type: ignore[attr-defined]

    def blocked(*_args, **_kwargs):
        raise utils.AppError(utils.ErrorKind.PLATFORM_412, "request rejected without numeric status text")

    monkeypatch.setattr(downloader, "parse_video_info", blocked)
    monkeypatch.setattr(
        sys,
        "argv",
        ["app.main", "--parse-test", "BV1GJ411x7h7", "--parse-output", str(output)],
    )

    assert main_module.main() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert payload["ok"] is False
    assert payload["error_code"] == "platform_412"
    assert "parse_test_error_code=platform_412" in captured.err
    assert "Traceback" not in captured.err


def test_public_parse_smoke_accepts_structured_412_environment_block(
    isolated_paths: object,
    monkeypatch,
) -> None:
    smoke = importlib.import_module("tools.public_parse_smoke")
    output = isolated_paths.root / "public-smoke.json"  # type: ignore[attr-defined]

    def fake_run(command, **_kwargs):
        parse_output = Path(command[command.index("--parse-output") + 1])
        parse_output.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "platform_412",
                    "message": "synthetic environment block",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=2, stdout="", stderr="parse_test_error_code=platform_412")

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["public_parse_smoke.py", "--output", str(output)])

    assert smoke.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["outcome"] == "environment_blocked_412"
    assert payload["parse_result"]["error_code"] == "platform_412"
