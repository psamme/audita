"""Tier 0: deterministic matcher. No model calls. Abstains whenever a match is not mutually unique.

A wrong match is worse than an item left for review, so every ambiguity (two candidates, two claimants,
a counterparty that disagrees) sends the line to the next tier instead of guessing.
"""
import re
from datetime import date, timedelta
from itertools import combinations

from shadow import db

BEFORE, AFTER = 3, 21   # ledger entry may be dated up to 3 days after, or 21 days before, the bank line
BATCH_DAYS, BATCH_MAX = 5, 4


def period_end(period: str) -> str:
    y, m = map(int, period.split("-"))
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def open_ledger(con, period: str) -> list[dict]:
    """Ledger entries visible in this period that no earlier-period resolution has consumed."""
    used = set()
    for r in db.q(con, "SELECT ledger_ids FROM resolution WHERE period < ? AND item_kind='bank'", period):
        used.update(r["ledger_ids"])
    return [e for e in db.q(con, "SELECT * FROM ledger_entry WHERE period <= ? ORDER BY date, id", period)
            if e["id"] not in used]


def tokens(*parts: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", " ".join(p or "" for p in parts).lower())
            if t not in {"ach", "inc", "llc", "wire", "debit", "credit", "out", "the", "pmt", "payment"}}


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def compatible(bank: dict, entry: dict) -> bool:
    """Counterparties, when both sides name one, must share a token."""
    if bank["ref"] and bank["ref"] == entry["ref"]:
        return True
    b, e = tokens(bank["counterparty"]), tokens(entry["counterparty"])
    return not b or not e or bool(b & e)


def in_window(bank: dict, entry: dict) -> bool:
    return -BEFORE <= days_between(bank["date"], entry["date"]) <= AFTER


def run(con, period: str, skip: set[str] = frozenset()) -> tuple[list[dict], set[str]]:
    """Returns (matches, used_ledger_ids). Each match: {bank_id, ledger_ids, how}. Bank ids in `skip` are left alone."""
    bank = [b for b in db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date, id", period) if b["id"] not in skip]
    ledger = open_ledger(con, period)
    by_amount: dict[int, list[dict]] = {}
    for e in ledger:
        by_amount.setdefault(db.cents(e["amount"]), []).append(e)

    # one-to-one: candidate graph on exact amount, narrowed by reference when the reference is decisive
    cand: dict[str, list[str]] = {}
    for b in bank:
        pool = [e for e in by_amount.get(db.cents(b["amount"]), []) if in_window(b, e) and compatible(b, e)]
        same_ref = [e for e in pool if b["ref"] and e["ref"] == b["ref"]]
        cand[b["id"]] = [e["id"] for e in (same_ref or pool)]
    claims: dict[str, list[str]] = {}
    for bid, es in cand.items():
        for eid in es:
            claims.setdefault(eid, []).append(bid)
    matches, used = [], set()
    for b in bank:
        es = cand[b["id"]]
        if len(es) == 1 and len(claims[es[0]]) == 1:
            matches.append({"bank_id": b["id"], "ledger_ids": es, "how": "exact amount, unique candidate"})
            used.add(es[0])

    # many-to-one: a small set of same-family entries just before the bank date that sums to it, if unique
    matched = {m["bank_id"] for m in matches}
    contested = {eid for eid, bs in claims.items() if len(bs) > 1}
    for b in bank:
        if b["id"] in matched or cand[b["id"]]:
            continue
        pool = [e for e in ledger if e["id"] not in used and e["id"] not in contested
                and 0 <= days_between(b["date"], e["date"]) <= BATCH_DAYS
                and (e["amount"] > 0) == (b["amount"] > 0) and abs(e["amount"]) < abs(b["amount"])
                and (tokens(b["counterparty"]) & tokens(e["counterparty"]) if b["counterparty"] else True)]
        if not 2 <= len(pool) <= 14:
            continue
        target, hits = db.cents(b["amount"]), []
        for k in range(2, BATCH_MAX + 1):
            for combo in combinations(pool, k):
                if sum(db.cents(e["amount"]) for e in combo) == target and len({e["account"] for e in combo}) == 1:
                    hits.append(combo)
        if len(hits) == 1:
            ids = sorted(e["id"] for e in hits[0])
            matches.append({"bank_id": b["id"], "ledger_ids": ids, "how": f"{len(ids)} entries sum to the bank amount, unique combination"})
            used.update(ids)
    return matches, used
