"""Opt-in real-source cancellation and process-restart acceptance.

Each case uses a fresh profile, production TaskManager and strict TLS. No
credentials or raw task/log objects are exported. A restart case terminates
its own worker at a real download progress notification, before FFmpeg starts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import ssl
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def inspect_task(task: dict, ffmpeg: str) -> dict:
    result = task.get("result") or {}
    parts = result.get("part_results", [])
    files = result.get("saved_files", [])
    outputs = []
    for name in files:
        path = Path(name)
        probe = subprocess.run([str(Path(ffmpeg).with_name("ffprobe.exe")), "-v", "error",
            "-show_entries", "format=format_name,duration:stream=codec_type,height,bit_rate", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        decode = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:v?", "-map", "0:a?", "-f", "null", "-"],
            capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        outputs.append({"name": path.name, "bytes": path.stat().st_size,
            "sha256": digest,
            "ffprobe_exit": probe.returncode, "full_decode_exit": decode.returncode,
            "probe": json.loads(probe.stdout) if probe.returncode == 0 else None})
    return {"state": task["state"], "task_id": task["task_id"], "attempts": [
        {"attempt_id": a["attempt_id"], "state": a["state"]} for a in task.get("attempts", [])],
        "parts": [{"index": p["index"], "status": p["status"], "file_count": len(p["saved_files"]),
            "error_code": (p.get("error") or {}).get("code")} for p in parts],
        "saved_files_consistent": set(files) == {f for p in parts for f in p["saved_files"]},
        "outputs": outputs}


def accepted(task: dict, indices: list[int], mode: str, height: int | None = 360) -> bool:
    if task["state"] != "completed" or not task["saved_files_consistent"]:
        return False
    if sorted(p["index"] for p in task["parts"]) != sorted(indices):
        return False
    if any(p["status"] != "completed" or p["file_count"] != 1 for p in task["parts"]):
        return False
    for output in task["outputs"]:
        if output["ffprobe_exit"] or output["full_decode_exit"] or not output["bytes"]:
            return False
        streams = output["probe"]["streams"]
        audio = any(s["codec_type"] == "audio" for s in streams)
        if not audio:
            return False
        if mode == "audio_video":
            if "mp4" not in output["probe"]["format"]["format_name"].split(","):
                return False
            if not any(s["codec_type"] == "video" and (s.get("height") == height if height else
                    type(s.get("height")) is int and s["height"] > 0) for s in streams):
                return False
        if mode == "audio_mp3" and (output["probe"]["format"]["format_name"] != "mp3" or
                not any(s.get("bit_rate") == "192000" for s in streams)):
            return False
    return len(task["outputs"]) == len(indices)


def worker(args) -> int:
    sys.path.insert(0, str(ROOT))
    from app.config import AppConfig
    from app.cookies import CredentialMode
    from app.services.parse_service import parse_video
    from app.services.task_service import TaskManager
    from app.utils import require_ffmpeg

    work, report = args.work, args.output
    config = AppConfig(download_dir=str(work / "media"))
    milestone = threading.Event()
    record = {"case": args.case, "mode": args.mode, "source": args.source,
        "indices": args.parts, "height": args.height or None, "outcome": "running"}
    manager = None

    def notify(event):
        task = event["task"]
        progress = task.get("progress") or {}
        if args.phase != "initial" or milestone.is_set() or progress.get("phase") != "downloading":
            return
        if (progress.get("downloaded_bytes") or 0) < 1024:
            return
        if progress.get("part_index") != max(args.parts):
            return
        milestone.set()
        published = []
        for path in (work / "media").glob("*"):
            if path.is_file() and path.suffix in {".mp4", ".mp3"}:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                published.append({"name": path.name, "sha256": digest})
        record.update(trigger={"phase": "downloading", "part_index": progress["part_index"],
            "downloaded_bytes": progress["downloaded_bytes"]}, published_before_control=published,
            task_id=task["task_id"])
        write(report, record)
        if args.case == "restart":
            # Parent sees the committed progress milestone and kills only this
            # isolated Python process. This barrier prevents a fast download
            # from finishing before the crash is actually exercised.
            while True:
                time.sleep(0.05)
        manager.command("cancel", task["task_id"])

    manager = TaskManager(notify, config, "acceptance-" + uuid.uuid4().hex, work / "tasks.sqlite3")
    try:
        if args.phase == "initial":
            info = parse_video("https://www.bilibili.com/video/" + args.source, config,
                CredentialMode.ANONYMOUS, lambda _: None)
            selected = [p for p in info.parts if p.index in args.parts]
            if len(selected) != len(args.parts) or any(not p.duration or p.duration > 600 for p in selected):
                record.update(outcome="blocked_parts_or_duration")
                write(report, record)
                return 1
            choice = next((str(i) for i, f in enumerate(info.formats) if f.height == (args.height or None)), None)
            task = manager.create(SimpleNamespace(info=info, mode=CredentialMode.ANONYMOUS, generation=None),
                args.parts, args.mode, choice if args.mode == "audio_video" else None,
                config.download_dir, uuid.uuid4().hex)["task"]
            task_id = task["task_id"]
        else:
            record = json.loads(report.read_text(encoding="utf-8"))
            task_id = record["task_id"]
            before = manager.repo.get(task_id)
            record["recovered_state"] = before["state"]
            record["automatic_restart"] = bool(manager.running)
            if before["state"] != "interrupted" or manager.running:
                record.update(outcome="failed_recovery")
                write(report, record)
                return 1
            manager.command("resume", task_id)
        manager.start_ready()
        while manager.running:
            time.sleep(0.02)
        ffmpeg = require_ffmpeg()
        if args.phase == "initial":
            initial = manager.repo.get(task_id)
            record["before_resume"] = inspect_task(initial, ffmpeg)
            if not milestone.is_set() or initial["state"] != "cancelled":
                record.update(outcome="failed_cancel")
                write(report, record)
                return 1
            manager.command("resume", task_id)
            manager.start_ready()
            while manager.running:
                time.sleep(0.02)
        record["final"] = inspect_task(manager.repo.get(task_id), ffmpeg)
        final_digests = {o["name"]: o["sha256"] for o in record["final"]["outputs"]}
        record["retained_outputs_unchanged"] = all(final_digests.get(o["name"]) == o["sha256"]
            for o in record.get("published_before_control", []))
        record["outcome"] = "passed" if accepted(record["final"], args.parts, args.mode, args.height or None) and (
            record["retained_outputs_unchanged"]) else "failed_media"
        write(report, record)
        return 0 if record["outcome"] == "passed" else 1
    finally:
        manager.close()
        manager.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", default="BV1GJ411x7h7")
    parser.add_argument("--parts", type=int, nargs="+", default=[1])
    parser.add_argument("--height", type=int, default=360, help="Frozen MP4 height; 0 selects best-per-part.")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--work", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", choices=["cancel", "restart"], help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=["audio_video", "audio_mp3"], help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=["initial", "resume"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.work:
        try:
            return worker(args)
        except Exception as exc:
            record = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
            record.update(outcome="worker_failed", error_type=type(exc).__name__)
            write(args.output, record)
            return 1
    if len(args.parts) > 2 or len(set(args.parts)) != len(args.parts) or any(p < 1 for p in args.parts):
        parser.error("Select one or two distinct positive part indices.")
    work = args.output.parent / (args.output.stem + "-isolated")
    work.mkdir(exist_ok=False)
    sys.path.insert(0, str(ROOT))
    from app import __version__
    summary = {"version": __version__, "python": platform.python_version(), "os": platform.system(),
        "tls": ssl.OPENSSL_VERSION, "tls_verification": True, "credential_mode": "anonymous", "expected_cases": 4, "cases": []}
    for case in ["cancel", "restart"]:
        for mode in ["audio_video", "audio_mp3"]:
            folder = work / (case + "-" + mode)
            folder.mkdir()
            env = os.environ.copy()
            for name in ["APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"]:
                target = folder / name
                target.mkdir()
                env[name] = str(target)
            env["PYTHONUTF8"] = "1"
            report = folder / "report.json"
            command = [sys.executable, str(Path(__file__).resolve()), "--output", str(report),
                "--work", str(folder), "--case", case, "--mode", mode, "--source", args.source,
                "--height", str(args.height), "--parts", *map(str, args.parts)]
            with (folder / "worker.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command + ["--phase", "initial"], env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + args.timeout
                    terminated = False
                    while process.poll() is None:
                        if case == "restart" and report.exists() and json.loads(report.read_text(encoding="utf-8")).get("trigger"):
                            process.kill()
                            process.wait(timeout=10)
                            terminated = True
                            break
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(command, args.timeout)
                        time.sleep(0.02)
                    if terminated:
                        process = subprocess.Popen(command + ["--phase", "resume"], env=env, stdout=log, stderr=log)
                        process.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    # Reap all descendants of this worker on timeout.
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=log, stderr=log)
                    else:
                        process.kill()
                    process.wait()
                    write(report, {"case": case, "mode": mode, "outcome": "timeout"})
            record = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"outcome": "no_report"}
            record["worker_exit"] = process.returncode
            summary["cases"].append(record)
            summary["outcome"] = "running" if len(summary["cases"]) < summary["expected_cases"] else (
                "passed" if all(c["outcome"] == "passed" and c["worker_exit"] == 0 for c in summary["cases"]) else "failed")
            write(args.output, summary)
    return 0 if summary["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
