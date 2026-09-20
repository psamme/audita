"""HTTP surface for onboarding a real company.

Everything that costs money or changes the database is a POST, matching the convention the
existing API already follows. Anything that takes minutes returns a job id instead of blocking.
"""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from shadow import db, jobs, llm, pipeline, playbook as pbmod
from shadow.onboard import (boundary, classify, coldstart, company, contract, importer,
                            mapping, preflight, staging)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _client() -> str:
    c = company.configured()
    if not c or not company.exists(c):
        raise HTTPException(404, "no company has been set up yet")
    return c


@router.get("/health")
def health():
    """Also proves the routers were mounted ahead of the static catch-all."""
    from shadow import server
    return {"ok": True, "company": company.configured(), "clients": list(server.CLIENTS)}


# --- setup ----------------------------------------------------------------------------------
class Person(BaseModel):
    id: str
    name: str = ""
    role: str
    senior: bool = False


class NewCompany(BaseModel):
    id: str
    name: str
    blurb: str = ""
    chart: dict[str, str] = {}
    chart_text: str = ""
    close_days: int = company.DEFAULT_CLOSE_DAYS
    users: list[Person] = []
    reconciler: str | None = None


@router.post("/company")
def create_company(c: NewCompany):
    chart = c.chart or company.parse_chart(c.chart_text)
    try:
        return company.create(c.id, c.name, c.blurb, chart,
                              [p.model_dump() for p in c.users], c.close_days, c.reconciler)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/company")
def get_company():
    c = company.configured()
    if not c:
        return {"created": False}
    return company.summary(c)


class Roster(BaseModel):
    users: list[Person]
    reconciler: str | None = None
    force: bool = False


@router.post("/users")
def set_users(r: Roster):
    try:
        return company.set_users(_client(), [p.model_dump() for p in r.users], r.reconciler,
                                 force=r.force)
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- import ---------------------------------------------------------------------------------
@router.post("/upload")
async def upload(request: Request, role: str = Query(...), filename: str = Query("upload.csv"), purpose: str = Query("history")):
    """Raw CSV body, so no multipart dependency is needed."""
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "empty upload")
    try:
        return staging.store(_client(), role, filename, raw, purpose)
    except ValueError as e:
        raise HTTPException(400, str(e))


class Proposal(BaseModel):
    upload_id: str


@router.post("/mapping/propose")
def propose(p: Proposal):
    client = _client()
    try:
        up = staging.load(client, p.upload_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    usage = llm.Usage()
    out = mapping.guess(up["role"], up["columns"]) | {"source": "heuristic", "notes": "Review the column matches before importing. No model call was needed."}
    return out | {"role": up["role"], "upload_id": p.upload_id, "usage": usage.as_dict()}


class MappingIn(BaseModel):
    upload_id: str
    mapping: dict


@router.post("/mapping/validate")
def validate(m: MappingIn):
    client = _client()
    try:
        up = staging.load(client, m.upload_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return mapping.validate(up["role"], m.mapping, up["parsed"]) | {"role": up["role"]}


class ImportIn(BaseModel):
    upload_id: str
    mapping: dict
    mode: str = "upsert"
    date_from: str | None = None
    date_to: str | None = None
    cash_accounts: list[str] | None = None


@router.post("/import")
def start_import(i: ImportIn):
    client = _client()

    def work(job):
        with jobs.client_lock(client):
            return importer.run_import(client, i.upload_id, i.mapping, i.mode,
                                       i.date_from, i.date_to, i.cash_accounts, job=job)

    try:
        return jobs.submit("import", client, work, meta={"upload_id": i.upload_id},
                           estimate_s=20, exclusive=False)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/import/{import_id}/undo")
def undo_import(import_id: str):
    try:
        return importer.undo(_client(), import_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


# --- drop a folder in ----------------------------------------------------------------------
@router.post("/identify")
async def identify(request: Request, filename: str = Query("upload.csv"),
                   purpose: str = Query("history"), role: str = Query(None),
                   use_llm: bool = Query(False)):
    """One dropped file: read it, decide what it is, stage it and propose its columns.

    The person still confirms, but they confirm a filled-in answer instead of starting from a
    menu. `role` forces a kind when they disagree; `use_llm` is the one paid escape hatch, for a
    file the column names alone cannot place.
    """
    client = _client()
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "empty upload")
    try:
        parsed = staging.read(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    columns = staging.profile(parsed["header"], parsed["rows"])

    # Classify against every kind even on the new-receipts page. Narrowing the field first would
    # force a reconciliation export into the nearest permitted shape and report a vague failure,
    # when the useful answer is that this file is history and belongs on the other page.
    usage = llm.Usage()
    if role:
        if role not in contract.SPECS:
            raise HTTPException(400, f"unknown upload kind {role}")
        found = classify.score(role, columns, filename)
        verdict = {"role": role, "label": found["label"], "confidence": 1.0, "close": False,
                   "source": "you", "why": "You chose this kind.", "alternatives": [],
                   "mapping": found["mapping"],
                   "ranked": classify.classify(columns, filename)["ranked"]}
    elif use_llm:
        verdict = classify.propose(columns, filename, usage=usage)
    else:
        verdict = classify.classify(columns, filename)

    card = {"filename": filename, "purpose": purpose, "rows": len(parsed["rows"]),
            "header": parsed["header"], "usage": usage.as_dict(), **verdict}
    if not verdict["role"]:
        card["blocked"] = "We could not tell what this file is. Choose a kind."
        return card
    if purpose == "incoming" and verdict["role"] not in boundary.INCOMING_ROLES:
        card["blocked"] = (f"This reads as {verdict['label'].lower()}, which records a decision "
                           "your team already made. New receipts carry source records only.")
        return card
    try:
        up = staging.store(client, verdict["role"], filename, raw, purpose)
    except ValueError as e:
        card["blocked"] = str(e)
        return card
    card["upload_id"] = up["upload_id"]
    card["encoding"], card["delimiter"] = up["encoding"], up["delimiter"]
    card["skipped_preamble"] = up["skipped_preamble"]
    card["duplicate_of"] = up["duplicate_of"]
    card["check"] = mapping.validate(verdict["role"], verdict["mapping"], parsed)
    return card


class BatchImport(BaseModel):
    files: list[ImportIn]


@router.post("/import/batch")
def start_batch_import(b: BatchImport):
    """Import several staged files in one job, parents first.

    Order is not a nicety here. A reconciliation row resolves its bank and ledger references
    against records that must already exist, so importing the trail before the statement leaves
    every row dangling and reports nothing but unresolved references.
    """
    client = _client()
    if not b.files:
        raise HTTPException(400, "nothing to import")
    try:
        staged = [(staging.load(client, f.upload_id), f) for f in b.files]
    except ValueError as e:
        raise HTTPException(404, str(e))
    staged.sort(key=lambda pair: classify.ORDER.index(pair[0]["role"]))

    def work(job):
        with jobs.client_lock(client):
            reports, failed = [], []
            for i, (up, f) in enumerate(staged):
                job.phase(f"{up['filename']} ({i + 1} of {len(staged)})")
                try:
                    reports.append(importer.run_import(
                        client, f.upload_id, f.mapping, f.mode, f.date_from, f.date_to,
                        f.cash_accounts, job=None))
                except (ValueError, KeyError) as e:
                    failed.append({"filename": up["filename"], "role": up["role"],
                                   "error": str(e)})
            return {"imported": reports, "failed": failed,
                    "inserted": sum(r["inserted"] for r in reports),
                    "updated": sum(r["updated"] for r in reports),
                    "warnings": [w for r in reports for w in r["warnings"]],
                    "order": [up["role"] for up, _ in staged]}

    try:
        return jobs.submit("import", client, work,
                           meta={"files": len(staged)}, estimate_s=20 * len(staged),
                           exclusive=False)
    except ValueError as e:
        raise HTTPException(409, str(e))


# --- cold start -----------------------------------------------------------------------------
@router.get("/coldstart/status")
def coldstart_status(before: str | None = None):
    client = _client()
    res = coldstart.residue(client, before, limit=0)
    con = db.connect(client, readonly=True)
    try:
        links = con.execute("SELECT COUNT(*) FROM reconcile_link").fetchone()[0]
        derived = con.execute("SELECT COUNT(*) FROM reconcile_link WHERE id LIKE ?",
                              (f"{client}-{coldstart.AUTO_PREFIX}-%",)).fetchone()[0]
        bank = con.execute("SELECT COUNT(*) FROM bank_line").fetchone()[0]
    finally:
        con.close()
    return {"bank_lines": bank, "links": links, "derived_links": derived,
            "your_links": links - derived, "residue": res["total"],
            "kinds": res["kinds"], "kinds_ready": res["kinds_ready"], "shapes": res["shapes"]}


class Derive(BaseModel):
    reconciler: str | None = None
    before: str | None = None


@router.post("/coldstart/derive")
def derive(d: Derive):
    client = _client()
    who = d.reconciler or company.summary(client).get("reconciler")
    if not who:
        raise HTTPException(400, "name the person who normally clears the bank, so derived "
                                 "matches are attributed to a real preparer")

    def work(job):
        with jobs.client_lock(client):
            return coldstart.derive_links(client, who, d.before, job=job)

    try:
        return jobs.submit("derive", client, work, meta={"reconciler": who}, estimate_s=30)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/coldstart/derive/clear")
def clear_derived():
    return coldstart.clear_derived(_client())


@router.get("/coldstart/queue")
def coldstart_queue(limit: int = 25, offset: int = 0, before: str | None = None):
    return coldstart.residue(_client(), before, limit=limit, offset=offset)


class Label(BaseModel):
    item_id: str
    action: str
    ledger_ids: list[str] = []
    adjustments: list[dict] = []
    handled_by: str = ""
    days_to_handle: int = 1
    senior_signed_off: bool = False
    escalate_to: str | None = None
    note: str = ""


@router.post("/coldstart/label")
def label(l: Label):
    try:
        return coldstart.label(_client(), l.item_id, l.action, l.ledger_ids, l.adjustments,
                               l.handled_by, l.days_to_handle, l.senior_signed_off,
                               l.escalate_to, l.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/coldstart/unlabel/{item_id}")
def unlabel(item_id: str):
    return coldstart.unlabel(_client(), item_id)


# --- learn and run --------------------------------------------------------------------------
@router.get("/preflight")
def get_preflight(before: str | None = None):
    return preflight.run(_client(), boundary.cutoff(_client()) or before)


class Induce(BaseModel):
    before: str | None = None
    track: str = "main"
    force: bool = False


@router.post("/induce")
def induce(i: Induce):
    client = _client()
    before = boundary.cutoff(client) or i.before
    if i.before and boundary.cutoff(client) and i.before != boundary.cutoff(client):
        raise HTTPException(400, "New receipts are excluded from training.")
    pre = preflight.run(client, before)
    if not pre["ready"] and not i.force:
        raise HTTPException(409, {"message": "the history cannot teach a playbook yet",
                                  "blocking": [c for c in pre["checks"] if c["level"] == "block"]})
    before = before or pre["before"]
    boundary.freeze(client, before)

    def work(job):
        job.phase("reading the trail")
        usage = llm.Usage()
        with jobs.client_lock(client):
            pb = pbmod.induce(client, before, i.track, usage=usage, db_file=db.DATA / client / "training_snapshot.db")
        approved = sum(r["status"] == "approved" for r in pb["rules"])
        return {"version": pb["version"], "rules": len(pb["rules"]), "approved": approved,
                "open_questions": sum(bool(r.get("open_question")) for r in pb["rules"]),
                "findings": len(pb.get("findings") or []), "track": i.track,
                "usage": usage.as_dict()}

    try:
        return jobs.submit("induce", client, work,
                           meta={"before": before, "track": i.track,
                                 "estimate": pre["estimate"]},
                           estimate_s=pre["estimate"]["seconds"])
    except ValueError as e:
        raise HTTPException(409, str(e))


class Reconcile(BaseModel):
    period: str
    track: str = "main"
    condition: str = "playbook"
    use_llm: bool = True
    version: int | None = None


@router.post("/reconcile/estimate")
def reconcile_estimate(r: Reconcile):
    """Free: the deterministic tiers alone tell us how many items need the model, and what that
    will cost, before anything is spent."""
    client = _client()
    boundary.run_period(client, r.period)
    rid = f"estimate_{client}_{r.period}"
    summary = pipeline.run(client, r.period, r.condition, r.track, version=r.version,
                           use_llm=False, run_id=rid, label="cost estimate", persist=False)
    need = summary["tiers"]["investigator"]
    resolved = sum(i["resolution"]["action"] in {"match", "match_adjust", "book"} for i in summary["items"])
    per_item = 0.09
    return {"period": r.period, "n_items": summary["n_items"], "tiers": summary["tiers"],
            "needs_model": need, "cleared_free": resolved,
            "est_usd": round(need * per_item, 2), "est_seconds": int(20 + need * 4),
            "run_id": rid}


@router.post("/reconcile")
def reconcile(r: Reconcile):
    client = _client()
    boundary.run_period(client, r.period)
    if r.condition != "zero_shot" and not pbmod.load(client, r.track):
        raise HTTPException(409, "there is no playbook on this track yet: induce one first, or "
                                 "run with condition zero_shot")

    def work(job):
        job.phase("guardrails and matching")
        with jobs.client_lock(client):
            return pipeline.run(client, r.period, r.condition, r.track, version=r.version,
                                use_llm=r.use_llm, label="company run")

    try:
        return jobs.submit("reconcile", client, work,
                           meta={"period": r.period, "use_llm": r.use_llm}, estimate_s=180)
    except ValueError as e:
        raise HTTPException(409, str(e))


# --- jobs -----------------------------------------------------------------------------------
@jobs_router.get("/{job_id}")
def get_job(job_id: str):
    rec = jobs.get(job_id)
    if not rec:
        raise HTTPException(404, "no such job")
    if rec["client"] != _client():
        raise HTTPException(404, "no such job")
    return rec


@jobs_router.get("")
def list_jobs(kind: str | None = None, limit: int = 20):
    c = company.configured()
    return jobs.recent(c, kind, limit)


@jobs_router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    return {"cancelled": jobs.cancel(job_id),
            "note": "a model call already in flight will finish before the job stops"}

@router.get('/template')
def csv_template(role: str):
    import csv
    import io
    from fastapi.responses import Response
    from shadow.onboard import contract
    if role not in contract.SPECS:
        raise HTTPException(404, 'No such CSV template')
    buf = io.StringIO()
    csv.writer(buf).writerow(contract.fields_for(role))
    return Response(buf.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="{role}.csv"'})
