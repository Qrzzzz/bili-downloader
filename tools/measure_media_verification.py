"""Opt-in local decode measurements; cached public media is an explicit input.

No network, credentials or user profile access. Generated samples stay in the
specified directory. Run before/after a policy change and keep the JSON evidence.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import downloader as d


def process_cost(process):
    if os.name != "nt":
        return {}
    from ctypes import wintypes
    handle = wintypes.HANDLE(int(process._handle))
    times = [ctypes.c_ulonglong() for _ in range(4)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))
    class Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    io = Io()
    kernel.GetProcessIoCounters(handle, ctypes.byref(io))
    return {"cpu_seconds": (times[2].value + times[3].value) / 10_000_000,
            "read_bytes": io.read_bytes, "write_bytes": io.write_bytes}


def measure(path, ffmpeg, threads=None):
    processes = []
    original_popen, original_probe = subprocess.Popen, d._run_media_probe
    class Measured(original_popen):
        def wait(self, *a, **kw):
            result = super().wait(*a, **kw)
            if not getattr(self, "cost_recorded", False):
                processes.append(process_cost(self))
                self.cost_recorded = True
            return result
    def probe(command, *a, **kw):
        if threads and "-xerror" in command:
            i = command.index("-i")
            command = command[:i] + ["-threads", str(threads)] + command[i:]
        return original_probe(command, *a, **kw)
    mode = d.DownloadMode.AUDIO_MP3 if path.suffix == ".mp3" else d.DownloadMode.AUDIO_VIDEO
    plan = d.DownloadPlan((), None, mode)
    started = time.monotonic()
    with patch.object(d.subprocess, "Popen", Measured), patch.object(d, "_run_media_probe", probe):
        valid = d._matches_output_spec(path, plan, ffmpeg)
    return {"name": path.name, "bytes": path.stat().st_size, "decoder_threads": threads or "default",
            "valid": valid, "wall_seconds": round(time.monotonic() - started, 3),
            "processes": processes}


def queue_worker(path):
    from app.services import task_resources  # Import before timing admission.
    ffmpeg = shutil.which("ffmpeg")
    controller = d.DownloadController()
    controller.task_id = uuid4().hex
    requested = time.monotonic()
    print("requested", flush=True)
    with d._media_slot(controller):
        acquired = time.monotonic()
        result = measure(path, ffmpeg)
        result["queue_wait_seconds"] = round(acquired - requested, 3)
    print(json.dumps(result), flush=True)
    return 0 if result["valid"] else 1


def measure_queue(output):
    env = {**os.environ, "APPDATA": str(output.resolve() / "profile" / "Roaming"),
           "LOCALAPPDATA": str(output.resolve() / "profile" / "Local")}
    workers = []
    try:
        for name in ("synthetic-2160-300s.mp4", "synthetic-3600s.mp3", "synthetic-1080-600s.mp4"):
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--queue-worker",
                str(output.resolve() / name)], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            workers.append(child)
            if child.stdout.readline().strip() != "requested":
                raise RuntimeError("queue worker did not start")
        results = []
        for child in workers:
            out, err = child.communicate(timeout=60)
            if child.returncode:
                raise RuntimeError(err[:1000])
            results.append(json.loads(out))
        return results
    finally:
        for child in workers:
            if child.poll() is None:
                child.kill()
                child.communicate()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--queue-worker":
        return queue_worker(Path(sys.argv[2]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-media", type=Path, action="append", default=[])
    parser.add_argument("--reuse-samples", action="store_true")
    parser.add_argument("--queue-only", action="store_true")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("FFmpeg required")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.queue_only:
        report_path = args.output / "measurements.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["concurrent_queue"] = measure_queue(args.output)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0
    report = {"os": platform.platform(), "python": platform.python_version(),
              "ffmpeg": subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()[0],
              "fixed_baseline_seconds": d.MEDIA_VERIFY_TIMEOUT, "samples": []}
    def save():
        (args.output / "measurements.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    samples = []
    for height, seconds in [(360, 3600), (1080, 600), (2160, 300)]:
        seed, target = args.output / f"seed-{height}.mp4", args.output / f"synthetic-{height}-{seconds}s.mp4"
        if not args.reuse_samples or not target.exists():
            subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                f"testsrc2=size={height * 16 // 9}x{height}:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "2", "-c:v", "libx264", "-preset", "fast", "-crf", "30", "-c:a", "aac", str(seed)],
                check=True, capture_output=True, timeout=120)
            subprocess.run([ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-i", str(seed),
                "-t", str(seconds), "-c", "copy", "-movflags", "+faststart", str(target)],
                check=True, capture_output=True, timeout=120)
        samples.append((target, seconds, "synthetic_loop"))
    audio = args.output / "synthetic-3600s.mp3"
    if not args.reuse_samples or not audio.exists():
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "3600", "-b:a", "192k", str(audio)], check=True, capture_output=True, timeout=120)
    samples.append((audio, 3600, "synthetic_audio"))
    samples.extend((p, None, "cached_real_download") for p in args.real_media)
    for path, duration, source in samples:
        duration = d._probe_media(path, ffmpeg).duration_seconds
        for threads in (None, 2):
            result = measure(path, ffmpeg, threads)
            result.update(duration_seconds=duration, source=source)
            report["samples"].append(result)
            save()
            print(json.dumps(result), flush=True)
    # Real concurrent decoder processes with the production FIFO lease. Keep
    # all locks and profiles under the output directory, outside user settings.
    report["concurrent_queue"] = measure_queue(args.output)
    save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
