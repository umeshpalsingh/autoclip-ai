"""Run each studio job in a separate process so blocking media work can be stopped."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from config import BASE_DIR, CACHE_DIR
from core.pipeline import ClippingPipeline

TERMINAL = {"completed", "cancelled", "error"}


def write_status(path, status):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(status), encoding="utf-8")
    # Windows can hold the previous file open during a concurrent status poll.
    for attempt in range(10):
        try:
            pending.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.02)


def worker(job_id):
    pipeline = ClippingPipeline(job_id)
    progress = Path(os.environ["AUTOCLIP_JOB_TMP"]) / "progress.json"
    last_key = None
    last_write = 0

    def report(status):
        nonlocal last_key, last_write
        key = (status["stage"], len(status["clips"]))
        now = time.monotonic()
        if key != last_key or now - last_write >= 0.5:
            write_status(progress, status)
            last_key, last_write = key, now

    options = json.loads(sys.stdin.buffer.read())
    result = pipeline.process_video(progress_callback=report, **options)
    report(result)


class StudioJob:
    def __init__(self):
        pipeline = ClippingPipeline()
        self.job_id = pipeline.job_id
        self.directory = pipeline.job_dir
        self.status = pipeline.status
        self.lock = threading.Lock()
        self.process = None
        self.scratch = None
        self.stopping = False

    def _snapshot(self):
        if self.scratch:
            path = Path(self.scratch) / "progress.json"
            if path.is_file():
                self.status = json.loads(path.read_text(encoding="utf-8"))
        return self.status.copy()

    def snapshot(self):
        with self.lock:
            status = self._snapshot()
            if self.stopping and status["stage"] not in TERMINAL:
                status.update(stage="cancelling", message="Stopping clip generation…")
            return status

    def cancel(self):
        with self.lock:
            status = self._snapshot()
            if self.stopping or status["stage"] in TERMINAL:
                return
            proc = self.process
            if proc and proc.poll() is None:
                if os.name == "nt":
                    # The worker owns only this job's FFmpeg and download children.
                    result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode and proc.poll() is None:
                        raise RuntimeError("The worker could not be stopped. Try Cancel again.")
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.stopping = True

    def run(self, options):
        with tempfile.TemporaryDirectory(prefix=f"job-{self.job_id}-", dir=CACHE_DIR) as scratch:
            try:
                with self.lock:
                    self.scratch = scratch
                    if not self.stopping:
                        self.process = subprocess.Popen(
                            [sys.executable, "-m", "core.studio_job", self.job_id],
                            cwd=BASE_DIR, env={**os.environ, "AUTOCLIP_JOB_TMP": scratch},
                            stdin=subprocess.PIPE, start_new_session=os.name != "nt",
                        )
                        self.process.stdin.write(json.dumps(options).encode("utf-8"))
                        self.process.stdin.close()
                if self.process:
                    self.process.wait()
            except Exception as exc:
                self.status.update(stage="error", error=str(exc), message=str(exc))
            finally:
                with self.lock:
                    status = self._snapshot()
                    self.scratch = self.process = None
                    if self.stopping and status["stage"] != "completed":
                        status.update(stage="cancelled", message="Clip generation cancelled.", error=None)
                    elif status["stage"] not in TERMINAL:
                        status.update(stage="error", error="Clip worker stopped unexpectedly.",
                                      message="Clip worker stopped unexpectedly.")
                    if status["stage"] != "completed":
                        status.pop("zip_url", None)
                        keep = set()
                        for clip in status["clips"]:
                            mp4 = Path(clip["filename"])
                            keep.update([mp4.name, mp4.with_suffix(".srt").name, mp4.with_suffix(".ass").name,
                                         mp4.stem + "_thumb.jpg"])
                        for artifact in self.directory.iterdir():
                            if artifact.is_file() and artifact.name not in keep:
                                artifact.unlink(missing_ok=True)
                    self.status = status
                    write_status(self.directory / "job_manifest.json", status)


if __name__ == "__main__":
    worker(sys.argv[1])
