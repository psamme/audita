"""What a company has to give us, and how to turn their spreadsheet into our columns.

Two bars, not one. "Enough to run" is bank lines and ledger entries: the matcher and the
investigator work from those alone. "Enough to learn" additionally needs the reconciliation
trail, because the playbook is induced from what the humans did, not from what moved.

Money in is positive, money out is negative, everywhere.
"""
import re
from datetime import date

CURRENCY = re.compile(r"[^\d.,()\-+]")
THOUSANDS = re.compile(r",(?=\d{3}\b)")


class Bad(ValueError):
    """A row we cannot coerce. Carries the field so the report can point at a column."""

    def __init__(self, field: str, value, problem: str):
        super().__init__(f"{field}: {problem}")
        self.field, self.value, self.problem = field, value, problem


# --- coercion -------------------------------------------------------------------------------
def to_amount(raw, field: str = "amount") -> float:
    """Handle what finance exports actually contain: $1,234.56, (89.00), 1.234,56 , 12.00-"""
    if raw is None or str(raw).strip() == "":
        raise Bad(field, raw, "empty")
    s = str(raw).strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.endswith("-"):                      # trailing-minus, common from SAP and older ERPs
        negative, s = True, s[:-1]
    s = CURRENCY.sub("", s).strip()
    if not s:
        raise Bad(field, raw, "no digits")
    if "," in s and "." in s:                # 1.234,56 (European) vs 1,234.56
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else THOUSANDS.sub("", s)
    elif "," in s:
        # a lone comma is a decimal separator only when it is followed by one or two digits
        s = s.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}", s) else s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        raise Bad(field, raw, "not a number")
    return round(-v if negative else v, 2)


_DATE_PATTERNS = [
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")),
    ("%Y/%m/%d", re.compile(r"^\d{4}/\d{1,2}/\d{1,2}")),
    ("%d %b %Y", re.compile(r"^\d{1,2} [A-Za-z]{3,} \d{4}$")),
    ("%b %d, %Y", re.compile(r"^[A-Za-z]{3,} \d{1,2}, \d{4}$")),
]


def to_date(raw, field: str = "date", dayfirst: bool | None = None) -> str:
    """Return ISO. `dayfirst` resolves the ambiguous slash formats; it is decided per file by
    `sniff_dayfirst`, never guessed per row."""
    from datetime import datetime
    if raw is None or str(raw).strip() == "":
        raise Bad(field, raw, "empty")
    s = str(raw).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", s):        # drop a time component
        s = re.split(r"[T ]", s, 1)[0]
    for fmt, pat in _DATE_PATTERNS:
        if pat.match(s):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        d, mo = (a, b) if dayfirst else (b, a)
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            try:                              # the other reading, when only one is a real date
                return date(y, d, mo).isoformat()
            except ValueError:
                raise Bad(field, raw, "not a valid date either way round")
    raise Bad(field, raw, "unrecognised date format")


def sniff_dayfirst(values) -> bool | None:
    """True when the file must be day-first, False when it must be month-first, None when the
    sample cannot tell them apart. Ambiguity is surfaced to the user, not guessed."""
    day_only = month_only = False
    for v in values:
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", str(v).strip())
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            day_only = True
        if b > 12:
            month_only = True
    if day_only and not month_only:
        return True
    if month_only and not day_only:
        return False
    return None


def to_text(raw, field: str = "", default: str = "") -> str:
    return default if raw is None else str(raw).strip()


def to_bool(raw) -> bool:
    return str(raw).strip().lower() in ("1", "true", "t", "yes", "y", "senior", "x")


def period_of(iso: str) -> str:
    """Every `WHERE period < ?` in the codebase is a string compare, so this is always YYYY-MM."""
    return iso[:7]


# --- sign handling --------------------------------------------------------------------------
# The riskiest mapping decision in the whole importer. Get it backwards and nothing raises:
# guardrails stops seeing outgoing payments (it filters amount < 0) and the matcher's
# many-to-one pass stops firing, so the run just quietly gets worse.
SIGN_MODES = ("signed", "debit_credit", "flag")


def has_amount(mapping: dict) -> bool:
    """Amount is supplied either by a mapped column or by the debit/credit pair."""
    sign = mapping.get("sign") or {"mode": "signed"}
    if sign.get("mode") == "debit_credit":
        return bool(sign.get("debit_column") and sign.get("credit_column"))
    return bool((mapping.get("columns") or {}).get("amount"))


def resolve_amount(row: dict, mapping: dict, field: str = "amount") -> float:
    sign = mapping.get("sign") or {"mode": "signed"}
    mode = sign.get("mode", "signed")
    cols = mapping.get("columns", {})
    if mode == "debit_credit":
        dr = row.get(sign.get("debit_column", ""), "")
        cr = row.get(sign.get("credit_column", ""), "")
        d = to_amount(dr, "debit") if str(dr).strip() else 0.0
        c = to_amount(cr, "credit") if str(cr).strip() else 0.0
        if d and c:
            raise Bad(field, f"{dr}/{cr}", "both debit and credit are populated")
        v = c - d if sign.get("credit_is_money_in", True) else d - c
    elif mode == "flag":
        v = to_amount(row.get(cols.get(field, ""), ""), field)
        flag = str(row.get(sign.get("flag_column", ""), "")).strip().upper()
        out = flag in {x.upper() for x in sign.get("flag_money_out", ["DR", "D", "DEBIT"])}
        v = -abs(v) if out else abs(v)
    else:
        v = to_amount(row.get(cols.get(field, ""), ""), field)
        if sign.get("invert"):
            v = -v
    return round(v, 2)


# --- table specs ----------------------------------------------------------------------------
# `required` means the agent crashes or learns nothing without it. Anything listed in `derived`
# the importer computes; it is never mapped from a column.
SPECS: dict[str, dict] = {
    "chart_of_accounts": {
        "table": None, "label": "Chart of accounts", "learn": True,
        "required": ["account", "name"], "optional": [], "derived": [],
        "note": "Account codes the playbook is allowed to name."},
    "people": {
        "table": "user", "label": "People", "learn": True,
        "required": ["id", "name", "role", "senior"], "optional": [], "derived": [],
        "note": "At least one senior. Senior roles are the only addresses an escalation can use."},
    "bank_lines": {
        "table": "bank_line", "label": "Bank statement", "learn": True,
        "required": ["date", "amount", "description"],
        "optional": ["counterparty", "ref", "source_id"], "derived": ["period"],
        "note": "Money in positive, money out negative."},
    "ledger_entries": {
        "table": "ledger_entry", "label": "Cash-clearing ledger", "learn": True,
        "required": ["date", "account", "amount", "memo"],
        "optional": ["posted_at", "counterparty", "ref", "invoice_id", "source_id"],
        "derived": ["period"],
        "note": "The cash-clearing subset, not the whole general ledger. Importing every GL line "
                "fills the matcher with decoys and pushes almost everything to review."},
    "reconciliations": {
        "table": "reconcile_link", "label": "Reconciliation history", "learn": True,
        "required": ["bank_ref", "ledger_ref", "reconciled_by", "reconciled_at"],
        "optional": ["undone_at"], "derived": ["period", "amount"],
        "note": "Which bank line cleared which ledger entry, who did it and when. This is most "
                "of the learning signal."},
    "adjustments": {
        "table": "journal_entry", "label": "Adjusting entries", "learn": True,
        "required": ["bank_ref", "account", "amount", "posted_by", "posted_at"],
        "optional": ["memo", "reverses_ref", "date"], "derived": ["period"],
        "note": "Write-offs, fees and short-pay adjustments. Without the bank reference these are "
                "invisible and no rule will ever name an account."},
    "approvals": {
        "table": "approval", "label": "Approvals", "learn": False,
        "required": ["subject_ref", "approver", "status", "date"],
        "optional": ["requested_by", "comment", "subject"], "derived": ["period"],
        "note": "Optional but valuable: the clearest evidence of what needed a senior."},
    "invoices": {
        "table": "invoice", "label": "Invoices", "learn": False,
        "required": ["id", "date", "amount"],
        "optional": ["party", "direction", "due_date", "terms", "status"], "derived": [],
        "note": "Lets rules reason about invoice age and terms."},
    "documents": {
        "table": "document", "label": "Emails and remittances", "learn": False,
        "required": ["type", "date", "body"],
        "optional": ["sender", "subject", "party", "batch_id"], "derived": ["meta"],
        "note": "Use type 'internal_email' for internal mail; remittances and settlement reports "
                "get their own type."},
}

ROLES = tuple(SPECS)
LEARNING_ROLES = tuple(r for r, s in SPECS.items() if s["learn"])


def required_for(role: str) -> list[str]:
    return SPECS[role]["required"]


def fields_for(role: str) -> list[str]:
    s = SPECS[role]
    return s["required"] + s["optional"]
