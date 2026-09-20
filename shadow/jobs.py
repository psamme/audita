"""Background jobs for work that is too slow to hold an HTTP request.

Induction is one to four model calls and takes minutes; a full reconciliation run is dozens.
Neither `playbook.induce` nor `pipeline.run` exposes a progress hook, so a job reports coarse
named phases and elapsed time, never a fabricated percentage.

Two workers, because `pipeline.run` already spawns its own pool of eight and the Starlette
threadpool that serves requests is only forty. One lock per client, because `playbook.save`
derives the next version number from a directory listing: two writers at once lose a version.
"""
import json
import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from shadow import db

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
_CLIENT_LOCKS: dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()
_LIVE: dict[str, dict] = {}
_HANDLES: dict[str, "Job"] = {}


def client_lock(client: str) -> threading.Lock:
    """One writer at a time per client. Anything that saves a playbook version must hold it."""
    with _REGISTRY_LOCK:
        return _CLIENT_LOCKS.setdefault(client, threading.Lock())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_dir(client: str) -> Path:
    """Jobs live beside the client, not under runs/, so server.runs() never globs them."""
    d = db.DATA / client / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(rec: dict) -> None:
    path = job_dir(rec["client"]) / f"{rec['job_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, default=str))
    os.replace(tmp, path)


class Job:
    """Handle passed to the worker function so it can report where it has got to."""

    def __init__(self, rec: dict):
        self.rec = rec
        self._cancel = threading.Event()

    def phase(self, name: str) -> None:
        self.rec["phase"] = name
        self.rec["log"].append({"at": now(), "text": name})
        _write(self.rec)

    def log(self, text: str) -> None:
        self.rec["log"].append({"at": now(), "text": text})
        _write(self.rec)

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()


def submit(kind: str, client: str, fn, meta: dict | None = None, estimate_s: int | None = None,
           exclusive: bool = True) -> dict:
    """Start `fn(job)` on the pool. Returns the job record immediately.

    exclusive: refuse when a job of the same kind is already live for this client.
    """
    if exclusive:
        for rec in list(_LIVE.values()):
            if rec["client"] == client and rec["kind"] == kind and rec["state"] in ("queued", "running"):
                raise ValueError(f"a {kind} job is already running for {client}")
    rec = {"job_id": f"{kind}_{uuid.uuid4().hex[:10]}", "kind": kind, "client": client,
           "state": "queued", "phase": "queued", "meta": meta or {}, "started_at": now(),
           "finished_at": None, "estimate_s": estimate_s, "result": None, "error": None, "log": []}
    job = Job(rec)
    _LIVE[rec["job_id"]] = rec
    _write(rec)

    def runner():
        rec["state"], rec["phase"] = "running", "starting"
        _write(rec)
        try:
            rec["result"] = fn(job)
            rec["state"], rec["phase"] = "done", "done"
        except Exception as e:                     # a failed job must still be reportable
            rec["state"], rec["phase"] = "error", "error"
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["log"].append({"at": now(), "text": traceback.format_exc()[-2000:]})
        finally:
            rec["finished_at"] = now()
            _write(rec)

    _HANDLES[rec["job_id"]] = job
    POOL.submit(runner)
    return rec


def get(job_id: str) -> dict | None:
    if job_id in _LIVE:
        return _LIVE[job_id]
    for d in (db.DATA).glob("*/jobs"):             # server may have restarted
        path = d / f"{job_id}.json"
        if path.exists():
            rec = json.loads(path.read_text())
            if rec["state"] in ("queued", "running"):
                rec["state"], rec["phase"] = "lost", "lost"   # that thread died with the process
            return rec
    return None


def recent(client: str | None = None, kind: str | None = None, limit: int = 20) -> list[dict]:
    out = []
    roots = [db.DATA / client / "jobs"] if client else list(db.DATA.glob("*/jobs"))
    for d in roots:
        if not d.exists():
            continue
        for path in d.glob("*.json"):
            try:
                rec = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if kind and rec.get("kind") != kind:
                continue
            out.append(_LIVE.get(rec["job_id"], rec))
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out[:limit]


def cancel(job_id: str) -> bool:
    """Cooperative only. An in-flight model call cannot be interrupted."""
    job = _HANDLES.get(job_id)
    if not job:
        return False
    job.cancel()
    job.log("cancellation requested")
    return True
