"""Opt-in anonymous production queue acceptance, with an isolated profile.

This exercises real public sources and never disables TLS verification. Local
artifacts are kept under build; the JSON report contains no profile/output paths.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import ssl
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def worker(report: Path, work: Path) -> int:
    sys.path.insert(0, str(ROOT))
    from app import __version__
    from app.config import AppConfig
    from app.cookies import CredentialMode
    from app.services.parse_service import parse_video
    from app.services.task_service import TaskManager
    from app.utils import probe_ffmpeg
    import certifi

    result = {"version": __version__, "python": platform.python_version(), "os": platform.system(),
        "tls": ssl.OPENSSL_VERSION, "tls_verification": True, "credential_mode": "anonymous",
        "certifi": certifi.__version__, "certifi_tls": "not_executed", "system_tls": "not_executed",
        "max_active": 0, "tasks": [], "outcome": "running",
        "not_executed": ["multi_part", "cancel_resume", "process_restart_resume", "failure_retry", "phone_qr",
            "file_association", "clipboard", "screen_reader", "high_contrast"]}
    def save():
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save()
    # Compare bundled CA trust with the OS trust store on a public metadata host.
    # No proxy configuration or local trust paths are written to the report.
    for key, context in [("certifi_tls", ssl.create_default_context(cafile=certifi.where())),
                         ("system_tls", ssl.create_default_context())]:
        import socket
        try:
            with socket.create_connection(("api.bilibili.com", 443), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname="api.bilibili.com"):
                    result[key] = "passed"
        except Exception as exc:
            result[key] = type(exc).__name__
    ffmpeg = probe_ffmpeg()
    result["ffmpeg"] = ffmpeg.status.value
    save()
    if not ffmpeg.available:
        result.update(outcome="blocked_ffmpeg")
        save()
        return 1
    config = AppConfig(download_dir=str(work / "media"))
    manager = TaskManager(lambda _: None, config, "public-v34", work / "tasks.sqlite3")
    try:
        for index, (bvid, mode) in enumerate([("BV1GJ411x7h7", "audio_video"), ("BV1kkbC6eEgm", "audio_mp3")]):
            entry = {"source": bvid, "mode": mode, "state": "parsing"}
            result["tasks"].append(entry)
            try:
                info = parse_video("https://www.bilibili.com/video/" + bvid, config, CredentialMode.ANONYMOUS, lambda _: None)
                if not info.duration or info.duration > 300:
                    entry["state"] = "blocked_duration"
                    continue
                parsed = SimpleNamespace(info=info, mode=CredentialMode.ANONYMOUS, generation=None)
                choice = next((str(i) for i, f in enumerate(info.formats) if f.height == 360), "0")
                task = manager.create(parsed, [info.current_part_index], mode, choice if mode == "audio_video" else None,
                    config.download_dir, f"public-{index}")["task"]
                entry.update(task_id=task["task_id"], state="queued")
            except Exception as exc:
                # Error type/code is enough to record platform/network failures;
                # raw messages may contain proxy URLs or machine paths.
                entry.update(state="parse_failed", error_type=type(exc).__name__, error_code=getattr(exc, "code", ""))
            save()
        manager.start_ready()
        while manager.running:
            result["max_active"] = max(result["max_active"], len(manager.running))
            time.sleep(0.1)
        for entry in result["tasks"]:
            if "task_id" not in entry:
                continue
            task = manager.repo.get(entry["task_id"])
            entry.update(state=task["state"], outputs=[], errors=[])
            for part in (task["result"] or {}).get("part_results", []):
                if part.get("error"):
                    error = part["error"]
                    detail = error.get("detail", "")
                    entry["errors"].append({"code": error["code"], "tls_certificate_error":
                        "CERTIFICATE_VERIFY_FAILED" in detail or "certificate verify failed" in detail.lower()})
                for name in part["saved_files"]:
                    probe = subprocess.run([str(Path(ffmpeg.path).with_name("ffprobe.exe")), "-v", "error",
                        "-show_entries", "format=format_name,duration:stream=codec_type,height,bit_rate", "-of", "json", name],
                        capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    entry["outputs"].append({"name": Path(name).name, "bytes": Path(name).stat().st_size,
                        "ffprobe_exit": probe.returncode, "probe": json.loads(probe.stdout) if probe.returncode == 0 else None})
        result["outcome"] = "passed" if all(t["state"] == "completed" and t.get("outputs") for t in result["tasks"]) else "failed"
        save()
        # Retry failed parts once; retain the first real outcome above.
        retried = set()
        for entry in result["tasks"]:
            if entry["state"] in {"partial", "failed"} and "task_id" in entry:
                manager.command("retry", entry["task_id"])
                retried.add(entry["task_id"])
        if retried:
            result["not_executed"].remove("failure_retry")
        manager.start_ready()
        while manager.running:
            time.sleep(0.1)
        for entry in result["tasks"]:
            if "task_id" in entry:
                task = manager.repo.get(entry["task_id"])
                entry.update(retry_state=task["state"] if entry["task_id"] in retried else "not_needed",
                    retained_attempts=len(task.get("attempts", [])))
        save()
        return 0 if result["outcome"] == "passed" else 1
    finally:
        manager.close()
        manager.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = args.output.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    if args.worker:
        try:
            return worker(report, args.worker)
        except Exception as exc:
            result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
            result.update(outcome="worker_failed", error_type=type(exc).__name__)
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1
    work = report.parent / (report.stem + "-isolated")
    # Do not reuse an earlier profile/database.
    work.mkdir(exist_ok=False)
    env = os.environ.copy()
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        directory = work / name
        directory.mkdir()
        env[name] = str(directory)
    env["PYTHONUTF8"] = "1"
    with (work / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--output", str(report),
            "--worker", str(work)], env=env, stdout=log, stderr=log)
        try:
            return process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=log, stderr=log)
            else:
                process.kill()
            process.wait()
            result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
            result["outcome"] = "timeout"
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
