"""Recheck decision evidence, including edits, unlinks, reversals and new entries."""
import json

from shadow import db

FIELDS = ("amount", "date", "account", "counterparty", "posted_at", "ref", "invoice_id")


def _records(con, table, ids):
    return {i: r[0] for i in ids for r in [db.q(con, f"SELECT * FROM {table} WHERE id=?", i)] if r}


def fingerprint(con, resolution: dict, item: dict | None = None) -> dict:
    ledger = _records(con, "ledger_entry", resolution.get("ledger_ids") or [])
    documents = _records(con, "document", resolution.get("evidence_ids") or [])
    bank_id = (item or {}).get("id")
    links = db.q(con, "SELECT * FROM reconcile_link WHERE bank_id=?", bank_id) if bank_id else []
    journals = db.q(con, "SELECT * FROM journal_entry WHERE bank_id=?", bank_id) if bank_id else []
    for row in list(journals):
        journals += db.q(con, "SELECT * FROM journal_entry WHERE reverses_id=?", row["id"])
    return {"ledger": {i: {f: r[f] for f in FIELDS} for i, r in ledger.items()},
            "documents": sorted(documents), "document_records": documents,
            "bank": _records(con, "bank_line", [bank_id]) if bank_id else {},
            "links": {r["id"]: r for r in links}, "journals": {r["id"]: r for r in journals},
            "bank_id": bank_id}


def _changes(was, now):
    changes = []
    for rid in sorted(was.keys() | now.keys()):
        if rid not in now:
            changes.append({"record": rid, "change": "deleted"})
        elif rid not in was:
            changes.append({"record": rid, "change": "added"})
        else:
            moved = {f: {"was": value, "now": now[rid].get(f)} for f, value in was[rid].items()
                     if value != now[rid].get(f)}
            if moved:
                changes.append({"record": rid, "change": "edited", "fields": moved})
    return changes


def verify(client: str, run_id: str, db_file=None) -> dict:
    con = db.connect(client, readonly=True, path=db_file)
    meta = json.loads((db.RUNS / run_id / "run.json").read_text())
    stale = []
    for line in (db.RUNS / run_id / "resolutions.jsonl").read_text().splitlines():
        it = json.loads(line)
        was = it.get("evidence_fingerprint") or {}
        now = fingerprint(con, it["resolution"], {"id": was.get("bank_id")})
        changes = _changes(was.get("ledger", {}), {k: v for k, v in now["ledger"].items() if k in was.get("ledger", {})})
        if "document_records" in was:
            changes += _changes(was["document_records"], now["document_records"])
        else:  # old runs can establish deletion, but have no content snapshot
            changes += [{"record": did, "change": "deleted"} for did in was.get("documents", [])
                        if not db.q(con, "SELECT id FROM document WHERE id=?", did)]
        for group in ("bank", "links", "journals"):
            if group in was:
                changes += _changes(was[group], now[group])
        if changes:
            stale.append({"item_id": it["item_id"], "reason": "evidence_changed", "record": it["record"],
                          "resolution": {k: it["resolution"].get(k) for k in ("action", "ledger_ids", "adjustments", "rule_id")},
                          "changes": changes})
    entries = db.q(con, "SELECT * FROM ledger_entry WHERE period=? ORDER BY posted_at, id", meta["period"])
    if "ledger_snapshot_ids" in meta:
        late = [e for e in entries if e["id"] not in set(meta["ledger_snapshot_ids"])]
    else:
        late = [e for e in entries if e["posted_at"][:10] > meta["created_at"][:10]]
    return {"run_id": run_id, "checked_at_playbook_version": meta.get("playbook_version"), "stale": stale,
            "posted_after_reconciliation": late, "complete_snapshot": "ledger_snapshot_ids" in meta}
