"""Stale evidence: a resolution is only as good as the records it stood on.

Every resolution stores a fingerprint of its evidence (each ledger entry's amount, date and account, plus document
ids). Re-verifying a run compares those fingerprints with the ledger as it is now. An entry that was edited or
removed, or a new entry posted into the period after it was reconciled, re-opens the item with reason
"evidence_changed" and says exactly what moved.
"""
import json

from shadow import db

FIELDS = ("amount", "date", "account", "counterparty")


def fingerprint(con, resolution: dict) -> dict:
    ids = list(resolution.get("ledger_ids") or [])
    ledger = {i: {f: r[0][f] for f in FIELDS} for i in ids for r in [db.q(con, "SELECT * FROM ledger_entry WHERE id=?", i)] if r}
    return {"ledger": ledger, "documents": sorted(e for e in resolution.get("evidence_ids") or [] if "-DOC-" in e)}


def verify(client: str, run_id: str, db_file=None) -> dict:
    con = db.connect(client, readonly=True, path=db_file)
    meta = json.loads((db.RUNS / run_id / "run.json").read_text())
    stale = []
    for line in (db.RUNS / run_id / "resolutions.jsonl").read_text().splitlines():
        it = json.loads(line)
        changes = []
        for lid, was in (it.get("evidence_fingerprint") or {}).get("ledger", {}).items():
            now = db.q(con, "SELECT * FROM ledger_entry WHERE id=?", lid)
            if not now:
                changes.append({"record": lid, "change": "deleted", "was": was})
                continue
            moved = {f: {"was": was[f], "now": now[0][f]} for f in FIELDS if was[f] != now[0][f]}
            if moved:
                changes.append({"record": lid, "change": "edited", "fields": moved})
        for did in (it.get("evidence_fingerprint") or {}).get("documents", []):
            if not db.q(con, "SELECT id FROM document WHERE id=?", did):
                changes.append({"record": did, "change": "deleted"})
        if changes:
            stale.append({"item_id": it["item_id"], "reason": "evidence_changed", "record": it["record"],
                          "resolution": {k: it["resolution"].get(k) for k in ("action", "ledger_ids", "adjustments", "rule_id")},
                          "changes": changes})
    late = db.q(con, "SELECT * FROM ledger_entry WHERE period=? AND posted_at > ? ORDER BY posted_at", meta["period"], meta["created_at"][:10])
    return {"run_id": run_id, "checked_at_playbook_version": meta.get("playbook_version"), "stale": stale,
            "posted_after_reconciliation": late}
