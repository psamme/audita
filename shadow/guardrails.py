"""Controls that run before any matching and that no playbook can switch off.

A flagged bank line is never matched by the matcher and never resolved by a playbook rule, even when the amount
ties exactly. It goes to the investigator with the flag attached. Hard flags are escalated whatever the
investigator thinks; soft flags let it read the evidence and decide, but never on the strength of the amount alone.
"""
import re
from datetime import date

from shadow import db
from shadow.matcher import tokens

ACCT = re.compile(r"ACCT\s*\*?(\d{3,})", re.I)
BANK_CHANGE = re.compile(r"(bank|account)[^.\n]{0,60}(chang|new |updat|moved|switch|effective)|(chang|new |updat|moved|switch)[^.\n]{0,60}(bank|account)", re.I)
CHANGE_WINDOW_DAYS, DUPLICATE_WINDOW_DAYS = 45, 10


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def scan(con, period: str) -> dict[str, list[dict]]:
    flags: dict[str, list[dict]] = {}
    out_all = db.q(con, "SELECT * FROM bank_line WHERE period <= ? AND amount < 0 ORDER BY date, id", period)
    reconciled = {r["bank_id"] for r in db.q(con, "SELECT bank_id FROM reconcile_link WHERE period < ? AND undone_at IS NULL", period)}

    # 1. payee account token differs from every earlier payment to the same counterparty (hard)
    seen: dict[str, set[str]] = {}
    for b in out_all:
        m = ACCT.search(b["description"])
        if not m or not b["counterparty"]:
            continue
        known = seen.setdefault(b["counterparty"], set())
        new = bool(known) and m.group(1) not in known
        if new and b["period"] == period:
            flags.setdefault(b["id"], []).append({
                "flag": "payee_bank_details_changed",
                "detail": f"paid to account ending {m.group(1)}; every earlier verified payment to {b['counterparty']} went to {', '.join(sorted(known))}"})
        if not new or b["id"] in reconciled:     # an unfamiliar account only becomes known once a payment to it was cleared by the client
            known.add(m.group(1))

    # 2. a bank-detail change request is on file for the payee: the first payment after it is never auto-cleared (soft)
    requests = [d for d in db.q(con, "SELECT * FROM document WHERE type != 'internal_email' ORDER BY date")
                if BANK_CHANGE.search(f"{d['subject']} {d['body']}")]
    memos = [e for e in db.q(con, "SELECT * FROM ledger_entry WHERE period <= ? AND amount < 0", period) if BANK_CHANGE.search(e["memo"] or "")]
    for d in requests:
        who = tokens(d["meta"].get("party", ""), d["sender"].split("@")[-1].split(".")[0], d["subject"])
        after = [b for b in out_all if b["counterparty"] and tokens(b["counterparty"]) & who and 0 <= _days(b["date"], d["date"]) <= CHANGE_WINDOW_DAYS]
        if after and after[0]["period"] == period:
            flags.setdefault(after[0]["id"], []).append({
                "flag": "bank_change_request_on_file",
                "detail": f"first payment to {after[0]['counterparty']} since {d['id']} ({d['date']}, \"{d['subject']}\") asked for a change of bank details"})
    for e in memos:
        for b in out_all:
            if b["period"] == period and b["amount"] == e["amount"] and tokens(b["counterparty"]) & tokens(e["counterparty"]) and abs(_days(b["date"], e["date"])) <= 10:
                flags.setdefault(b["id"], []).append({"flag": "bank_change_request_on_file",
                                                      "detail": f"ledger entry {e['id']} for this payment mentions new bank details: \"{e['memo']}\""})

    # 3. the same outgoing payment twice: same payee and amount within ten days, and the business only issued one (soft)
    issued = db.q(con, "SELECT * FROM ledger_entry WHERE period <= ? AND amount < 0", period)
    recent = [b for b in out_all if b["counterparty"]]
    for i, a in enumerate(recent):
        twins = [b for b in recent[i + 1:] if b["amount"] == a["amount"] and b["counterparty"] == a["counterparty"]
                 and _days(b["date"], a["date"]) <= DUPLICATE_WINDOW_DAYS]
        if not twins:
            continue
        group = [a] + twins
        ledger_side = [e for e in issued if e["amount"] == a["amount"] and tokens(e["counterparty"]) & tokens(a["counterparty"])
                       and abs(_days(a["date"], e["date"])) <= DUPLICATE_WINDOW_DAYS + 5]
        if not ledger_side or len(ledger_side) >= len(group):
            continue          # bank-originated charges, or as many payments issued as cleared: nothing was paid twice
        for x in group:
            if x["period"] == period and not any(f["flag"] == "possible_duplicate_payment" for f in flags.get(x["id"], [])):
                other = next(y for y in group if y is not x)
                flags.setdefault(x["id"], []).append({
                    "flag": "possible_duplicate_payment",
                    "detail": f"{len(group)} payments of this amount to {x['counterparty']} within {DUPLICATE_WINDOW_DAYS} days (e.g. {other['id']} on {other['date']}) "
                              f"but only {len(ledger_side)} issued in the ledger"})
    return flags
