"""Work out which of their columns is which of ours.

The model proposes; code decides. It sees the header and twenty sample rows, never the file, and
returns a mapping the user then confirms or corrects on screen. Applying the mapping is ordinary
Python, so a fifty-thousand-row import costs one model call and is reproducible.

There is also a deterministic guess for the obvious cases, which means the screen has something
to show before any call is made and the whole importer stays testable without a model.
"""
import json
import re

from shadow import llm
from shadow.onboard import contract

_WORD = re.compile(r"[a-z0-9]+")

# Column names finance systems actually use, per canonical field.
HINTS: dict[str, list[str]] = {
    "date": ["date", "transaction date", "value date", "posting date", "txn date", "booked"],
    "posted_at": ["posted at", "posted date", "entered", "created", "entry date", "posted on"],
    "amount": ["amount", "value", "net", "transaction amount", "gross"],
    "description": ["description", "details", "narrative", "particulars", "memo", "text", "payee"],
    "memo": ["memo", "description", "narrative", "note", "details"],
    "counterparty": ["counterparty", "payee", "customer", "vendor", "supplier", "name", "party"],
    "ref": ["ref", "reference", "cheque", "check", "document", "doc no", "transaction id"],
    "account": ["account", "gl account", "account code", "account no", "nominal"],
    "invoice_id": ["invoice", "invoice no", "invoice id", "bill"],
    "source_id": ["id", "transaction id", "line id", "unique id", "key"],
    "bank_ref": ["bank ref", "bank line", "statement line", "bank id", "bank transaction"],
    "ledger_ref": ["ledger ref", "ledger id", "gl line", "entry id", "journal line"],
    "reconciled_by": ["reconciled by", "cleared by", "matched by", "user", "prepared by"],
    "reconciled_at": ["reconciled at", "cleared date", "matched on", "reconciled on"],
    "posted_by": ["posted by", "entered by", "created by", "user"],
    "approver": ["approver", "approved by", "authorised by", "authorized by"],
    "requested_by": ["requested by", "raised by", "submitted by"],
    "status": ["status", "state", "outcome", "decision"],
    "subject_ref": ["subject", "item", "bank ref", "reference", "applies to"],
    "comment": ["comment", "note", "reason"],
    "id": ["id", "code", "user id", "employee id", "email"],
    "name": ["name", "full name", "person", "description"],
    "role": ["role", "title", "job title", "position"],
    "senior": ["senior", "approver", "is senior", "can approve"],
    "party": ["party", "customer", "vendor", "supplier", "name"],
    "direction": ["direction", "type", "ar ap", "kind"],
    "due_date": ["due date", "due", "maturity"],
    "terms": ["terms", "payment terms"],
    "type": ["type", "kind", "document type", "category"],
    "body": ["body", "text", "content", "message"],
    "subject": ["subject", "title", "summary"],
    "sender": ["sender", "from", "from address", "email"],
    "batch_id": ["batch", "batch id", "payout id", "settlement id"],
    "undone_at": ["undone", "unmatched at", "reversed at"],
    "reverses_ref": ["reverses", "reversal of", "reverses entry"],
}


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(str(s).lower()))


def guess(role: str, columns: list[dict]) -> dict:
    """Deterministic first pass: exact and token-overlap matches, plus inferred types."""
    fields = contract.fields_for(role)
    by_name = {c["name"]: c for c in columns}
    taken: set[str] = set()
    out: dict[str, str] = {}
    for field in fields:
        best, best_score = None, 0.0
        for col in columns:
            if col["name"] in taken:
                continue
            name = col["name"].lower().strip()
            score = 0.0
            if name == field:
                score = 1.0
            elif name in HINTS.get(field, []):
                score = 0.95
            else:
                ct, ft = _tokens(name), _tokens(field) | set().union(
                    *[_tokens(h) for h in HINTS.get(field, [])] or [set()])
                if ct and ft:
                    score = 0.7 * len(ct & ft) / len(ct | ft)
            want = {"date": "date", "posted_at": "date", "reconciled_at": "date",
                    "due_date": "date", "amount": "amount"}.get(field)
            if want and col["inferred"] == want:
                score += 0.15
            elif want and col["inferred"] not in (want, "empty"):
                score -= 0.3
            if score > best_score:
                best, best_score = col["name"], score
        if best and best_score >= 0.5:
            out[field] = best
            taken.add(best)
    return {"columns": out, "sign": _guess_sign(by_name, columns, out)}


def _guess_sign(by_name: dict, columns: list[dict], cols: dict) -> dict:
    names = {c["name"].lower(): c["name"] for c in columns}
    debit = next((names[n] for n in names if n in ("debit", "dr", "debit amount", "withdrawal", "paid out")), None)
    credit = next((names[n] for n in names if n in ("credit", "cr", "credit amount", "deposit", "paid in")), None)
    if debit and credit:
        return {"mode": "debit_credit", "debit_column": debit, "credit_column": credit,
                "credit_is_money_in": True}
    flag = next((c["name"] for c in columns if c["inferred"] == "dr_cr_flag"), None)
    if flag:
        return {"mode": "flag", "flag_column": flag, "flag_money_out": ["DR", "D", "DEBIT"]}
    return {"mode": "signed", "invert": False}


MAPPING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["columns", "sign", "notes"],
    "properties": {
        "columns": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["field", "source", "confidence", "why"],
            "properties": {"field": {"type": "string"}, "source": {"type": ["string", "null"]},
                           "confidence": {"type": "number"}, "why": {"type": "string"}}}},
        "sign": {"type": "object", "additionalProperties": False, "required": ["mode"],
                 "properties": {
                     "mode": {"type": "string", "enum": list(contract.SIGN_MODES)},
                     "invert": {"type": "boolean"},
                     "debit_column": {"type": ["string", "null"]},
                     "credit_column": {"type": ["string", "null"]},
                     "credit_is_money_in": {"type": "boolean"},
                     "flag_column": {"type": ["string", "null"]},
                     "flag_money_out": {"type": "array", "items": {"type": "string"}}}},
        "dayfirst": {"type": ["boolean", "null"]},
        "notes": {"type": "string"}},
}

SYSTEM = """You map a finance export onto a fixed set of columns for a bank reconciliation system.

You are given the name of the kind of file, the target fields, and the export's header with a few \
real values from each column. Decide which source column supplies each target field.

Rules:
- Only ever name a column that appears in the header. Use null when nothing fits; a wrong guess \
is worse than an admission, because the person reviewing your answer can fill a gap faster than \
they can spot a plausible mistake.
- Money in is positive and money out is negative. Say how the file expresses that. A single \
signed column is `signed`; separate debit and credit columns are `debit_credit`; one amount \
column plus a DR/CR indicator is `flag`. If the file is signed the other way round (outgoing \
shown positive), set invert true.
- dayfirst matters only for dates written with slashes. Set it true for day/month/year, false for \
month/day/year, null if the sample cannot tell.
- confidence is your honest 0 to 1 that a reviewer would accept the pairing.
- why is a short phrase naming the evidence, such as "header says Paid Out and every value is \
positive".

Do not transform any data. You are choosing columns, nothing else."""


def propose(role: str, columns: list[dict], usage=None) -> dict:
    """One model call. Falls back to the deterministic guess if it cannot be parsed."""
    spec = contract.SPECS[role]
    fallback = guess(role, columns)
    ask = {"file_kind": role, "what_it_is": spec["note"],
           "target_fields": [{"field": f, "required": f in spec["required"]}
                             for f in contract.fields_for(role)],
           "export_columns": [{"name": c["name"], "examples": c["samples"][:5],
                               "looks_like": c["inferred"], "empty_rows": c["nulls"]}
                              for c in columns]}
    try:
        reply = llm.call(SYSTEM, [{"role": "user", "content": json.dumps(ask, indent=1)}],
                         schema=MAPPING_SCHEMA, max_tokens=4000, usage=usage)
        raw = json.loads(reply.text)
    except Exception as e:
        return fallback | {"source": "heuristic", "notes": f"model unavailable ({type(e).__name__}); "
                           "showing a rule-based guess for you to correct"}
    valid = {c["name"] for c in columns}
    cols = {c["field"]: c["source"] for c in raw.get("columns", [])
            if c.get("source") in valid and c.get("field") in contract.fields_for(role)}
    conf = {c["field"]: c.get("confidence") for c in raw.get("columns", [])}
    why = {c["field"]: c.get("why") for c in raw.get("columns", [])}
    sign = raw.get("sign") or fallback["sign"]
    for key in ("debit_column", "credit_column", "flag_column"):
        if sign.get(key) and sign[key] not in valid:
            sign = fallback["sign"]
            break
    for field, col in fallback["columns"].items():     # keep anything the model left out
        cols.setdefault(field, col)
    return {"columns": cols, "sign": sign, "dayfirst": raw.get("dayfirst"),
            "confidence": conf, "why": why, "notes": raw.get("notes", ""), "source": "model"}


def validate(role: str, mapping: dict, parsed: dict, limit: int = 200) -> dict:
    """Apply the mapping to real rows and report what breaks. No model, no writes."""
    spec = contract.SPECS[role]
    cols = mapping.get("columns", {})
    header = set(parsed["header"])
    errors: list[dict] = []
    missing = [f for f in spec["required"]
               if f not in cols and f not in spec["derived"]
               and not (f == "amount" and contract.has_amount(mapping))]
    unknown = [f"{f} -> {c}" for f, c in cols.items() if c not in header]

    dayfirst = mapping.get("dayfirst")
    if dayfirst is None and cols.get("date"):
        dayfirst = contract.sniff_dayfirst([r.get(cols["date"], "") for r in parsed["rows"][:200]])
    ambiguous = dayfirst is None and bool(cols.get("date")) and any(
        re.match(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$", str(r.get(cols["date"], "")).strip())
        for r in parsed["rows"][:50])

    preview, money_in, money_out, dates = [], 0, 0, []
    for i, row in enumerate(parsed["rows"][:limit]):
        out: dict = {}
        for field in contract.fields_for(role):
            src = cols.get(field)
            if not src and not (field == "amount" and contract.has_amount(mapping)):
                continue
            raw = row.get(src, "") if src else ""
            try:
                if field in ("date", "posted_at", "reconciled_at", "due_date", "undone_at"):
                    out[field] = contract.to_date(raw, field, dayfirst=bool(dayfirst))
                elif field == "amount":
                    out[field] = contract.resolve_amount(row, mapping)
                elif field == "senior":
                    out[field] = contract.to_bool(raw)
                else:
                    out[field] = contract.to_text(raw, field)
            except contract.Bad as e:
                if len(errors) < 50:
                    errors.append({"row": i + 1, "field": e.field, "value": str(e.value)[:60],
                                   "problem": e.problem})
        if "amount" in out:
            money_in += out["amount"] > 0
            money_out += out["amount"] < 0
        if "date" in out:
            dates.append(out["date"])
        if len(preview) < 8:
            preview.append(out)

    stats = {"rows": len(parsed["rows"]), "checked": min(limit, len(parsed["rows"])),
             "money_in": money_in, "money_out": money_out,
             "date_range": [min(dates), max(dates)] if dates else None,
             "dayfirst": dayfirst, "date_format_ambiguous": ambiguous}
    blocking = missing + [f"column not in this file: {u}" for u in unknown]
    if role == "bank_lines" and money_out == 0 and money_in > 0:
        stats["sign_warning"] = ("every amount is positive, so nothing reads as money leaving the "
                                 "account. If this file shows payments as positive, set invert.")
    return {"ok": not blocking and not errors, "blocking": blocking, "errors": errors,
            "preview": preview, "stats": stats}
