"""Controls that run before any matching and that no playbook can switch off.

A flagged bank line is never auto-matched and never resolved by a playbook rule, even when the amount ties
exactly. It goes to the investigator with the flag attached, and the investigator may only clear or escalate it
with evidence on file.
"""
import re

from shadow import db

ACCT = re.compile(r"ACCT\s*\*?(\d{3,})", re.I)


def scan(con, period: str) -> dict[str, list[dict]]:
    flags: dict[str, list[dict]] = {}
    # 1. payee bank details differ from every earlier payment to the same counterparty
    seen: dict[str, set[str]] = {}
    for b in db.q(con, "SELECT * FROM bank_line WHERE period <= ? AND amount < 0 ORDER BY date, id", period):
        m = ACCT.search(b["description"])
        if not m or not b["counterparty"]:
            continue
        known = seen.setdefault(b["counterparty"], set())
        if known and m.group(1) not in known and b["period"] == period:
            flags.setdefault(b["id"], []).append({
                "flag": "payee_bank_details_changed",
                "detail": f"paid to account ending {m.group(1)}; every earlier payment to {b['counterparty']} went to {', '.join(sorted(known))}"})
        known.add(m.group(1))
    # 2. the same outgoing payment twice: same counterparty, amount and reference within ten days
    out = db.q(con, "SELECT * FROM bank_line WHERE period = ? AND amount < 0 ORDER BY date, id", period)
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            if (a["amount"] == b["amount"] and a["counterparty"] and a["counterparty"] == b["counterparty"]
                    and a["ref"] and a["ref"] == b["ref"] and abs(int(b["date"][8:]) - int(a["date"][8:])) <= 10):
                for x, y in ((a, b), (b, a)):
                    flags.setdefault(x["id"], []).append({
                        "flag": "possible_duplicate_payment",
                        "detail": f"same payee, amount and reference as {y['id']} on {y['date']}"})
    return flags
