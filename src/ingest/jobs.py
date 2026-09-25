"""A persisted, single-worker job queue for indexing work.

Jobs are JSON files under data/processed/_ingest/jobs/, so the queue and its
history survive a server restart. One background thread takes the oldest
queued job and runs it in a subprocess (`python -m src.ingest.worker`):

  - the subprocess isolates native crashes (PyMuPDF, Tesseract) and the heavy
    embedding work from the API process that serves Chat;
  - it runs with PYTHONUTF8=1 — without it Python writes to a pipe in the
    system code page (cp1252 here) and the pipeline dies on its first
    Vietnamese or arrow character, which is why the old /reindex never ran;
  - one worker means jobs never race each other on the same index files.

Every step a worker performs is idempotent (upserts by stable ids, deletes by
filter, atomic file swaps), so a job interrupted by a restart is simply queued
again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .registry import atomic_write_json, load_sidecar, read_json, save_sidecar

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ("queued", "running")
MAX_ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class JobStore:
    def __init__(self, jobs_dir: Path):
        self.dir = Path(jobs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def log_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.log"

    def create(self, jtype: str, params: dict) -> dict:
        ts = time.time()
        job_id = f"j{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        job = {
            "id": job_id,
            "type": jtype,
            "status": "queued",
            "created_at": now_iso(),
            "created_ts": ts,
            "started_at": None,
            "started_ts": None,
            "finished_at": None,
            "finished_ts": None,
            "attempts": 0,
            "params": params,
            "progress": {},
            "results": {},
            "error": None,
            "log_tail": [],
        }
        with self._lock:
            atomic_write_json(self.path(job_id), job)
        return job

    def get(self, job_id: str) -> dict | None:
        if not job_id or any(c in job_id for c in "\\/:"):
            return None
        data = read_json(self.path(job_id))
        return data if isinstance(data, dict) else None

    def save(self, job: dict) -> None:
        with self._lock:
            atomic_write_json(self.path(job["id"]), job)

    def list(self) -> list[dict]:
        jobs = []
        for p in self.dir.glob("*.json"):
            data = read_json(p)
            if isinstance(data, dict) and "id" in data:
                jobs.append(data)
        jobs.sort(key=lambda j: j.get("created_ts") or 0)
        return jobs

    def active(self) -> list[dict]:
        return [j for j in self.list() if j.get("status") in ACTIVE]

    def next_queued(self) -> dict | None:
        for job in self.list():
            if job.get("status") == "queued":
                return job
        return None

    def latest(self, jtype: str) -> dict | None:
        jobs = [j for j in self.list() if j.get("type") == jtype]
        return jobs[-1] if jobs else None

    def recover(self) -> list[str]:
        """Re-queue jobs a previous server process left running."""
        recovered = []
        for job in self.list():
            if job.get("status") != "running":
                continue
            job["attempts"] = int(job.get("attempts") or 0) + 1
            if job["attempts"] >= MAX_ATTEMPTS:
                job["status"] = "failed"
                job["error"] = {"code": "INTERRUPTED", "message": "Bị ngắt nhiều lần do server khởi động lại."}
                job["finished_at"], job["finished_ts"] = now_iso(), time.time()
            else:
                job["status"] = "queued"
                job["log_tail"] = (job.get("log_tail") or []) + ["[recovered after server restart]"]
            self.save(job)
            recovered.append(job["id"])
        return recovered

    def prune(self, keep: int = 200) -> None:
        finished = [j for j in self.list() if j.get("status") not in ACTIVE]
        for job in finished[:-keep] if keep > 0 else finished:
            for p in (self.path(job["id"]), self.log_path(job["id"])):
                try:
                    p.unlink()
                except OSError:
                    pass


def _tail(path: Path, n: int = 25) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-n:]]
    except OSError:
        return []


class JobRunner:
    """Background thread that runs queued jobs one at a time."""

    def __init__(
        self,
        store: JobStore,
        config_path: str,
        on_finished: Callable[[dict], None] | None = None,
        timeout_s: float = 3600,
        command: Callable[[Path], list[str]] | None = None,
        raw_dir: Path | None = None,
    ):
        self.store = store
        self.config_path = config_path
        self.on_finished = on_finished
        self.timeout_s = timeout_s
        self._command = command or (lambda job_path: [
            sys.executable, "-m", "src.ingest.worker",
            "--config", str(config_path), "--job", str(job_path),
        ])
        self.raw_dir = raw_dir
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="ingest-job-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def enqueue(self, jtype: str, params: dict) -> dict:
        job = self.store.create(jtype, params)
        self._wake.set()
        return job

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_queued()
            if job is None:
                self._wake.wait(timeout=5)
                self._wake.clear()
                continue
            try:
                self._run(job)
            except Exception as exc:          # never let the runner thread die
                job = self.store.get(job["id"]) or job
                job["status"] = "failed"
                job["error"] = {"code": "WORKER_CRASHED", "message": f"{type(exc).__name__}: {exc}"}
                job["finished_at"], job["finished_ts"] = now_iso(), time.time()
                self.store.save(job)
            finally:
                self.current = None

    def _run(self, job: dict) -> None:
        job_id = job["id"]
        self.current = job_id
        job["status"] = "running"
        job["started_at"], job["started_ts"] = now_iso(), time.time()
        self.store.save(job)

        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        log_path = self.store.log_path(job_id)
        timed_out = False
        with open(log_path, "a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                self._command(self.store.path(job_id)),
                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                returncode = proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                returncode = -1
                timed_out = True

        final = self.store.get(job_id) or job
        final["log_tail"] = _tail(log_path)
        if final.get("status") == "running":
            # The worker never recorded an outcome: it crashed or was killed.
            code = "TIMEOUT" if timed_out else "WORKER_CRASHED"
            detail = final["log_tail"][-1] if final["log_tail"] else f"exit code {returncode}"
            message = "Xử lý quá thời gian cho phép." if timed_out else f"Tiến trình xử lý dừng bất thường: {detail}"
            final["status"] = "failed"
            final["error"] = {"code": code, "message": message}
            final["finished_at"], final["finished_ts"] = now_iso(), time.time()
            self._fail_documents(final, code, message)
        self.store.save(final)
        if self.on_finished:
            try:
                self.on_finished(final)
            except Exception:
                pass

    def _fail_documents(self, job: dict, code: str, message: str) -> None:
        """Documents the crashed worker left 'processing' get a final status."""
        if job.get("type") != "ingest" or self.raw_dir is None:
            return
        for item in (job.get("params") or {}).get("items", []):
            src = item.get("source")
            if not src or src in (job.get("results") or {}):
                continue
            group = item.get("group") or ""
            doc_path = (self.raw_dir / group / src) if group else (self.raw_dir / src)
            side = load_sidecar(doc_path)
            if side is not None:
                side["status"] = "failed"
                side["status_detail"] = {"step": "failed", "error_code": code, "message": message}
                try:
                    save_sidecar(doc_path, side)
                except OSError:
                    pass
            job.setdefault("results", {})[src] = {"status": "failed", "error_code": code, "message": message}
