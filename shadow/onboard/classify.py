"""Work out what each dropped file is, before anyone has to tell us.

A finance team does not export one file at a time with our names on it. They hand over a folder.
This reads a file's header and the sample rows `staging.profile` already collected and decides
which of the nine kinds it is, reusing the per-field column matching the mapping step does.

Coverage comes first: a file cannot be a bank statement if nothing in it reads as an amount, so a
kind whose required fields are not all present is out, whatever else matches. Among the kinds that
do fit, the winner is the one that explains the most of the file with the least stretched
matches, and required fields count for more when few kinds ask for them. A column literally named
`memo` is much better evidence of a ledger than of a bank statement, even though our bank
statement would accept it as a description.

A close call is reported as a close call. The screen shows the runner-up and the person picks,
which is faster than spotting a plausible mistake after the import.
"""
import json
import re

from shadow import llm
from shadow.onboard import contract, mapping

# Parents before children. Reconciliations, adjustments and approvals resolve references against
# bank lines and ledger entries, so importing them first leaves every row dangling.
ORDER = ("chart_of_accounts", "people", "bank_lines", "ledger_entries", "invoices", "documents",
         "reconciliations", "adjustments", "approvals")

# What finance teams actually call these files. Worth a nudge, never enough to outvote the columns.
FILENAME_HINTS: dict[str, tuple[str, ...]] = {
    "bank_lines": ("bank", "statement", "stmt", "transactions", "activity"),
    "ledger_entries": ("ledger", "gl", "clearing", "subledger", "entries"),
    "reconciliations": ("reconciliation", "reconciliations", "reconciled", "recon", "matches", "cleared"),
    "adjustments": ("adjustment", "adjustments", "adjusting", "writeoff", "writeoffs", "je"),
    "approvals": ("approval", "approvals", "approved", "signoff", "authorisations", "authorizations"),
    "invoices": ("invoice", "invoices", "billing", "bills", "ar", "ap"),
    "documents": ("document", "documents", "email", "emails", "remittance", "remittances", "advice", "mail"),
    "people": ("people", "users", "staff", "roster", "employees", "team"),
    "chart_of_accounts": ("chart", "coa", "accounts"),
}

MIN_FIT = 0.55       # below this nothing is proposed, and the file waits for a person
MARGIN = 0.12        # two kinds this close are both shown rather than one being guessed
TIE = 0.02           # only inside this can the file name decide anything

# Ids and cross-references are the one thing every export has and every kind's hints reach for, so
# an unused column matching one of them says nothing about what the file is.
_NOT_A_SIGNAL = {"id", "source_id", "bank_ref", "ledger_ref", "subject_ref", "reverses_ref", "ref"}

_WORD = re.compile(r"[a-z0-9]+")
_SEP = re.compile(r"[_\-.]+")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", _SEP.sub(" ", str(name).lower())).strip()


def _weight(field: str) -> float:
    """How much a required field narrows things down. `reconciled_at` names one kind; `date` names
    six, so matching it is almost no evidence at all."""
    n = sum(field in spec["required"] for spec in contract.SPECS.values())
    return 1.0 / max(1, n)


def _quality(field: str, column: str) -> float:
    """How good the evidence for one pairing is: the field's own name, a known synonym, or a
    token overlap the mapping step was willing to accept."""
    name = _norm(column)
    if name == field or name == _norm(field):
        return 1.0
    if name in mapping.HINTS.get(field, []):
        return 0.75
    return 0.5


def _filename_matches(role: str, filename: str) -> bool:
    tokens = set(_WORD.findall(str(filename).lower()))
    return bool(tokens & set(FILENAME_HINTS.get(role, ())))


def _orphans(role: str, columns: list[dict], used: set[str]) -> list[str]:
    """Columns this kind has nowhere to put, which another kind requires by name.

    This is what separates a ledger from a statement. Both have a date, an amount and a line of
    text, and our bank statement will happily read a column named `memo` as its description. What
    it cannot do is hold a GL account code, so a file with one is not a statement.
    """
    mine = set(contract.fields_for(role))
    out = []
    for col in columns:
        if col["name"] in used:
            continue
        name = _norm(col["name"])
        for other, spec in contract.SPECS.items():
            if other == role:
                continue
            hit = next((f for f in spec["required"]
                        if f not in mine and f not in _NOT_A_SIGNAL
                        and (name == f or name in mapping.HINTS.get(f, []))), None)
            if hit:
                out.append(hit)
                break
    return out


def score(role: str, columns: list[dict], filename: str = "") -> dict:
    """How well one kind explains this file. Returns the mapping it would use, so a caller that
    accepts the answer does not have to guess the columns a second time."""
    guess = mapping.guess(role, columns)
    cols = guess["columns"]
    spec = contract.SPECS[role]
    required = [f for f in spec["required"] if f not in spec["derived"]]
    by_pair = contract.has_amount(guess) and not cols.get("amount")
    missing = [f for f in required if f not in cols and not (f == "amount" and by_pair)]

    used = set(cols.values())
    sign = guess.get("sign") or {}
    for key in ("debit_column", "credit_column", "flag_column"):
        if sign.get(key):
            used.add(sign[key])

    total = sum(_weight(f) for f in required) or 1.0
    got = 0.0
    evidence: list[str] = []
    for f in required:
        if f in cols:
            got += _weight(f) * _quality(f, cols[f])
            evidence.append(cols[f] if _norm(cols[f]) != f else f)
        elif f == "amount" and by_pair:
            got += _weight(f) * 0.75
            evidence.append(f"{sign.get('debit_column')}/{sign.get('credit_column')}")
    cover = len(used) / max(1, len(columns))
    orphans = _orphans(role, columns, used)
    penalty = min(0.3, 0.25 * sum(_weight(f) for f in orphans))
    fit = 0.0 if missing else max(0.0, 0.7 * (got / total) + 0.3 * cover - penalty)
    return {"role": role, "label": spec["label"], "fit": round(min(1.0, fit), 4),
            "missing": missing, "mapped": len(used), "columns": len(columns),
            "evidence": evidence, "orphans": orphans,
            "filename": _filename_matches(role, filename), "mapping": guess}


def classify(columns: list[dict], filename: str = "", allowed: set[str] | None = None) -> dict:
    """Rank every kind against one file. `allowed` narrows the field for new receipts, where
    reconciliation history and approvals are not permitted at all."""
    roles = [r for r in ORDER if allowed is None or r in allowed]
    # The columns decide. A file called "bank statement january" that carries a GL account code is
    # a ledger somebody misnamed, so the name only settles a fit the content left tied.
    ranked = sorted((score(r, columns, filename) for r in roles),
                    key=lambda s: (-round(s["fit"] / TIE), -s["filename"], -s["fit"]))
    best = ranked[0] if ranked else None
    runner = ranked[1] if len(ranked) > 1 else None
    close = bool(runner and runner["fit"] >= MIN_FIT and best["fit"] - runner["fit"] < MARGIN)

    if not best or best["fit"] < MIN_FIT:
        return {"role": None, "confidence": 0.0, "close": False, "source": "columns",
                "why": _no_idea(ranked), "alternatives": [r["role"] for r in ranked[:3]],
                "mapping": None, "ranked": _trim(ranked)}
    return {"role": best["role"], "label": best["label"],
            "confidence": round(best["fit"] * (0.6 if close else 1.0), 2), "close": close,
            "source": "columns", "why": _why(best, runner if close else None),
            "alternatives": [r["role"] for r in ranked[1:3] if r["fit"] >= MIN_FIT],
            "mapping": best["mapping"], "ranked": _trim(ranked)}


def _trim(ranked: list[dict]) -> list[dict]:
    return [{k: r[k] for k in ("role", "label", "fit", "missing", "mapped", "columns", "orphans")}
            for r in ranked[:4]]


def _why(best: dict, close: dict | None) -> str:
    named = ", ".join(best["evidence"][:4]) or "no named columns"
    base = (f"{named} match {best['label'].lower()}, and {best['mapped']} of {best['columns']} "
            f"columns in the file are used")
    if close:
        return f"{base}. It reads almost as well as {close['label'].lower()}, so check this one."
    return base + "."


def _no_idea(ranked: list[dict]) -> str:
    if not ranked:
        return "Nothing to compare this against."
    near = min(ranked, key=lambda r: len(r["missing"]) if r["missing"] else 99)
    if near["missing"]:
        return (f"Closest is {near['label'].lower()}, but nothing in this file reads as "
                + ", ".join(near["missing"]) + ".")
    return "No kind explains enough of this file to propose one."


# --- the model, only for the files the columns could not place -------------------------------
KIND_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["kind", "confidence", "why"],
    "properties": {"kind": {"type": ["string", "null"], "enum": list(ORDER) + [None]},
                   "confidence": {"type": "number"}, "why": {"type": "string"}},
}

SYSTEM = """You identify what kind of export a finance team has handed over, for a bank \
reconciliation system. You are given the file name, its header, and a few real values from each \
column. You are not mapping columns and you are not transforming data. You name the kind.

The kinds, and what makes each one itself:
- bank_lines: the bank statement. Dates, amounts, and text describing each movement.
- ledger_entries: the cash-clearing subset of the general ledger. Like the statement, but every \
row carries a GL account code.
- reconciliations: which bank line cleared which ledger entry, who did it and when. Two \
reference columns pointing at other files, and no amounts of its own.
- adjustments: write-offs, fees and short-pay differences. A bank reference, an account, an \
amount and who posted it.
- approvals: who approved what, and the outcome.
- invoices: invoice number, date, amount, and usually a customer or supplier.
- documents: emails and remittance advices. Free text, often long.
- people: the finance team. Names, roles, and who is senior enough to approve.
- chart_of_accounts: account codes and their names. No dates, no amounts.

Return null for kind when the file is none of these, or when two are genuinely indistinguishable \
from what you can see. A wrong name is worse than an admission, because the person reviewing your \
answer can place an unplaced file faster than they can spot a plausible mistake.

confidence is your honest 0 to 1 that a reviewer would accept the name."""


def propose(columns: list[dict], filename: str = "", allowed: set[str] | None = None,
            usage=None) -> dict:
    """One model call for one file. Falls back to the column ranking if it cannot be parsed, and
    never returns a kind the column ranking has ruled out on required fields."""
    fallback = classify(columns, filename, allowed)
    ask = {"file_name": filename,
           "columns": [{"name": c["name"], "examples": c["samples"][:4],
                        "looks_like": c["inferred"], "empty_rows": c["nulls"]} for c in columns]}
    try:
        reply = llm.call(SYSTEM, [{"role": "user", "content": json.dumps(ask, indent=1)}],
                         schema=KIND_SCHEMA, max_tokens=1000, usage=usage)
        raw = json.loads(reply.text)
    except Exception as e:
        return fallback | {"source": "columns",
                           "why": f"{fallback['why']} The model was not available ({type(e).__name__})."}

    role = raw.get("kind")
    if role not in contract.SPECS or (allowed is not None and role not in allowed):
        return fallback | {"source": "model", "why": raw.get("why") or fallback["why"]}
    picked = score(role, columns, filename)
    if picked["missing"]:                  # the model named a kind this file cannot supply
        return fallback | {"source": "model",
                           "why": (f"The model read this as {picked['label'].lower()}, but the file "
                                   f"has no " + ", ".join(picked["missing"]) + ".")}
    return {"role": role, "label": picked["label"],
            "confidence": round(float(raw.get("confidence") or 0.6), 2), "close": False,
            "source": "model", "why": raw.get("why") or _why(picked, None),
            "alternatives": [r["role"] for r in fallback.get("ranked", [])
                             if r["role"] != role and not r["missing"]][:2],
            "mapping": picked["mapping"], "ranked": fallback.get("ranked", [])}
