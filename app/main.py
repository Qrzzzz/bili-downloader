from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crash import acquire_running_lock, install_exception_hooks, log_current_exception, release_running_lock


def main(safe_mode: bool = False) -> int:
    parser = argparse.ArgumentParser(description="Bili Downloader Lite")
    parser.add_argument("--self-test", action="store_true", help="打开主界面后自动退出，用于构建验证")
    parser.add_argument("--parse-test", metavar="URL", help=argparse.SUPPRESS)
    parser.add_argument("--parse-output", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--playwright-smoke-output", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.playwright_smoke_output:
        from app.cookies import ensure_playwright_runtime
        from app.logger import redact_sensitive

        ensure_playwright_runtime()
        result = {"ok": False, "browser": None, "page_url": None, "error": None}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                last_error = None
                for channel in (None, "chrome", "msedge"):
                    browser = None
                    context = None
                    try:
                        kwargs = {"headless": True}
                        if channel:
                            kwargs["channel"] = channel
                        browser = playwright.chromium.launch(**kwargs)
                        context = browser.new_context()
                        page = context.new_page()
                        page.goto("about:blank", wait_until="load", timeout=10000)
                        if page.url != "about:blank":
                            raise RuntimeError(f"Unexpected Playwright page URL: {page.url}")
                        result = {
                            "ok": True,
                            "browser": "playwright-chromium" if channel is None else channel,
                            "page_url": page.url,
                            "error": None,
                        }
                    except Exception as exc:  # noqa: BLE001
                        last_error = redact_sensitive(exc)
                    finally:
                        if context is not None:
                            try:
                                context.close()
                            except Exception as exc:  # noqa: BLE001
                                last_error = f"context.close failed: {redact_sensitive(exc)}"
                                result["ok"] = False
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception as exc:  # noqa: BLE001
                                last_error = f"browser.close failed: {redact_sensitive(exc)}"
                                result["ok"] = False
                    if result["ok"]:
                        break
                else:
                    result["error"] = last_error
        except Exception as exc:  # noqa: BLE001
            result["error"] = redact_sensitive(exc)

        Path(args.playwright_smoke_output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0 if result["ok"] else 1

    if args.parse_test:
        from app.config import AppConfig
        from app.cookies import CredentialMode
        from app.downloader import parse_video_info
        from app.logger import redact_sensitive
        from app.utils import classify_error_details, normalize_bilibili_url

        output = Path(args.parse_output or "parse-test-result.json")
        try:
            info = parse_video_info(
                normalize_bilibili_url(args.parse_test),
                AppConfig(),
                credential_mode=CredentialMode.ANONYMOUS,
            )
            payload = {
                "ok": True,
                "title": info.title,
                "uploader": info.uploader,
                "duration": info.duration,
                "parts": len(info.parts),
                "current_part": info.current_part_index,
                "formats": [choice.label for choice in info.formats],
            }
            exit_code = 0
        except Exception as exc:  # noqa: BLE001 - smoke emits a structured failure
            classified = classify_error_details(exc)
            payload = {
                "ok": False,
                "error_code": classified.code,
                "message": classified.message,
                "detail": redact_sensitive(exc),
            }
            print(f"parse_test_error_code={classified.code}", file=sys.stderr)
            exit_code = 2
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return exit_code

    from app.ui_main import run_app

    return run_app(self_test=args.self_test, safe_mode=safe_mode)


if __name__ == "__main__":
    install_exception_hooks()
    detected_previous_crash = acquire_running_lock()
    try:
        exit_code = main(safe_mode=detected_previous_crash)
    except SystemExit:
        release_running_lock()
        raise
    except Exception:
        log_current_exception("fatal main exception")
        raise
    release_running_lock()
    raise SystemExit(exit_code)
