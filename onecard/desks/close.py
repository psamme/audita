"""Close desk. Owns the month-end checklist and refuses to lock until it passes. Runs the monthly invariants (10 bank
ties, 11 subledgers tie, 12 nothing left open) and writes the close memo.
"""
import json

from spine.checker import check_card, failed
from spine.contract import usd
from spine.tools import day_of

from . import bank_rec

SYSTEM = """You are the close desk at Kestrel Inference. Write the month-end close memo for the controller from the facts
given: plain prose, under 200 words, no invented numbers. Lead with whether the month locked and why. Then the human
decisions and policy changes of the month, then anything still open."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["memo"], "properties": {"memo": {"type": "string"}}}


def subledgers(ctx, month_end: str) -> dict:
    ledger, s, out = ctx.tools.ledger, ctx.store, {}
    billed = sum(i["amount"] for i in ctx.tools._invoices if i["issue_date"] <= month_end)
    applied = s.one("SELECT COALESCE(SUM(amount), 0) FROM applications WHERE date<=?", month_end)
    out["ar"] = {"ledger": ledger.balance("ar", month_end), "detail": billed - applied}
    for acct in ("customer_credits", "deferred_revenue", "unapplied_cash"):
        out[acct] = {"ledger": -ledger.balance(acct, month_end),
                     "detail": s.one("SELECT COALESCE(SUM(amount), 0) FROM subledger WHERE account=? AND date<=?", acct, month_end)}
    in_transit = sum(p["amount"] for p in ctx.tools.payouts() if day_of(p["created"]) <= month_end < day_of(p["arrival_date"]))
    held = ctx.tools.stripe_balance(month_end)
    out["stripe_clearing"] = {"ledger": ledger.balance("stripe_clearing", month_end), "detail": in_transit + (held or 0),
                              "payouts_in_transit": in_transit, "stripe_balance": held}
    for v in out.values():
        v["ties"] = v["ledger"] == v["detail"]
    return out


def run(ctx, period: str, month_end: str, audit_report: dict | None) -> dict:
    cards = ctx.store.cards(period)
    unresolved = [c["card_id"] for c in cards if c["status"] != "posted"]
    # contradictions: re-run the per-card invariants over the month as it stands at lock
    contradictions = []
    for c in cards:
        if c["status"] == "posted" and c["kind"] != "outflow":
            for f in failed(check_card(ctx, c, final=True)):
                contradictions.append({"card_id": c["card_id"], "rule": f["rule"], "detail": f["detail"]})
    orphans = bank_rec.find_orphans(ctx, month_end=True)
    for o in orphans:
        contradictions.append({"card_id": o["card_id"], "rule": "ledger_entry_without_bank_line",
                               "detail": f"{o['entry_id']} {usd(o['amount'])} \"{o['memo']}\" has no bank line"})
    stmt, subs = bank_rec.statement(ctx, period, month_end), subledgers(ctx, month_end)
    late = ctx.store.one("SELECT COUNT(*) FROM entries e JOIN periods p ON p.period=e.period WHERE p.locked=1 AND e.period<? AND e.posted_on>?", period, month_end)
    checklist = [
        {"item": "Every card closed, or escalated and resolved", "ok": not unresolved, "detail": ", ".join(unresolved)},
        {"item": "Bank balance ties to book cash", "n": 10, "ok": stmt["difference"] == 0 and not stmt["ledger_not_in_bank"] and not stmt["bank_not_in_ledger"],
         "detail": f"bank {usd(stmt['bank_balance'])}, book {usd(stmt['book_cash'])}, unreconciled ledger items {usd(stmt['ledger_not_in_bank'])}"},
        {"item": "Receivables subledger ties to the ledger", "n": 11, "ok": subs["ar"]["ties"], "detail": f"{usd(subs['ar']['ledger'])} vs {usd(subs['ar']['detail'])}"},
        {"item": "Stripe clearing ties to payouts in transit plus the Stripe balance", "n": 11, "ok": subs["stripe_clearing"]["ties"],
         "detail": f"{usd(subs['stripe_clearing']['ledger'])} vs {usd(subs['stripe_clearing']['detail'])}"},
        {"item": "Customer credits and deferred revenue tie to their detail", "n": 11,
         "ok": all(subs[a]["ties"] for a in ("customer_credits", "deferred_revenue", "unapplied_cash")),
         "detail": ", ".join(f"{a} {usd(subs[a]['ledger'])}" for a in ("customer_credits", "deferred_revenue"))},
        {"item": "No claims in contradiction", "ok": not contradictions, "detail": f"{len(contradictions)} contradictions"},
        {"item": "Nothing left open, nothing dated in a locked month", "n": 12, "ok": not unresolved and not late, "detail": ""},
    ]
    passed = all(c["ok"] for c in checklist)
    decisions = [json.loads(r[0]) for r in ctx.store.q("SELECT doc FROM decisions WHERE day LIKE ?", period + "%")]
    policies = [p for p in ctx.book.all() if p["created"].startswith(period)]
    locked = passed or not ctx.cfg.cards  # independent desks have no gate: the month locks with whatever is in it
    if locked:
        ctx.tools.ledger.lock(period)
    facts = {"period": period, "locked": locked, "checklist": checklist, "human_decisions": [
        {"id": d["decision_id"], "card": d["card_id"], "question": d["question"], "answer": d["treatment"], "why": d["reason_text"]} for d in decisions],
        "policy_changes": [f"{p['code']} v{p['version']}: {p['reason']}" for p in policies],
        "audit": {"sampled": (audit_report or {}).get("sampled", 0), "findings": [f["detail"] for f in (audit_report or {}).get("findings", [])]}}
    memo = None
    if ctx.model.on:
        memo = (ctx.model.ask("close", SYSTEM, json.dumps(facts, indent=1), SCHEMA, strong=False) or {}).get("memo")
    if not memo:
        memo = (f"{period} {'locked' if locked else 'NOT locked'}. {sum(c['ok'] for c in checklist)} of {len(checklist)} checklist items pass. "
                f"{len(decisions)} human decisions, {len(policies)} policy versions written, {len(contradictions)} contradictions, "
                f"{len((audit_report or {}).get('findings', []))} audit findings.")
    report = {**facts, "passed": passed, "statement": stmt, "subledgers": subs, "contradictions": contradictions,
              "balances": ctx.tools.ledger.balances(month_end), "memo": memo}
    ctx.store.set_note("close", period, report)
    return report
