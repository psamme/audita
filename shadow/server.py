"""JSON API for the UI. Contract: docs/API.md.

    uv run uvicorn shadow.server:app --port 8787
"""
import json

from fastapi import FastAPI, HTTPException, Query
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
def playbook_diff(client: str, to: int, frm: int | None = Query(None, alias="from"), track: str = TRACK):
    have = pbmod.versions(client, track)
    if to not in have or (frm or to - 1) not in have:
        raise HTTPException(404, "no such version")
    a, b = pbmod.load(client, track, frm or to - 1), pbmod.load(client, track, to)
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


@app.get("/api/playbook/{client}/questions")
def questions(client: str, track: str = TRACK):
    pb = pbmod.load(client, track)
    if not pb:
        return {"band_questions": [], "open_questions": []}
    return {"band_questions": pbmod.band_questions(pb),
            "open_questions": [{"rule_id": r["id"], "rule_text": r["text"], "text": r["open_question"], "precedent_count": r.get("precedent_count", 0)}
                               for r in pb["rules"] if r["status"] == "proposed" and r.get("open_question")]}


class BandAnswer(BaseModel):
    client: str
    rule_id: str
    condition: str
    value: float
    review: bool          # True: "yes, send one at that value for review"; False: "no, handle it the usual way"
    track: str = TRACK


@app.post("/api/playbook/answer-band")
def post_band_answer(a: BandAnswer):
    out = pbmod.answer_band(a.client, a.track, a.rule_id, a.condition, a.value, a.review)
    correct._log(a.client, {"type": "interview", "source": "interview", "rule_id": a.rule_id, "condition": a.condition,
                            "value": a.value, "review": a.review, "playbook_version": out["new_version"]})
    return out


@app.get("/api/curve")
def curve():
    path = db.RUNS / "questions_to_trust.json"
    return json.loads(path.read_text()) if path.exists() else {}


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


def _cached(value):
    if value is None:
        raise HTTPException(404, "not computed yet; POST /api/experiment/run")
    return value


@app.get("/api/experiment")
def get_experiment(version: int | None = None):
    return _cached(experiment.run(version=version))


@app.get("/api/experiment/bank-change")
def get_bank_change():
    return _cached(experiment.bank_change())


class ExperimentRun(BaseModel):
    which: str = "same_transaction"      # or "bank_change"
    version: int | None = None
    track: str = TRACK


@app.post("/api/experiment/run")
def run_experiment(r: ExperimentRun):
    """Live re-run. Costs model calls; works on a throwaway copy of the client database."""
    if r.which == "bank_change":
        return experiment.bank_change(track=r.track, fresh=True)
    return experiment.run(track=r.track, fresh=True, version=r.version)


if (db.ROOT / "ui").exists():
    app.mount("/", StaticFiles(directory=db.ROOT / "ui", html=True), name="ui")
