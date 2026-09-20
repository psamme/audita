"""Client C, the real ledger: read-only routes over runs/real_results.json (written by `python -m benchrec.real`)."""

import json

from fastapi import APIRouter, HTTPException

from shadow import db

router = APIRouter(prefix="/api/real")
RESULTS = db.ROOT / "runs" / "real_results.json"


def _load() -> dict:
    path = RESULTS if RESULTS.exists() else db.ROOT / "benchrec" / "report_summary.json"
    if not path.exists():
        raise HTTPException(404, "No saved benchmark report is available.")
    return json.loads(path.read_text())


@router.get("/results")
def results():
    r = _load()
    return {k: v for k, v in r.items() if k not in ("examples", "playbook")}


@router.get("/playbook")
def playbook():
    r = _load()
    return {"client": r["client"], "floor": 0.98, "conventions": r["playbook"],
            "by_convention_on_eval": r["by_convention_on_eval"], "train": r["train"]}


@router.get("/examples")
def examples(verdict: str | None = None, convention: str | None = None):
    rows = _load()["examples"]
    if verdict:
        rows = [x for x in rows if x["verdict"] == verdict]
    if convention:
        rows = [x for x in rows if x["convention"] == convention]
    return {"examples": rows}
