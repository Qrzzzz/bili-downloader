from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_release_version_is_2_10_and_windows_compatible() -> None:
    app = importlib.import_module("app")
    version_tool = importlib.import_module("tools.write_version_info")

    assert app.__app_name__ == "Bili Downloader Lite"
    assert app.__version__ == "2.10"
    assert version_tool._numeric_version(app.__version__) == (2, 10, 0, 0)
    resource = version_tool._version_resource(
        app.__version__,
        (2, 10, 0, 0),
        "a" * 40,
        False,
        "2026-07-12T00:00:00Z",
    )
    assert "StringStruct('ProductName', 'Bili Downloader Lite')" in resource
    assert "StringStruct('OriginalFilename', 'BiliDownloader.Backend.exe')" in resource
    assert "StringStruct('FileVersion', '2.10')" in resource
    assert "StringStruct('ProductVersion', '2.10')" in resource
    root = Path(__file__).resolve().parents[1]
    props = ET.parse(root / "Directory.Build.props").getroot()
    assert props.findtext(".//Version") == app.__version__
    assert props.findtext(".//AssemblyVersion") == f"{app.__version__}.0.0"
    assert props.findtext(".//FileVersion") == f"{app.__version__}.0.0"
    project = ET.parse(root / "BiliDownloader.WinUI/BiliDownloader.WinUI.csproj").getroot()
    assert project.findtext(".//AssemblyName") == f"BiliDownloader.v{app.__version__}"
    manifest = ET.parse(root / "BiliDownloader.WinUI/app.manifest").getroot()
    identity = manifest.find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity")
    assert identity is not None and identity.attrib["version"] == f"{app.__version__}.0.0"
    assert f'AppVersion = "{app.__version__}"' in (root / "BiliDownloader.WinUI/App.xaml.cs").read_text(encoding="utf-8")
    assert f'Subtitle="{app.__version__}"' in (root / "BiliDownloader.WinUI/MainWindow.xaml").read_text(encoding="utf-8")
    assert f'Text="Bili Downloader Lite {app.__version__}"' in (root / "BiliDownloader.WinUI/Views/SettingsPage.xaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("version", ["1", "1.2.0", "v1.2", "1.2rc1", "1.2.3.4"])
def test_release_version_rejects_non_two_level_forms(version: str) -> None:
    version_tool = importlib.import_module("tools.write_version_info")

    with pytest.raises(ValueError, match="exactly two"):
        version_tool._numeric_version(version)


def test_native_qr_build_has_no_browser_runtime_or_smoke_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    build_script = (root / "build.ps1").read_text(encoding="utf-8")
    spec = (root / "BiliDownloader.Backend.spec").read_text(encoding="utf-8")
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
        "PySide6.QtCore",
        "backend-runtime/Qt6Widgets.dll",
        "shiboken6/Shiboken.pyd",
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


def test_artifact_audit_rejects_missing_xaml_resource_index() -> None:
    audit = importlib.import_module("tools.audit_release_artifact")
    package = {name: None for name in (
        "BiliDownloader.v2.10.exe", "BiliDownloader.v2.10.dll", "Assets/AppIcon.ico",
        "BiliDownloader.Backend.exe", "Microsoft.ui.xaml.dll", "build-info.json",
        "backend-runtime/build-info.json",
    )}
    with pytest.raises(ValueError, match=r"Missing native package files:.*BiliDownloader.v2.10.pri"):
        audit._audit_contents(package, lambda _: b"", Path("unused.exe"), "2.10", None, False)


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

    public_smoke = workflows["public-smoke.yml"]
    assert "schedule:" not in public_smoke
    assert "workflow_dispatch:" in public_smoke
    assert "pull_request:" not in public_smoke
    assert "environment_blocked_412" not in public_smoke
    assert "public_qr_smoke.py" in public_smoke

    release = workflows["release.yml"]
    assert "tags:\n      - v2.10" in release
    assert "RELEASE_TITLE: Bili Downloader Lite v2.10" in release
    assert "BiliDownloader.v2.10.win-x64.zip" in release
    assert 'docs/releases/v2.10.md' in release
    assert "attestations: write" in release
    assert "id-token: write" in release


@pytest.mark.parametrize(
    ("mismatch", "error"),
    [
        (None, None),
        ("digest", "digest mismatch"),
        ("commit", "Tag resolves to"),
        ("assets", "Release assets mismatch"),
    ],
)
def test_published_release_matches_verified_local_assets(
    tmp_path: Path,
    monkeypatch,
    capsys,
    mismatch: str | None,
    error: str | None,
) -> None:
    verifier = importlib.import_module("tools.verify_github_release")
    commit = "a" * 40
    asset_names = ["BiliDownloader.v2.10.win-x64.zip", "BiliDownloader.v2.10.sbom.json", "SHA256SUMS"]
    assets = []
    for name in asset_names:
        data = f"test asset {name}".encode()
        (tmp_path / name).write_bytes(data)
        assets.append({
            "name": name,
            "state": "uploaded",
            "size": len(data),
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
        })
    release = {
        "tag_name": "v2.10",
        "name": "Bili Downloader Lite v2.10",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-04T00:00:00Z",
        "assets": assets,
    }
    if mismatch == "digest":
        assets[0]["digest"] = "sha256:" + "0" * 64
    elif mismatch == "assets":
        assets.pop()
    remote_commit = "b" * 40 if mismatch == "commit" else commit

    def fake_gh_json(*args):
        if args == ("api", "repos/example/project/releases/tags/v2.10"):
            return release
        if args == ("api", "repos/example/project/git/ref/tags/v2.10"):
            return {"object": {"type": "commit", "sha": remote_commit}}
        raise AssertionError(f"Unexpected GitHub request: {args}")

    monkeypatch.setattr(verifier, "_gh_json", fake_gh_json)
    monkeypatch.setattr(sys, "argv", [
        "verify_github_release.py", "--repository", "example/project",
        "--tag", "v2.10", "--expected-version", "2.10",
        "--expected-commit", commit, "--expected-title", "Bili Downloader Lite v2.10",
        "--asset-directory", str(tmp_path),
    ])
    if error is not None:
        with pytest.raises(ValueError, match=error):
            verifier.main()
    else:
        assert verifier.main() == 0
        result = json.loads(capsys.readouterr().out)
        assert result["asset_count"] == 3
        assert result["tag_commit"] == commit


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
