"""JSON API for the UI. Contract: docs/API.md.

    uv run uvicorn shadow.server:app --port 8787
"""
import json

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shadow import correct, db, experiment, pipeline, playbook as pbmod

app = FastAPI(title="Shadow Onboarding")
CLIENTS = ("A", "B")
TRACK = "main"


def _run_dir(run_id: str):
    d = db.RUNS / run_id
    if not (d / "run.json").exists():
        raise HTTPException(404, "no such run")
    return d


def _items(run_id: str) -> list[dict]:
    d = _run_dir(run_id)
    grades = json.loads((d / "grades.json").read_text()) if (d / "grades.json").exists() else {}
    meta = json.loads((d / "run.json").read_text())
    pb = pbmod.load(meta["client"], meta.get("track") or TRACK, meta.get("playbook_version")) if meta.get("playbook_version") else None
    by_id = {r["id"]: r for r in (pb or {}).get("rules", [])}   # the playbook version this run actually used
    out = []
    for line in (d / "resolutions.jsonl").read_text().splitlines():
        it = json.loads(line)
        it["grade"] = grades.get(it["item_id"])
        it["rule"] = by_id.get(it["resolution"].get("rule_id"))
        out.append(it)
    return out


def _summary(run_id: str) -> dict:
    d = _run_dir(run_id)
    s = json.loads((d / "run.json").read_text())
    s["metrics"] = json.loads((d / "metrics.json").read_text()) if (d / "metrics.json").exists() else None
    return s


@app.get("/api/clients")
def clients():
    out = []
    for c in CLIENTS:
        if db.db_path(c).exists():
            out.append({k: v for k, v in db.q(db.connect(c, readonly=True), "SELECT * FROM client")[0].items() if k != "close_days"})
    return out


@app.get("/api/runs")
def runs():
    out = [_summary(p.parent.name) for p in db.RUNS.glob("*/run.json")]
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    return _summary(run_id) | {"items": _items(run_id)}


@app.get("/api/runs/{run_id}/queue")
def queue(run_id: str):
    return [it for it in reversed(_items(run_id)) if it["resolution"]["action"] == "escalate"]


@app.get("/api/record/{client}/{record_id}")
def record(client: str, record_id: str):
    con = db.connect(client, readonly=True)
    for table in ("bank_line", "ledger_entry", "invoice", "document", "reconcile_link", "journal_entry", "approval"):
        r = db.q(con, f"SELECT * FROM {table} WHERE id=?", record_id)
        if r:
            return {"table": table} | r[0]
    raise HTTPException(404, "no such record")


@app.get("/api/playbook/{client}")
def get_playbook(client: str, version: int | None = None, track: str = TRACK):
    pb = pbmod.load(client, track, version)
    if not pb:
        return {"client": client, "version": 0, "versions": [], "rules": []}
    vs = [{"version": v, "created_at": p["created_at"], "cause": p["cause"]}
          for v in pbmod.versions(client, track) for p in [pbmod.load(client, track, v)]]
    return pb | {"versions": vs}


@app.get("/api/playbook/{client}/diff")
def playbook_diff(client: str, to: int, frm: int | None = None, track: str = TRACK):
    a, b = pbmod.load(client, track, frm or to - 1), pbmod.load(client, track, to)
    if not a or not b:
        raise HTTPException(404, "no such version")
    return pbmod.diff(a, b)


class Correction(BaseModel):
    client: str
    run_id: str
    item_id: str
    resolution: dict
    note: str = ""
    track: str = TRACK


@app.post("/api/corrections")
def post_correction(c: Correction):
    item = next((it for it in _items(c.run_id) if it["item_id"] == c.item_id), None)
    if not item:
        raise HTTPException(404, "no such item in that run")
    human = {"action": c.resolution["action"], "ledger_ids": c.resolution.get("ledger_ids") or [],
             "adjustments": c.resolution.get("adjustments") or [], "escalate_to": c.resolution.get("escalate_to")}
    result = correct.correct(c.client, c.track, item, human, c.note, run_id=c.run_id)
    reran = []
    if result["diff"]:   # give every other item still in the queue another go under the new playbook
        s = _summary(c.run_id)
        open_ids = {it["item_id"] for it in _items(c.run_id) if it["resolution"]["action"] == "escalate" and it["item_id"] != c.item_id}
        if open_ids:
            rid = f"{c.run_id}__after_{result['correction_id']}"
            pipeline.run(c.client, s["period"], "corrected", c.track, only=open_ids, run_id=rid, use_llm=False,
                         label="re-run of the open queue after a correction (rules only)")
            reran = [it for it in _items(rid) if it["resolution"]["action"] != "escalate"]
    return result | {"reran": reran}


class Answer(BaseModel):
    client: str
    rule_id: str
    answer: str
    track: str = TRACK


@app.post("/api/playbook/answer")
def post_answer(a: Answer):
    return correct.answer(a.client, a.track, a.rule_id, a.answer)


@app.get("/api/metrics")
def metrics():
    path = db.RUNS / "results.json"
    out = json.loads(path.read_text()) if path.exists() else {"period": "2026-04", "clients": {}}
    bench = db.RUNS / "benchrec_results.json"
    if bench.exists():
        out["benchrec"] = json.loads(bench.read_text())
    return out


@app.get("/api/experiment")
def get_experiment(fresh: bool = False, version: int | None = None):
    return experiment.run(fresh=fresh, version=version)


@app.get("/api/experiment/bank-change")
def get_bank_change(fresh: bool = False):
    return experiment.bank_change(fresh=fresh)


if (db.ROOT / "ui").exists():
    app.mount("/", StaticFiles(directory=db.ROOT / "ui", html=True), name="ui")
