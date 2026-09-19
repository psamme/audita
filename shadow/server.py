"""JSON API for the UI. Contract: docs/API.md.

    uv run uvicorn shadow.server:app --port 8787
"""
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shadow import authority, preview, correct, db, experiment, pipeline, playbook as pbmod, stale, unlearn

app = FastAPI(title="Shadow Onboarding")
CLIENTS = ("A", "B")
TRACK = "main"


@app.exception_handler(PermissionError)
async def permission_error(request, exc):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(preview.StalePreview)
async def stale_preview(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def bad_value(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _run_dir(run_id: str):
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(404, "no such run")
    d = db.RUNS / run_id
    if not (d / "run.json").exists():
        raise HTTPException(404, "no such run")
    return d


def _items(run_id: str, grades: bool = False) -> list[dict]:
    d = _run_dir(run_id)
    grades = json.loads((d / "grades.json").read_text()) if grades and (d / "grades.json").exists() else {}
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
    if s["metrics"]:
        s["metrics"].pop("by_category", None)      # category names describe the hidden test
    return s


@app.get("/api/clients")
def clients():
    out = []
    for c in CLIENTS:
        if db.db_path(c).exists():
            con = db.connect(c, readonly=True)
            out.append({k: v for k, v in db.q(con, "SELECT * FROM client")[0].items() if k != "close_days"} |
                       {"senior_roles": [u["role"].lower().replace(" ", "_") for u in db.q(con, "SELECT role FROM user WHERE senior=1")]})
    return out


@app.get("/api/runs")
def runs():
    out = [_summary(p.parent.name) for p in db.RUNS.glob("*/run.json")]
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


@app.get("/api/runs/{run_id}")
def run(run_id: str, grades: bool = False):
    """grades=true attaches the grader's verdict per item (off by default: it reveals the answer key item by item)."""
    return _summary(run_id) | {"items": _items(run_id, grades)}


@app.get("/api/runs/{run_id}/queue")
def queue(run_id: str, grades: bool = False):
    return [it for it in reversed(_items(run_id, grades)) if it["resolution"]["action"] == "escalate"]


def _con(client: str):
    if client not in CLIENTS or not db.db_path(client).exists():
        raise HTTPException(404, "no such client")
    return db.connect(client, readonly=True)


@app.get("/api/record/{client}/{record_id}")
def record(client: str, record_id: str):
    con = _con(client)
    for table in ("bank_line", "ledger_entry", "invoice", "document", "reconcile_link", "journal_entry", "approval"):
        r = db.q(con, f"SELECT * FROM {table} WHERE id=?", record_id)
        if r:
            return {"table": table} | r[0]
    raise HTTPException(404, "no such record")


@app.get("/api/playbook/{client}")
def get_playbook(client: str, version: int | None = None, track: str = TRACK):
    _con(client)
    try:
        if version and version not in pbmod.versions(client, track):
            raise HTTPException(404, "no such version")
        pb = pbmod.load(client, track, version)
    except ValueError:
        raise HTTPException(404, "no such track")
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
    role: str | None = None       # who is teaching; a non-senior role cannot widen auto-resolution without sign-off


@app.post("/api/corrections")
def post_correction(c: Correction):
    meta = _summary(c.run_id)
    if meta["client"] != c.client or meta.get("track", TRACK) != c.track:
        raise HTTPException(400, "The run must belong to this client and track")
    item = next((it for it in _items(c.run_id) if it["item_id"] == c.item_id), None)
    if not item:
        raise HTTPException(404, "no such item in that run")
    human = {"action": c.resolution["action"], "ledger_ids": c.resolution.get("ledger_ids") or [],
             "adjustments": c.resolution.get("adjustments") or [], "escalate_to": c.resolution.get("escalate_to")}
    result = correct.correct(c.client, c.track, item, human, c.note, run_id=c.run_id, role=c.role)
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
    answer: str | None = None     # "limit" | "review" | "usual" | "not_amount"
    limit: float | None = None    # required when answer is "limit": the stated limit closes the band in one answer
    value: float | None = None    # the value that was asked about, for "review" and "usual"
    review: bool | None = None    # older callers: True is "review", False is "usual"
    role: str | None = None       # who answered; only a senior role may move a band
    track: str = TRACK


@app.post("/api/playbook/answer-band")
def post_band_answer(a: BandAnswer):
    kind = a.answer or {True: "review", False: "usual"}.get(a.review)
    if kind not in ("limit", "review", "usual", "not_amount"):
        raise HTTPException(400, "answer must be limit, review, usual or not_amount")
    if kind == "limit" and (a.limit is None or a.limit < 0):
        raise HTTPException(400, "answer 'limit' needs a non-negative limit")
    if kind in ("review", "usual") and a.value is None:
        raise HTTPException(400, f"answer '{kind}' needs the value that was asked about")
    _con(a.client).close()
    try:
        return correct.answer_band(a.client, a.track, a.rule_id, a.condition,
                                   value=a.value if kind in ("review", "usual") else None,
                                   review={"review": True, "usual": False}.get(kind),
                                   limit=a.limit if kind == "limit" else None,
                                   not_amount=kind == "not_amount", role=a.role)
    except ValueError as e:      # unknown rule or band
        raise HTTPException(400, str(e))


class BandPreview(BandAnswer):
    period: str


@app.post("/api/playbook/preview-band")
def preview_band(a: BandPreview):
    _con(a.client).close()
    kind = a.answer or {True: "review", False: "usual"}.get(a.review)
    if kind not in {"limit", "review", "usual", "not_amount"}:
        raise HTTPException(400, "Choose limit, review, usual or not_amount")
    return preview.build(a.client, a.track, a.period, a.rule_id, a.condition, a.role,
                         value=a.value if kind in {"review", "usual"} else None,
                         review={"review": True, "usual": False}.get(kind),
                         limit=a.limit if kind == "limit" else None, not_amount=kind == "not_amount")


class ApplyPreview(BaseModel):
    preview_id: str
    role: str


@app.post("/api/playbook/apply-preview")
def apply_preview(a: ApplyPreview):
    return preview.commit(a.preview_id, a.role)


class ConflictOutcome(BaseModel):
    client: str
    outcome: str          # one_off_exception | policy_change | mistake
    role: str | None = None
    track: str = TRACK


@app.post("/api/conflicts/{conflict_id}")
def settle_conflict(conflict_id: str, o: ConflictOutcome):
    if o.outcome not in ("one_off_exception", "policy_change", "mistake"):
        raise HTTPException(400, "outcome must be one_off_exception, policy_change or mistake")
    return correct.resolve_conflict(o.client, o.track, conflict_id, o.outcome, o.role)


class Retraction(BaseModel):
    role: str | None = None
    client: str
    correction_id: str | None = None
    precedent_id: str | None = None
    note: str = ""
    track: str = TRACK


@app.post("/api/retract")
def post_retract(r: Retraction):
    """Undo one input of the playbook. Deterministic: stored patches are replayed, no model call."""
    authority.require_senior(_con(r.client), r.role)
    try:
        return unlearn.retract(r.client, r.track, r.correction_id, r.precedent_id, r.note)
    except ValueError as e:
        raise HTTPException(404, str(e))


def _jsonl(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()][::-1] if path.exists() else []


@app.get("/api/corrections/{client}")
def corrections(client: str, track: str = TRACK):
    """Everything this track's playbook was taught, newest first. Non-main tracks keep their own log."""
    _con(client).close()
    if not track.isidentifier():
        raise HTTPException(404, "no such track")
    return _jsonl(correct.log_path(client, track))


@app.get("/api/reopened/{client}")
def reopened(client: str, track: str = TRACK):
    _con(client).close()
    if not track.isidentifier():
        raise HTTPException(404, "no such track")
    return _jsonl(db.DATA / client / ("reopened.jsonl" if track == "main" else f"reopened_{track}.jsonl"))


@app.get("/api/runs/{run_id}/stale")
def stale_check(run_id: str):
    meta = _summary(run_id)
    return stale.verify(meta["client"], run_id)


@app.get("/api/curve")
def curve():
    path = db.RUNS / "questions_to_trust.json"
    return json.loads(path.read_text()) if path.exists() else {}


class Answer(BaseModel):
    role: str | None = None
    client: str
    rule_id: str
    answer: str
    track: str = TRACK


@app.post("/api/playbook/answer")
def post_answer(a: Answer):
    return correct.answer(a.client, a.track, a.rule_id, a.answer, role=a.role)


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
