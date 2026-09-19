"""Bank reconciliation desk. Matches bank lines to ledger cash entries: one-to-one, then one bank line to many entries.
Reads the bank's own words for any difference, finds ledger cash entries no bank line supports, and owns the month-end
statement: bank balance, plus and minus reconciling items, equals book cash.
"""
import json
import re
from itertools import combinations

from spine.tools import days_between

ORPHAN_DAYS = 5


def bank_side(card: dict) -> list[dict]:
    """What the bank line itself says about a difference. Independent of the cash application desk."""
    m = re.search(r"/CHGS USD(\d+\.\d\d)", card["bank_line"]["text"])
    return [{"amount": round(float(m.group(1)) * 100), "reason": "BANK_FEE", "evidence": "bank_text"}] if m else []


def pre_post(ctx, card: dict) -> dict:
    line = card["bank_line"]
    claim = {"desk": "bank_rec", "matched_entries": [], "difference": None, "bank_side": bank_side(card),
             "evidence": [f"bank:{line['line_id']}"]}
    if card["kind"] == "outflow":
        claim.update(match_outflow(ctx, line))
    return claim


def unmatched_cash_entries(ctx, sign: str) -> list[dict]:
    col = "credit" if sign == "out" else "debit"
    rows = ctx.store.q(f"SELECT e.entry_id, e.date, e.source, e.memo, e.erp_id, l.{col} AS amount, e.card_id FROM entries e "
                       f"JOIN lines l USING (entry_id) WHERE l.account='cash' AND l.{col} > 0 AND e.reverses IS NULL "
                       "AND e.entry_id NOT IN (SELECT entry_id FROM bank_matches) "
                       "AND e.entry_id NOT IN (SELECT reverses FROM entries WHERE reverses IS NOT NULL) ORDER BY e.entry_id")
    return [dict(r) for r in rows]


def match_outflow(ctx, line: dict) -> dict:
    amount = -line["amount"]
    pool = [e for e in unmatched_cash_entries(ctx, "out") if abs(days_between(e["date"], line["date"])) <= 3]
    ref = re.search(r"\b(RF-\d+|SC-\d+)\b", line["text"])
    singles = [e for e in pool if e["amount"] == amount]
    if len(singles) > 1:  # several plausible matches: prefer the entry carrying the bank reference, then a system entry
        ranked = sorted(singles, key=lambda e: (not (ref and ref.group(1) in e["memo"] and e["source"] != "manual"),
                                                e["source"] == "manual", e["entry_id"]))
        singles = ranked[:1]
    if singles:
        return {"matched_entries": [singles[0]["entry_id"]], "difference": 0, "how": "one_to_one"}
    same_day = [e for e in pool if e["date"] == line["date"]]
    for r in (2, 3):
        for combo in combinations(same_day, r):
            if sum(e["amount"] for e in combo) == amount and len({e["source"] for e in combo}) == 1:
                return {"matched_entries": [e["entry_id"] for e in combo], "difference": 0, "how": "one_to_many"}
    return {"matched_entries": [], "difference": amount, "how": "unmatched"}


def post_post(ctx, card: dict, claim: dict, entry_id: str | None) -> dict:
    """After posting: tie the bank line to the ledger cash entry and record the match."""
    line = card["bank_line"]
    if card["kind"] != "outflow":
        e = ctx.tools.ledger.entry(entry_id) if entry_id else None
        cash = sum(ln["debit"] - ln["credit"] for ln in e["lines"] if ln["account"] == "cash") if e else 0
        claim.update(matched_entries=[entry_id] if e else [], difference=line["amount"] - cash, how="one_to_one")
    for eid in claim["matched_entries"]:
        ctx.store.x("INSERT OR REPLACE INTO bank_matches VALUES (?,?,?)", eid, line["line_id"], card["card_id"])
        ctx.store.x("UPDATE entries SET card_id=? WHERE entry_id=? AND card_id IS NULL", card["card_id"], eid)
    return claim


def find_orphans(ctx, month_end: bool = False) -> list[dict]:
    """Ledger cash entries with no bank line after five days. A hand-keyed entry that repeats a matched entry for the
    same counterparty and amount is a duplicate, and the fix is a reversal."""
    out = []
    for e in unmatched_cash_entries(ctx, "out") + unmatched_cash_entries(ctx, "in"):
        if not month_end and days_between(e["date"], ctx.day) < ORPHAN_DAYS:
            continue
        full = ctx.tools.ledger.entry(e["entry_id"])
        cust = next((ln["customer_id"] for ln in full["lines"] if ln["customer_id"]), None)
        twin = None
        for r in ctx.store.q("SELECT m.entry_id, m.card_id, m.line_id FROM bank_matches m JOIN entries e USING (entry_id) "
                             "WHERE ABS(julianday(e.date) - julianday(?)) <= 7 AND m.entry_id != ?", e["date"], e["entry_id"]):
            other = ctx.tools.ledger.entry(r["entry_id"])
            same_cash = any(ln["account"] == "cash" and ln["credit"] == e["amount"] for ln in other["lines"])
            same_cust = cust and any(ln["customer_id"] == cust for ln in other["lines"])
            if same_cash and same_cust:
                twin = dict(r)
        out.append({"entry_id": e["entry_id"], "amount": e["amount"], "date": e["date"], "source": e["source"],
                    "memo": e["memo"], "duplicate_of": twin["entry_id"] if twin and e["source"] == "manual" else None,
                    "card_id": twin["card_id"] if twin else None})
    return out


def statement(ctx, period: str, month_end: str) -> dict:
    """Bank balance + ledger cash items the bank has not seen - bank items the ledger has not seen = book cash."""
    lines = ctx.tools.bank_lines(period=period)
    bank_balance = lines[-1]["balance"] if lines else ctx.tools.opening_balances()["balances"]["cash"]
    book = ctx.tools.ledger.balance("cash", month_end)
    ledger_only = ctx.store.one(
        "SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM entries e JOIN lines l USING (entry_id) WHERE l.account='cash' "
        "AND e.date<=? AND e.source != 'opening' AND e.entry_id NOT IN (SELECT entry_id FROM bank_matches) "
        "AND e.reverses IS NULL AND e.entry_id NOT IN (SELECT reverses FROM entries WHERE reverses IS NOT NULL)", month_end)
    matched_lines = {r[0] for r in ctx.store.q("SELECT line_id FROM bank_matches")}
    bank_only = sum(b["amount"] for b in ctx.tools.bank_lines() if b["date"] <= month_end and b["line_id"] not in matched_lines)
    return {"bank_balance": bank_balance, "ledger_not_in_bank": ledger_only, "bank_not_in_ledger": bank_only,
            "book_cash": book, "difference": bank_balance + ledger_only - bank_only - book,
            "unreconciled_items": (1 if ledger_only else 0) + (1 if bank_only else 0)}
