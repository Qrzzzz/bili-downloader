from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from app import diagnostics
from app.config import AppConfig


class FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> object:
        return self.payload


@pytest.mark.parametrize(
    ("tag", "available"),
    [("v1.1", True), ("1.1", True), ("V1.0", False), ("v1.0", False)],
)
def test_manual_update_check_accepts_two_part_versions(tag: str, available: bool) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(_url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse(
            {
                "tag_name": tag,
                "html_url": f"https://github.com/Qrzzzz/bili-downloader/releases/tag/{tag}",
            }
        )

    result = diagnostics.check_latest_release("1.0", get=fake_get)

    assert result.update_available is available
    assert result.latest_version == tag.removeprefix("v").removeprefix("V")
    assert calls[0]["timeout"] == 10


@pytest.mark.parametrize(
    "tag",
    ["1.3.0", "v1.3.0", "1.3.0.0", " 1.3", "1.3 ", "x1.3", "1.3beta"],
)
def test_manual_update_check_rejects_non_two_part_versions(tag: str) -> None:
    result = diagnostics.check_latest_release(
        "1.2",
        get=lambda *_args, **_kwargs: FakeResponse(
            {
                "tag_name": tag,
                "html_url": "https://github.com/Qrzzzz/bili-downloader/releases/tag/invalid",
            }
        ),
    )
    assert result.update_available is False
    assert result.latest_version is None
    assert result.release_url is None


@pytest.mark.parametrize(
    "get",
    [
        lambda *_args, **_kwargs: FakeResponse({}, status=403),
        lambda *_args, **_kwargs: FakeResponse({"tag_name": "latest", "html_url": "invalid"}),
        lambda *_args, **_kwargs: FakeResponse([]),
    ],
)
def test_manual_update_check_returns_safe_failure(get: Any) -> None:
    result = diagnostics.check_latest_release("1.0", get=get)
    assert result.update_available is False
    assert result.release_url is None
    assert result.message.startswith("检查更新失败")


def test_report_redacts_credentials_and_home_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    report = diagnostics.DiagnosticReport(
        (
            diagnostics.DiagnosticItem(
                "合成项目",
                diagnostics.DiagnosticStatus.WARNING,
                f"{tmp_path} SESSDATA=secret-value",
            ),
        )
    )

    text = report.to_redacted_text()

    assert "%USERPROFILE%" in text
    assert "secret-value" not in text
    assert "<redacted>" in text


def test_qr_component_probe_is_local_and_does_not_call_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        diagnostics.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("diagnostics must stay offline"),
    )

    item = diagnostics._qr_login_item(lambda: False)

    assert item.status is diagnostics.DiagnosticStatus.OK
    assert item.name == "扫码登录组件"
    assert "本地生成" in item.summary


def test_collect_diagnostics_does_not_remote_validate_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        diagnostics,
        "probe_ffmpeg",
        lambda: SimpleNamespace(
            status=diagnostics.FFmpegProbeStatus.AVAILABLE,
            path=str(tmp_path / "tools" / "ffmpeg.exe"),
            version="ffmpeg version synthetic",
            detail="",
        ),
    )
    monkeypatch.setattr(
        diagnostics,
        "_qr_login_item",
        lambda _cancelled: diagnostics.DiagnosticItem(
            "扫码登录组件", diagnostics.DiagnosticStatus.OK, "本地二维码可生成"
        ),
    )
    monkeypatch.setattr(diagnostics, "app_data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(
        diagnostics,
        "describe_login_status",
        lambda: SimpleNamespace(code="local_pending", text="本地登录凭据待服务端验证"),
    )

    report = diagnostics.collect_diagnostics(AppConfig(download_dir=str(tmp_path / "downloads")))

    names = {item.name for item in report.items}
    assert {"程序", "Windows", "Python", "yt-dlp", "FFmpeg", "扫码登录组件", "应用数据目录", "下载目录", "登录状态"} <= names
    assert (tmp_path / "downloads").is_dir()
