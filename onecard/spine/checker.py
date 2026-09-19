"""The checker. Dumb on purpose: comparisons in plain code over the claims on a card and the ledger. Nothing posts until
the per-card invariants pass. Invariants 10 to 12 run monthly, from the close desk.
"""
from .contract import TREATMENTS, entry_lines, over_total, short_total
from .policy import PolicyBook

REOPEN = {"forecast_agrees": "forecast", "same_reason_everywhere": "cash_app", "amounts_tie": "cash_app",
          "entry_matches_claim": "cash_app", "one_invoice_one_settlement": "cash_app", "payer_owns_invoice": "cash_app",
          "evidence_complete": "cash_app", "right_month": "cash_app"}


def claim_of(card: dict, desk: str) -> dict | None:
    return next((c for c in card["claims"] if c["desk"] == desk), None)


def check_card(ctx, card: dict, lines: list[dict] | None = None, entry_date: str | None = None, final: bool = False) -> list[dict]:
    """Invariants 1 to 8 for one card. `lines` is the proposed entry; by default the one the claim implies."""
    out = []
    add = lambda n, rule, ok, detail="": out.append({"n": n, "rule": rule, "ok": bool(ok), "detail": detail})
    line, ca, br, fc = card["bank_line"], claim_of(card, "cash_app"), claim_of(card, "bank_rec"), claim_of(card, "forecast")
    amount = line["amount"]
    if card["kind"] == "outflow":
        add(1, "amounts_tie", br and br["difference"] == 0, "" if br and br["difference"] == 0 else "no ledger entry matches this bank line")
        add(8, "evidence_complete", br and br["evidence"])
        return out
    lines = lines if lines is not None else entry_lines(amount, ca)
    # 0: the gate the hero card trips
    unknown = [r for r in ca["residuals"] if not r.get("treatment")]
    add(0, "no_unknown_residual", not unknown and not ca.get("needs_human"),
        "; ".join(f"{r['amount']} cents unexplained" for r in unknown) or (ca.get("needs_human") or {}).get("why", ""))
    # 1: amounts tie
    if ca.get("payout"):
        p = ca["payout"]
        tie = amount - (p["charges"] - p["fees"] - p["refunds"] - p["disputes"] - p["dispute_fees"])
    else:
        tie = amount - (sum(ca["settles"].values()) - short_total(ca["residuals"]) + over_total(ca["residuals"]))
    add(1, "amounts_tie", tie == 0, f"off by {tie} cents" if tie else "")
    # 2: entry matches claim
    net = {}
    for ln in lines:
        net[ln["account"]] = net.get(ln["account"], 0) + ln.get("debit", 0) - ln.get("credit", 0)
    want = {"cash": amount, "ar": -sum(ca["settles"].values())}
    for r in ca["residuals"]:
        t = TREATMENTS.get(r.get("treatment") or "")
        if t and t["account"]:
            want[t["account"]] = want.get(t["account"], 0) + (r["amount"] if t["dr"] else -r["amount"])
    for ln in ca.get("extra_lines", []):
        want[ln["account"]] = want.get(ln["account"], 0) + ln["debit"] - ln["credit"]
    diff = {a for a in set(net) | set(want) if net.get(a, 0) != want.get(a, 0)}
    add(2, "entry_matches_claim", not diff and sum(net.values()) == 0, f"differs on {sorted(diff)}" if diff else "")
    # 3: one invoice, one settlement
    bad = []
    for inv, cents in ca["settles"].items():
        rec = ctx.tools.invoice(inv)
        others = ctx.store.one("SELECT COALESCE(SUM(amount), 0) FROM applications WHERE invoice_id=? AND card_id!=?", inv, card["card_id"])
        if not rec or others + cents > rec["amount"]:
            bad.append(inv)
    add(3, "one_invoice_one_settlement", not bad, f"already settled elsewhere: {bad}" if bad else "")
    # 4: payer owns the invoice
    foreign = [inv for inv in ca["settles"] if (ctx.tools.invoice(inv) or {}).get("customer_id") != ca["customer_id"]]
    linked = card["payer"]["customer_id"] == ca["customer_id"] or ca.get("alias") or not ca["customer_id"]
    add(4, "payer_owns_invoice", not foreign and linked, f"{foreign} belong to another customer" if foreign else ("" if linked else "payer is not this customer and no alias links them"))
    # 5: same reason everywhere
    clash = []
    for b in (br or {}).get("bank_side", []):
        mine = [r for r in ca["residuals"] if r["amount"] == b["amount"]]
        if not mine or any((r.get("reason") if r.get("treatment") else r.get("candidate")) not in (b["reason"], None) for r in mine):
            if mine and all(r.get("treatment") == "leave_open_chase" for r in mine):
                continue
            clash.append(f"bank rec reads {b['amount']} as {b['reason']}")
    add(5, "same_reason_everywhere", not clash, "; ".join(clash))
    # 6: forecast agrees
    if fc and card["kind"] == "receipt":
        full = sorted(inv for inv, cents in ca["settles"].items()
                      if cents + _before(ctx, card, inv) >= (ctx.tools.invoice(inv) or {"amount": 1 << 60})["amount"])
        problems = []
        if sorted(fc["fulfils"]) != full:
            problems.append(f"forecast strikes {sorted(fc['fulfils'])}, cash application settles {full}")
        if over_total(ca["residuals"]) and fc["fulfils"]:
            problems.append("a customer credit is counted as an expected receipt")
        add(6, "forecast_agrees", not problems, "; ".join(problems))
    # 7: right month
    d = entry_date or line["date"]
    add(7, "right_month", d[:7] == line["date"][:7] and (final or not ctx.tools.ledger.locked(d[:7])),
        "" if d[:7] == line["date"][:7] else f"entry dated {d}, bank line {line['date']}")
    # 8: evidence complete, policies cited inside scope and version
    problems = [f"{c['desk']} has no evidence" for c in card["claims"] if not c.get("evidence")]
    book = PolicyBook(ctx.store)
    for r in ca["residuals"]:
        if r.get("policy"):
            p = book.get(r["policy"])
            uses = book.occurrences(ca["customer_id"], p["scope"]["reason"], line["date"]) if p else 0
            already = ctx.store.one("SELECT COUNT(*) FROM policy_uses WHERE card_id=? AND code=?", card["card_id"], (p or {}).get("code"))
            if final:
                if not p and "~" not in r["policy"] and not ctx.store.one("SELECT COUNT(*) FROM policies WHERE code LIKE ?", r["policy"].split("@")[0] + "~%"):
                    problems.append(f"{r['policy']} does not exist")
            elif not p or p["status"] not in ("probation", "active") or book.in_scope(p, ca["customer_id"], r["amount"], uses - (1 if already else 0)):
                problems.append(f"{r['policy']} cited outside its scope or version")
    add(8, "evidence_complete", not problems, "; ".join(problems))
    return out


def _before(ctx, card, inv) -> int:
    """Cents other cards had applied to this invoice by the time this card's bank line arrived."""
    return ctx.store.one("SELECT COALESCE(SUM(amount), 0) FROM applications WHERE invoice_id=? AND card_id<? AND date<=?",
                         inv, card["card_id"], card["bank_line"]["date"])


def failed(checks: list[dict]) -> list[dict]:
    return [c for c in checks if not c["ok"]]
