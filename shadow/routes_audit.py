"""Audit routes. The report is written by `python -m shadow.auditor`; these only serve it.

    GET /api/audit/{client}                    the report without the per-item files
    GET /api/audit/{client}/file/{item_id}     one sampled item's audit file
    GET /api/audit/{client}/download           the whole audit file as a JSON attachment
"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from shadow import db

router = APIRouter()
CLIENTS = ("A", "B")


def _report(client: str) -> dict:
    path = db.RUNS / f"audit_{client}.json"
    if client not in CLIENTS or not path.exists():
        raise HTTPException(404, f"no audit for this client yet; run: uv run python -m shadow.auditor --client {client[:1]} --run runs/<run_id>")
    return json.loads(path.read_text())


@router.get("/api/audit/{client}")
def audit(client: str):
    rep = _report(client)
    files = rep.pop("files")
    rep["sampled_items"] = [{"item_id": f["item_id"], "item_kind": f["item_kind"], "stratum": f["stratum"], "date": f["record"]["date"],
                             "amount": f["record"]["amount"], "text": f["record"].get("description") or f["record"].get("memo") or "",
                             "tier": f["preparer"]["tier"], "action": f["preparer"]["action"], "escalate_to": f["preparer"]["escalate_to"],
                             "by_model": bool(f["auditor"]["model"]), "verdict": f["auditor"]["verdict"]} for f in files]
    return rep


@router.get("/api/audit/{client}/file/{item_id}")
def audit_file(client: str, item_id: str):
    for f in _report(client)["files"]:
        if f["item_id"] == item_id:
            return f
    raise HTTPException(404, "that item was not in the audit sample")


@router.get("/api/audit/{client}/download")
def download(client: str):
    rep = _report(client)
    name = f"audit-file-{rep['client']}-{rep['period']}.json"
    return Response(json.dumps(rep, indent=1), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})
