"""Forecast desk. Keeps its own notebook of expected receipts: due date plus the payer's median lag. For each bank line
it says, from its own reading, which expected receipts were fulfilled and how far off it was. It learns each payer's
lag, flags payers who only pay on fixed days of the month, and reports 13 weekly buckets.
"""
import re
from datetime import date, timedelta
from itertools import combinations
from statistics import median

from spine.contract import NEAR_TOL_CENTS
from spine.tools import days_between

DESK = "forecast"


def _lags(ctx) -> dict:
    lags = ctx.store.note(DESK, "lags")
    if lags is None:
        lags = {}
        for h in ctx.tools.history():
            lags.setdefault(h["customer_id"], []).append({"lag": days_between(h["due_date"], h["paid_date"]),
                                                          "dom": int(h["paid_date"][8:])})
        ctx.store.set_note(DESK, "lags", lags)
    return lags


def fixed_days(obs: list[dict]) -> list[int] | None:
    """A payer whose receipts all land on the same days of the month (allowing the weekend roll to Monday)."""
    if len(obs) < 3:
        return None
    anchors = [a for a in (15, 30) if any(0 <= o["dom"] - a <= 2 or (a == 30 and o["dom"] <= 2) for o in obs)]
    ok = all(any(0 <= o["dom"] - a <= 2 for a in (15, 28, 30)) or o["dom"] <= 2 for o in obs)
    return anchors if ok and anchors else None


def expected_date(ctx, customer_id: str, due: str, learn: bool = True) -> str:
    obs = _lags(ctx).get(customer_id, [])
    if not learn or not obs:
        return due
    days = fixed_days(obs)
    d = date.fromisoformat(due)
    if days:
        while d.day not in (15, 30) and not (d.month == 2 and d.day == 28):
            d += timedelta(days=1)
        return d.isoformat()
    return (d + timedelta(days=round(median(o["lag"] for o in obs)))).isoformat()


def expected(ctx) -> dict:
    """The desk's own view of what is still to come: invoice_id -> {customer_id, amount, due, expected}."""
    book = ctx.store.note(DESK, "expected", {})
    seen = ctx.store.note(DESK, "seen", [])
    for i in ctx.tools.open_invoices():
        if i["invoice_id"] not in seen:
            seen.append(i["invoice_id"])
            book[i["invoice_id"]] = {"customer_id": i["customer_id"], "amount": i["open"], "due": i["due_date"],
                                     "expected": expected_date(ctx, i["customer_id"], i["due_date"])}
    ctx.store.set_note(DESK, "expected", book)
    ctx.store.set_note(DESK, "seen", seen)
    return book


def run(ctx, card: dict, peer: dict | None = None) -> dict:
    """`peer` is the cash application claim, shown only on a reopen round when the card is shared."""
    line = card["bank_line"]
    claim = {"desk": DESK, "fulfils": [], "partial": {}, "unexpected": 0, "expected": None, "actual": line["date"],
             "miss_days": None, "miss_amount": 0, "cause": None, "evidence": [f"bank:{line['line_id']}"], "how": None}
    if card["kind"] != "receipt":
        claim["how"] = "scheduled" if card["kind"] == "outflow" else "payout_run_rate"
        return claim
    book = expected(ctx)
    cid = (peer or {}).get("customer_id") or ctx.tools.identify_payer(line["text"], use_aliases=ctx.cfg.cards)["customer_id"]
    mine = sorted(((k, v) for k, v in book.items() if v["customer_id"] == cid), key=lambda kv: (kv[1]["due"], kv[0]))
    amount = line["amount"]
    if peer and peer.get("basis") and not peer.get("needs_human"):
        # reopened with the card in hand: the other desk holds stronger evidence than a forecast heuristic
        full = [k for k, v in peer["settles"].items() if k in book and v >= book[k]["amount"]]
        claim.update(fulfils=sorted(full), how=f"adopted:{peer['basis']}",
                     partial={k: v for k, v in peer["settles"].items() if k in book and v < book[k]["amount"]},
                     unexpected=sum(r["amount"] for r in peer["residuals"] if r["side"] == "over"))
        claim["evidence"] += [e for e in peer["evidence"] if not e.startswith("bank:")]
    elif cid is None:
        claim.update(unexpected=amount, how="unknown_payer")
    else:
        named = [n for n in re.findall(r"INV-\d+", line["text"]) if n in dict(mine)]
        exact = [c for r in range(1, 5) for c in combinations(mine, r) if sum(v["amount"] for _, v in c) == amount]
        if named and abs(sum(book[n]["amount"] for n in named) - amount) <= NEAR_TOL_CENTS:
            claim.update(fulfils=sorted(named), how="reference")
        elif exact:
            claim.update(fulfils=sorted(k for k, _ in exact[0]), how="exact" if len(exact) == 1 else "oldest_first")
        else:
            picked, total = [], 0
            for k, v in mine:  # oldest first, until the money runs out
                if total + v["amount"] <= amount + NEAR_TOL_CENTS:
                    picked.append(k)
                    total += v["amount"]
            if picked and abs(total - amount) <= NEAR_TOL_CENTS:
                claim.update(fulfils=sorted(picked), how="oldest_first", miss_amount=total - amount)
            else:
                claim.update(unexpected=amount, how="no_expected_receipt")
    if claim["fulfils"] or claim["partial"]:
        keys = claim["fulfils"] + list(claim["partial"])
        claim["expected"] = min(book[k]["expected"] for k in keys)
        claim["miss_days"] = days_between(claim["expected"], line["date"])
        claim["evidence"] += [f"inv:{k}" for k in keys]
        if abs(claim["miss_days"]) > 5:
            claim["cause"] = "TIMING"
    return claim


def commit(ctx, card: dict, claim: dict):
    """After the card closes: strike fulfilled receipts from the notebook and learn the payer's lag."""
    book, lags = expected(ctx), _lags(ctx)
    for k in claim["fulfils"]:
        v = book.pop(k, None)
        if v:
            lags.setdefault(v["customer_id"], []).append({"lag": days_between(v["due"], card["bank_line"]["date"]),
                                                          "dom": int(card["bank_line"]["date"][8:])})
    for k, paid in claim["partial"].items():
        if k in book:
            book[k]["amount"] -= paid
    ctx.store.set_note(DESK, "expected", book)
    ctx.store.set_note(DESK, "lags", lags)


def weekly(ctx, start: str, weeks: int = 13, learn: bool = True) -> list[dict]:
    """Net cash by week from `start`: expected receipts, the fixed outflow schedule, and the Stripe payout run rate."""
    book, s = expected(ctx), date.fromisoformat(start)
    recent = [b["amount"] for b in ctx.tools.bank_lines() if b["text"].startswith("STRIPE")][-15:]
    payout_day = sum(recent) / len(recent) if recent else 0
    out = []
    for w in range(weeks):
        a, b = s + timedelta(days=7 * w), s + timedelta(days=7 * w + 6)
        inflow = 0
        for v in book.values():
            exp = v["expected"] if learn else v["due"]
            exp = max(exp, start)  # overdue money is expected now, not in the past
            if a.isoformat() <= exp <= b.isoformat():
                inflow += v["amount"]
        outflow = sum(o["amount"] for o in ctx.tools.outflow_schedule() if a.isoformat() <= o["date"] <= b.isoformat())
        out.append({"week": a.isoformat(), "receipts": inflow, "payouts": round(payout_day * 5), "outflows": outflow,
                    "net": inflow + round(payout_day * 5) - outflow})
    return out
