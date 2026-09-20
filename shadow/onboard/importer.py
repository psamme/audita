"""Turn a confirmed mapping into rows in the client database.

Every write goes through here so the rules that are easy to get wrong live in one place:

- `period` is always derived from a date, never mapped, because every `WHERE period < ?` in the
  codebase is a string comparison on `YYYY-MM`.
- A reconcile link and an adjusting entry take the period of the *bank line* they belong to, not
  of the ledger row. `history.observe` keys both by `bank_id`; taking the ledger's period silently
  drops anything that cleared in a later month.
- `bank_line.description` and `ledger_entry.posted_at` are never null. The matcher lowercases one
  and compares the other with `>`, and both raise on None.
"""
import json
import uuid
from datetime import datetime, timezone

from shadow import db
from shadow.onboard import contract, ids, staging, boundary, mapping as mapmod

TABLE_COLUMNS = {
    "bank_line": ["id", "period", "date", "amount", "description", "counterparty", "ref"],
    "ledger_entry": ["id", "period", "date", "posted_at", "account", "amount", "memo",
                     "counterparty", "ref", "invoice_id"],
    "invoice": ["id", "party", "direction", "date", "due_date", "amount", "terms", "status"],
    "document": ["id", "type", "date", "sender", "subject", "body", "meta"],
    "reconcile_link": ["id", "period", "bank_id", "ledger_id", "amount", "reconciled_by",
                       "reconciled_at", "undone_at"],
    "journal_entry": ["id", "period", "date", "posted_at", "posted_by", "account", "amount",
                      "memo", "bank_id", "reverses_id"],
    "approval": ["id", "period", "date", "subject", "subject_id", "requested_by", "approver",
                 "status", "comment"],
}


def manifest_dir(client: str):
    p = db.DATA / client / "import"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _coerce(role: str, row: dict, mapping: dict, dayfirst: bool) -> dict:
    cols = mapping.get("columns", {})
    out: dict = {}
    for field in contract.fields_for(role):
        src = cols.get(field)
        if not src and not (field == "amount" and contract.has_amount(mapping)):
            continue
        raw = row.get(src, "") if src else ""
        if field in ("date", "posted_at", "reconciled_at", "due_date", "undone_at"):
            if str(raw).strip():
                out[field] = contract.to_date(raw, field, dayfirst=dayfirst)
        elif field == "amount":
            out[field] = contract.resolve_amount(row, mapping)
        elif field == "senior":
            out[field] = contract.to_bool(raw)
        else:
            out[field] = contract.to_text(raw, field)
    return out


def run_import(client: str, upload_id: str, mapping: dict, mode: str = "upsert",
               date_from: str | None = None, date_to: str | None = None,
               cash_accounts: list[str] | None = None, job=None) -> dict:
    up = staging.load(client, upload_id)
    role, parsed = up["role"], up["parsed"]
    spec = contract.SPECS[role]
    purpose = up.get("purpose", "history")
    boundary.check_upload(client, purpose, role, up["header"])
    check = mapmod.validate(role, mapping, parsed)
    if not check["ok"] or check["stats"]["date_format_ambiguous"]:
        raise ValueError("Fix the column mapping and invalid rows before importing.")
    if job:
        job.phase(f"reading {up['filename']}")

    dayfirst = mapping.get("dayfirst")
    if dayfirst is None and mapping.get("columns", {}).get("date"):
        dayfirst = contract.sniff_dayfirst(
            [r.get(mapping["columns"]["date"], "") for r in parsed["rows"][:200]])
    dayfirst = bool(dayfirst)

    if role == "chart_of_accounts":
        return _import_chart(client, parsed, mapping, up)
    if role == "people":
        return _import_people(client, parsed, mapping, up, dayfirst)

    alloc = ids.Allocator(client)
    table = spec["table"]
    rows_out, errors, skipped = [], [], 0
    dropped_accounts = 0
    posted_at_filled = 0

    for i, raw_row in enumerate(parsed["rows"]):
        try:
            v = _coerce(role, raw_row, mapping, dayfirst)
        except contract.Bad as e:
            if len(errors) < 100:
                errors.append({"row": i + 1, "field": e.field, "value": str(e.value)[:60],
                               "problem": e.problem})
            continue
        d = v.get("date") or v.get("reconciled_at") or v.get("posted_at")
        if d and date_from and d < date_from:
            skipped += 1
            continue
        if d and date_to and d > date_to:
            skipped += 1
            continue
        if role == "ledger_entries" and cash_accounts and v.get("account") not in cash_accounts:
            dropped_accounts += 1
            continue
        if role == "ledger_entries" and not v.get("posted_at"):
            v["posted_at"] = v["date"]          # never null: history and matcher compare it with >
            posted_at_filled += 1
        if role == "bank_lines":
            v["description"] = v.get("description") or ""   # never null: matcher lowercases it
        rows_out.append((i, v))

    if job:
        job.phase(f"writing {len(rows_out)} rows")

    if errors:
        raise ValueError("Fix invalid rows before importing; no records were written.")
    boundary.check_rows(client, purpose, role, rows_out)
    if purpose == "incoming" and mode != "upsert":
        raise ValueError("New receipts are added without replacing a historical date window.")
    written = _write_rows(client, role, table, rows_out, alloc, mode, date_from, date_to, boundary.cutoff(client) if purpose == "incoming" else None)
    alloc.commit()

    report = {"import_id": f"imp_{uuid.uuid4().hex[:10]}", "client": client, "role": role,
              "purpose": purpose, "table": table, "filename": up["filename"], "upload_id": upload_id,
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "mode": mode, "rows_in_file": len(parsed["rows"]),
              "inserted": written["inserted"], "updated": written["updated"],
              "skipped_out_of_window": skipped, "dropped_non_cash_accounts": dropped_accounts,
              "posted_at_defaulted": posted_at_filled,
              "unresolved_references": written["unresolved"],
              "errors": errors, "ids": written["ids"], "warnings": []}

    if posted_at_filled:
        report["warnings"].append(
            f"{posted_at_filled} ledger rows had no posting timestamp, so the transaction date was "
            "used. Entries posted after the books closed cannot be recognised, and no rule about "
            "late posting can be learned.")
    if written["unresolved"]:
        report["warnings"].append(
            f"{len(written['unresolved'])} rows referred to a bank line or ledger entry that is not "
            "imported yet. Import the statement and ledger first, then this file again.")
    if dropped_accounts:
        report["warnings"].append(
            f"{dropped_accounts} rows were outside the cash accounts you named and were left out.")

    (manifest_dir(client) / f"{report['import_id']}.json").write_text(
        json.dumps(report, indent=1))
    return report


def _write_rows(client: str, role: str, table: str, rows, alloc, mode, date_from, date_to, training_before=None) -> dict:
    con = db.connect(client, readonly=False)
    inserted = updated = 0
    written_ids, unresolved = [], []
    try:
        if mode == "replace_window" and date_from and date_to:
            col = "reconciled_at" if table == "reconcile_link" else "date"
            con.execute(f"DELETE FROM {table} WHERE {col} BETWEEN ? AND ?", (date_from, date_to))
        for i, v in rows:
            built = _build(client, role, table, v, alloc, con)
            if built is None:
                unresolved.append({"row": i + 1, "ref": v.get("bank_ref") or v.get("subject_ref")})
                continue
            rid, values = built
            if training_before:
                old = con.execute(f"SELECT date FROM {table} WHERE id=?", (rid,)).fetchone()
                if old and old[0][:7] < training_before:
                    raise ValueError("A new upload cannot overwrite a record from your training history.")
            existing = con.execute(f"SELECT 1 FROM {table} WHERE id=?", (rid,)).fetchone()
            cols = TABLE_COLUMNS[table]
            con.execute(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * len(cols))})",
                        values)
            written_ids.append(rid)
            if existing:
                updated += 1
            else:
                inserted += 1
        con.commit()
    finally:
        con.close()
    return {"inserted": inserted, "updated": updated, "ids": written_ids, "unresolved": unresolved}


def _build(client: str, role: str, table: str, v: dict, alloc, con):
    """Assemble one row in the table's column order. Returns None when a reference cannot be
    resolved, which is reported rather than written as a dangling id."""
    P = contract.period_of

    if table == "bank_line":
        rid, _ = alloc.allocate(table, v, v.get("source_id"))
        return rid, (rid, P(v["date"]), v["date"], v["amount"], v.get("description", ""),
                     v.get("counterparty", ""), v.get("ref", ""))

    if table == "ledger_entry":
        rid, _ = alloc.allocate(table, v, v.get("source_id"))
        return rid, (rid, P(v["date"]), v["date"], v["posted_at"], v.get("account", ""),
                     v["amount"], v.get("memo", ""), v.get("counterparty", ""),
                     v.get("ref", ""), v.get("invoice_id") or None)

    if table == "invoice":
        rid = v.get("id") or alloc.allocate(table, v)[0]
        return rid, (rid, v.get("party", ""), v.get("direction", "AR"), v["date"],
                     v.get("due_date") or v["date"], v["amount"], v.get("terms", ""),
                     v.get("status", "open"))

    if table == "document":
        rid, _ = alloc.allocate(table, v)
        meta = {"period": P(v["date"])}          # internal mail is selected on meta.period
        if v.get("party"):
            meta["party"] = v["party"]
        if v.get("batch_id"):
            meta["batch_id"] = v["batch_id"]
        return rid, (rid, v.get("type", "email"), v["date"], v.get("sender", ""),
                     v.get("subject", ""), v.get("body", ""), json.dumps(meta))

    if table == "reconcile_link":
        bank_id = alloc.lookup("bank_line", v.get("bank_ref", "")) or _find_ref(con, "bank_line", v.get("bank_ref"))
        ledger_id = alloc.lookup("ledger_entry", v.get("ledger_ref", "")) or _find_ref(con, "ledger_entry", v.get("ledger_ref"))
        if not bank_id or not ledger_id:
            return None
        bank = db.q(con, "SELECT period FROM bank_line WHERE id=?", bank_id)
        led = db.q(con, "SELECT amount FROM ledger_entry WHERE id=?", ledger_id)
        if not bank or not led:
            return None
        rid, _ = alloc.allocate(table, {"bank_ref": bank_id, "ledger_ref": ledger_id})
        return rid, (rid, bank[0]["period"], bank_id, ledger_id, led[0]["amount"],
                     v.get("reconciled_by", ""), v.get("reconciled_at", ""),
                     v.get("undone_at") or None)

    if table == "journal_entry":
        bank_id = alloc.lookup("bank_line", v.get("bank_ref", "")) or _find_ref(con, "bank_line", v.get("bank_ref"))
        if not bank_id:
            return None
        bank = db.q(con, "SELECT period, date FROM bank_line WHERE id=?", bank_id)
        if not bank:
            return None
        rid, _ = alloc.allocate(table, v | {"bank_ref": bank_id})
        when = v.get("date") or bank[0]["date"]
        return rid, (rid, bank[0]["period"], when, v.get("posted_at") or when,
                     v.get("posted_by", ""), v.get("account", ""), v["amount"],
                     v.get("memo", ""), bank_id,
                     alloc.lookup("journal_entry", v.get("reverses_ref", "")) or None)

    if table == "approval":
        ref = v.get("subject_ref", "")
        subject_id = (alloc.lookup("bank_line", ref) or alloc.lookup("ledger_entry", ref)
                      or _find_ref(con, "bank_line", ref) or _find_ref(con, "ledger_entry", ref))
        if not subject_id:
            return None
        kind = "bank_line" if "-BL-" in subject_id else "ledger_entry"
        rows = db.q(con, f"SELECT period FROM {kind} WHERE id=?", subject_id)
        rid, _ = alloc.allocate(table, v | {"subject_ref": subject_id})
        return rid, (rid, rows[0]["period"] if rows else P(v["date"]), v["date"],
                     v.get("subject") or kind, subject_id, v.get("requested_by", ""),
                     v.get("approver", ""), v.get("status", "approved"), v.get("comment", ""))
    return None


def _find_ref(con, table: str, ref):
    """Second chance for a reference: the record's own ref column."""
    if not ref:
        return None
    rows = db.q(con, f"SELECT id FROM {table} WHERE id=? OR ref=? LIMIT 2", str(ref), str(ref))
    return rows[0]["id"] if len(rows) == 1 else None


def _import_chart(client: str, parsed, mapping, up) -> dict:
    cols = mapping.get("columns", {})
    chart = {}
    for row in parsed["rows"]:
        code = contract.to_text(row.get(cols.get("account", ""), ""))
        name = contract.to_text(row.get(cols.get("name", ""), ""))
        if code:
            chart[code] = name or code
    con = db.connect(client, readonly=False)
    try:
        con.execute("UPDATE client SET chart=?", (json.dumps(chart),))
        con.commit()
    finally:
        con.close()
    return {"import_id": f"imp_{uuid.uuid4().hex[:10]}", "client": client, "role": "chart_of_accounts",
            "table": "client.chart", "filename": up["filename"], "inserted": len(chart),
            "updated": 0, "errors": [], "warnings": [], "ids": [], "accounts": len(chart),
            "rows_in_file": len(parsed["rows"]), "skipped_out_of_window": 0,
            "unresolved_references": []}


def _import_people(client: str, parsed, mapping, up, dayfirst) -> dict:
    cols = mapping.get("columns", {})
    people = []
    for row in parsed["rows"]:
        pid = contract.to_text(row.get(cols.get("id", ""), ""))
        if not pid:
            continue
        people.append({"id": pid,
                       "name": contract.to_text(row.get(cols.get("name", ""), "")) or pid,
                       "role": contract.to_text(row.get(cols.get("role", ""), "")) or "staff",
                       "senior": contract.to_bool(row.get(cols.get("senior", ""), ""))})
    warnings = []
    if not any(p["senior"] for p in people):
        warnings.append("nobody in this file is marked senior. Escalations are addressed to a "
                        "senior role, so mark at least one person before inducing a playbook.")
    con = db.connect(client, readonly=False)
    try:
        for p in people:
            con.execute("INSERT OR REPLACE INTO user VALUES (?,?,?,?)",
                        (p["id"], p["name"], p["role"], 1 if p["senior"] else 0))
        con.commit()
    finally:
        con.close()
    return {"import_id": f"imp_{uuid.uuid4().hex[:10]}", "client": client, "role": "people",
            "table": "user", "filename": up["filename"], "inserted": len(people), "updated": 0,
            "errors": [], "warnings": warnings, "ids": [p["id"] for p in people],
            "rows_in_file": len(parsed["rows"]), "skipped_out_of_window": 0,
            "unresolved_references": []}


def undo(client: str, import_id: str) -> dict:
    """Remove exactly what one import wrote, unless the playbook or a run now depends on it."""
    path = manifest_dir(client) / f"{import_id}.json"
    if not path.exists():
        raise ValueError("no such import")
    report = json.loads(path.read_text())
    table, rec_ids = report.get("table"), report.get("ids") or []
    if not table or table in ("client.chart", "user"):
        raise ValueError("this import cannot be undone automatically")
    cited = _cited_by_playbook_or_runs(client, set(rec_ids))
    if cited:
        raise ValueError("cannot undo: the playbook or a saved run cites "
                         f"{len(cited)} of these records, for example {sorted(cited)[:3]}")
    con = db.connect(client, readonly=False)
    try:
        for rid in rec_ids:
            con.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
        con.commit()
    finally:
        con.close()
    path.rename(path.with_suffix(".undone.json"))
    return {"import_id": import_id, "deleted": len(rec_ids), "table": table}


def _cited_by_playbook_or_runs(client: str, rec_ids: set[str]) -> set[str]:
    from shadow import playbook as pbmod
    hit: set[str] = set()
    for track_dir in (db.DATA / client / "playbook").glob("*"):
        vs = pbmod.versions(client, track_dir.name)
        if not vs:
            continue
        pb = pbmod.load(client, track_dir.name)
        for r in pb.get("rules", []):
            hit |= rec_ids & set(r.get("precedent_ids") or [])
            for b in (r.get("bands") or {}).values():
                hit |= rec_ids & {b.get("lo_precedent"), b.get("hi_precedent")}
    for meta in db.RUNS.glob("*/resolutions.jsonl"):
        try:
            for line in meta.read_text().splitlines():
                if any(rid in line for rid in list(rec_ids)[:200]):
                    hit |= {rid for rid in rec_ids if rid in line}
        except OSError:
            continue
    return {h for h in hit if h}
