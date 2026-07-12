from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Playwright and load about:blank without network access.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "browser": None,
        "page_url": None,
        "error": None,
    }
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="bili-playwright-smoke-") as temporary:
        temporary_root = Path(temporary)
        appdata = temporary_root / "AppData" / "Roaming"
        localappdata = temporary_root / "AppData" / "Local"
        profile = temporary_root / "UserProfile"
        for path in (appdata, localappdata, profile):
            path.mkdir(parents=True, exist_ok=True)
        os.environ["APPDATA"] = str(appdata)
        os.environ["LOCALAPPDATA"] = str(localappdata)
        os.environ["USERPROFILE"] = str(profile)

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                for channel in ("msedge", "chrome", None):
                    label = "playwright-chromium" if channel is None else channel
                    browser = None
                    context = None
                    page_url: str | None = None
                    attempt_errors: list[str] = []
                    try:
                        launch_options: dict[str, Any] = {"headless": True}
                        if channel is not None:
                            launch_options["channel"] = channel
                        browser = playwright.chromium.launch(**launch_options)
                        context = browser.new_context()
                        page = context.new_page()
                        page.goto("about:blank", wait_until="load", timeout=10_000)
                        page_url = page.url
                        if page_url != "about:blank":
                            raise RuntimeError(f"Unexpected page URL: {page_url}")
                    except Exception as exc:  # noqa: BLE001 - smoke must report every launch failure
                        attempt_errors.append(str(exc))
                    finally:
                        if context is not None:
                            try:
                                context.close()
                            except Exception as exc:  # noqa: BLE001
                                attempt_errors.append(f"context.close failed: {exc}")
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception as exc:  # noqa: BLE001
                                attempt_errors.append(f"browser.close failed: {exc}")

                    if not attempt_errors:
                        result.update(ok=True, browser=label, page_url=page_url, error=None)
                        break
                    errors.append(f"{label}: {'; '.join(attempt_errors)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Playwright initialization failed: {exc}")

    if not result["ok"]:
        result["error"] = "\n".join(errors)
    _write_result(args.output, result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
