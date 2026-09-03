from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_release_version_is_2_3_and_windows_compatible() -> None:
    app = importlib.import_module("app")
    version_tool = importlib.import_module("tools.write_version_info")

    assert app.__app_name__ == "Bili Downloader Lite"
    assert app.__version__ == "2.3"
    assert version_tool._numeric_version(app.__version__) == (2, 3, 0, 0)
    resource = version_tool._version_resource(
        app.__version__,
        (2, 3, 0, 0),
        "a" * 40,
        False,
        "2026-07-12T00:00:00Z",
    )
    assert "StringStruct('ProductName', 'Bili Downloader Lite')" in resource
    assert "StringStruct('OriginalFilename', 'BiliDownloader.v2.3.exe')" in resource
    assert "StringStruct('FileVersion', '2.3')" in resource
    assert "StringStruct('ProductVersion', '2.3')" in resource


@pytest.mark.parametrize("version", ["1", "1.2.0", "v1.2", "1.2rc1", "1.2.3.4"])
def test_release_version_rejects_non_two_level_forms(version: str) -> None:
    version_tool = importlib.import_module("tools.write_version_info")

    with pytest.raises(ValueError, match="exactly two"):
        version_tool._numeric_version(version)


def test_native_qr_build_has_no_browser_runtime_or_smoke_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    build_script = (root / "build.ps1").read_text(encoding="utf-8")
    spec = (root / "BiliDownloader.spec").read_text(encoding="utf-8")
    runtime_input = (root / "requirements.in").read_text(encoding="utf-8").lower()
    runtime_lock = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    main = (root / "app" / "main.py").read_text(encoding="utf-8").lower()
    package_smoke = (root / "tools" / "package_smoke.ps1").read_text(encoding="utf-8").lower()

    assert "playwright" not in spec.lower()
    assert "playwright" not in runtime_input
    assert "playwright" not in runtime_lock
    assert "greenlet==" not in runtime_lock
    assert "pyee==" not in runtime_lock
    assert "--playwright-smoke-output" not in main
    assert "playwright" not in package_smoke
    assert not (root / "tools" / "playwright_smoke.py").exists()
    assert "BILI_BROWSER_ROOT" not in spec
    assert "ffmpeg_file" not in spec
    assert "System32" in build_script
    assert "PYINSTALLER_CONFIG_DIR" in build_script


@pytest.mark.parametrize(
    "member",
    [
        "ms-playwright\\chromium-123\\chrome.exe",
        "playwright._impl._connection",
        "playwright\\driver\\node.exe",
        "electron.exe",
        "resources\\electron.asar",
        "tools\\ffmpeg.exe",
        "profile\\storage_state.json",
        "logs\\app.log",
        "icuuc.dll",
    ],
)
def test_artifact_audit_rejects_prohibited_members(member: str) -> None:
    audit = importlib.import_module("tools.audit_release_artifact")

    assert any(pattern.search(member) for pattern in audit.BANNED_MEMBER_PATTERNS)


def test_dependency_locks_are_pinned_and_hashed_for_approved_target() -> None:
    lock_tool = importlib.import_module("tools.verify_dependency_lock")
    root = Path(__file__).resolve().parents[1]

    lock_tool.verify_pair(root / "requirements.in", root / "requirements.txt")
    lock_tool.verify_pair(root / "requirements-dev.in", root / "requirements-dev.txt")
    lock_tool.verify_pair(root / "requirements-sbom.in", root / "requirements-sbom.txt")


def test_workflows_pin_actions_and_keep_public_network_out_of_quality() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow_dir = root / ".github" / "workflows"
    workflows = {path.name: path.read_text(encoding="utf-8") for path in workflow_dir.glob("*.yml")}

    for name, content in workflows.items():
        references = re.findall(r"^\s*uses:\s*[^@\s]+@([^\s#]+)", content, re.MULTILINE)
        assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in references), name

    quality = workflows["quality.yml"]
    assert "public_parse_smoke.py" not in quality
    assert "public_qr_smoke.py" not in quality
    assert "playwright" not in quality.lower()
    assert "BiliDownloader.v2.3.exe" in quality
    assert "--expected-version 2.3" in quality

    public_smoke = workflows["public-smoke.yml"]
    assert "schedule:" in public_smoke
    assert "workflow_dispatch:" in public_smoke
    assert "pull_request:" not in public_smoke
    assert "environment_blocked_412" not in public_smoke
    assert "public_qr_smoke.py" in public_smoke

    release = workflows["release.yml"]
    assert "tags:\n      - v2.3" in release
    assert "RELEASE_TITLE: Bili Downloader Lite v2.3" in release
    assert "BiliDownloader.v2.3.exe" in release
    assert 'docs/releases/v2.3.md' in release
    assert "attestations: write" in release
    assert "id-token: write" in release


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


def test_public_parse_smoke_fails_structured_412_environment_block(
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

    assert smoke.main() == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["outcome"] == "environment_blocked_412"
    assert payload["parse_result"]["error_code"] == "platform_412"


def test_public_qr_smoke_records_waiting_without_protocol_secrets(
    isolated_paths: object,
    monkeypatch,
) -> None:
    smoke = importlib.reload(importlib.import_module("tools.public_qr_smoke"))
    auth = importlib.import_module("app.auth_qr")
    output = isolated_paths.root / "public-qr-smoke.json"  # type: ignore[attr-defined]

    class FakeClient:
        def generate(self):
            return SimpleNamespace(qr_url="secret-url", qrcode_key="secret-key")

        def poll(self, _challenge):
            return SimpleNamespace(status=auth.QrStatus.WAITING_SCAN)

        def close(self):
            pass

    monkeypatch.setattr(smoke, "QrLoginClient", FakeClient)
    monkeypatch.setattr(sys, "argv", ["public_qr_smoke.py", "--output", str(output)])

    assert smoke.main() == 0
    raw = output.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["outcome"] == "passed"
    assert payload["first_status"] == "waiting_scan"
    assert "secret-url" not in raw and "secret-key" not in raw


def test_public_qr_smoke_fails_and_records_platform_412(
    isolated_paths: object,
    monkeypatch,
) -> None:
    smoke = importlib.reload(importlib.import_module("tools.public_qr_smoke"))
    auth = importlib.import_module("app.auth_qr")
    output = isolated_paths.root / "public-qr-412.json"  # type: ignore[attr-defined]

    class FakeClient:
        def generate(self):
            raise auth.QrNetworkError("synthetic platform block", code="platform_412")

        def close(self):
            pass

    monkeypatch.setattr(smoke, "QrLoginClient", FakeClient)
    monkeypatch.setattr(sys, "argv", ["public_qr_smoke.py", "--output", str(output)])

    assert smoke.main() == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["outcome"] == "environment_blocked_412"
