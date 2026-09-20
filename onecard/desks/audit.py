"""Audit desk. Independent on purpose: it reads raw evidence and posted entries only, never another desk's reasoning, and
shares no code with the cash application ladder. It re-performs a sample, tests controls, and writes findings.

Re-performance is mechanical by default (can every posted line be traced to raw evidence or to a human's authority?).
With --audit-model the strongest model rebuilds each sampled card from the raw records as a second pair of eyes.
"""
import json
import random
import re

from spine.policy import PolicyBook
from spine.tools import norm

BIG = 5000000  # every card above $50,000 is sampled
AUTHORITY_ACCOUNTS = {"bank_fees", "sales_discounts", "customer_credits", "deferred_revenue", "unapplied_cash"}

SYSTEM = """You are the audit desk at Kestrel Inference. You see raw records and the journal entry that was posted, and
nothing of how the posting desk reasoned. Rebuild the cash application from the raw records alone, then compare.
Human decisions and approved policies listed under AUTHORITY are valid authority for judgement calls such as absorbing a
fee; your job is to check the facts: right customer, right invoices, amounts that tie, the entry in the right accounts.
Answer agree=false only when the posted entry is wrong on the facts, and say exactly what differs."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["agree", "rebuilt", "finding"],
          "properties": {"agree": {"type": "boolean"}, "rebuilt": {"type": "string"}, "finding": {"type": "string"}}}


def sample(ctx, period: str, seed: int) -> dict:
    """card_id -> why sampled."""
    cards = [c for c in ctx.store.cards(period) if c["status"] == "posted" and c["kind"] != "outflow"]
    rng, picked = random.Random(f"{seed}:{period}"), {}
    for c in rng.sample(cards, max(1, len(cards) // 10)) if cards else []:
        picked[c["card_id"]] = "random 10%"
    for c in cards:
        if abs(c["bank_line"]["amount"]) > BIG:
            picked[c["card_id"]] = "above dollar threshold"
        if c.get("rounds", 0) > 1:
            picked[c["card_id"]] = "reopened more than once"
    seen = {}
    for u in ctx.store.q("SELECT code, version, card_id FROM policy_uses WHERE day LIKE ? ORDER BY rowid", period + "%"):
        k = (u["code"], u["version"])
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= 5 and ctx.store.card(u["card_id"]):
            picked[u["card_id"]] = f"early use of {u['code']}@v{u['version']}"
    return picked


def raw_view(ctx, card: dict) -> dict:
    """Read-only, reasoning-free: the bank line, source documents, and what reached the ledger."""
    ledger = ctx.tools.ledger
    docs = {}
    for e in card["evidence"]:
        kind, _, key = e.partition(":")
        if kind == "email" and ctx.tools.email(key):
            docs[e] = ctx.tools.email(key)
    return {"bank_line": card["bank_line"], "entries": ledger.card_entries(card["card_id"]), "documents": docs,
            "decisions": [json.loads(r[0]) for r in ctx.store.q("SELECT doc FROM decisions WHERE card_id=?", card["card_id"])],
            "policy_uses": [dict(r) for r in ctx.store.q("SELECT * FROM policy_uses WHERE card_id=?", card["card_id"])]}


def reperform(ctx, card: dict) -> dict:
    view, line, problems = raw_view(ctx, card), card["bank_line"], []
    net, ar = {}, {}
    for e in view["entries"]:
        for ln in e["lines"]:
            net[ln["account"]] = net.get(ln["account"], 0) + ln["debit"] - ln["credit"]
            if ln["account"] == "ar" and ln["invoice_id"]:
                ar[ln["invoice_id"]] = ar.get(ln["invoice_id"], 0) + ln["credit"] - ln["debit"]
    if net.get("cash", 0) != line["amount"]:
        problems.append(f"cash posted {net.get('cash', 0)} but the bank shows {line['amount']}")
    if card["kind"] == "payout":
        po = ctx.tools.payout_for(line["text"])
        want = -(sum(t["amount"] - t["fee"] for t in po["balance_transactions"] if t["reporting_category"] != "dispute")) if po else None
        if po is None or net.get("stripe_clearing") != want:
            problems.append("Stripe clearing relief does not equal charges less fees and refunds in the payout")
        return {"agree": not problems, "problems": problems, "method": "mechanical"}
    owners = {(ctx.tools.invoice(i) or {}).get("customer_id") for i in ar}
    if len(owners) > 1:
        problems.append("one receipt applied across several customers")
    for owner in owners:
        cust = ctx.tools.customer(owner)
        if not cust:
            problems.append("an invoice with no customer")
            continue
        printed = norm(line["text"])
        name = norm(cust["name"])
        linked = name[:12] in printed or any(cust["name"].lower() in (d["body"] + d["subject"]).lower() for d in view["documents"].values()) \
            or any(d["features"].get("reason") == "UNKNOWN_PAYER" for d in view["decisions"]) \
            or ctx.store.one("SELECT COUNT(*) FROM aliases WHERE customer_id=?", owner)
        if not linked:
            problems.append(f"nothing in the raw records links this payer to {cust['name']}")
    for inv, cents in ar.items():
        rec = ctx.tools.invoice(inv)
        total = ctx.store.one("SELECT COALESCE(SUM(amount), 0) FROM applications WHERE invoice_id=?", inv)
        if rec and total > rec["amount"]:
            problems.append(f"{inv} is settled for more than it was billed")
    named = set(re.findall(r"INV-\d+", line["text"]))
    if named and ar and not set(ar) <= named and not view["decisions"]:
        problems.append(f"the bank reference names {sorted(named)} but {sorted(set(ar) - named)} was credited")
    book = PolicyBook(ctx.store)
    owner = next(iter(owners), None) or next((ln["customer_id"] for e in view["entries"] for ln in e["lines"] if ln["customer_id"]), None)
    for acct in AUTHORITY_ACCOUNTS & set(net):
        cents = abs(net[acct])
        by_human = any(d["treatment"] not in ("allocation", "oldest_first", "alias") for d in view["decisions"])
        by_policy = False
        for u in view["policy_uses"]:
            p = book.get(f"{u['code']}@v{u['version']}") if "~" not in u["code"] else {"scope": {"customers": "any"}, "condition": {}}
            if p and book.in_scope(p, owner, u["amount"], 0) in (None, "over_use_limit"):
                by_policy = True
        by_words = acct == "deferred_revenue" and ("PREPAID" in line["text"].upper() or any("prepaid" in d["body"].lower() for d in view["documents"].values()))
        by_contract = acct == "sales_discounts" and "Early payment" in ctx.tools.contract(owner or "")
        if not (by_human or by_policy or by_words or by_contract):
            problems.append(f"{cents} cents to {acct} with no human decision, policy in scope, or customer instruction behind it")
    return {"agree": not problems, "problems": problems, "method": "mechanical"}


def model_reperform(ctx, card: dict) -> dict | None:
    view = raw_view(ctx, card)
    cid = next((ln["customer_id"] for e in view["entries"] for ln in e["lines"] if ln["customer_id"]), None)
    payer_invoices = [i for i in ctx.tools._invoices if i["customer_id"] == cid and i["issue_date"] <= card["bank_line"]["date"]]
    prompt = (f"BANK LINE\n{json.dumps(view['bank_line'])}\n\nINVOICES OF THE CUSTOMER CREDITED\n{json.dumps(payer_invoices, default=str)}\n\n"
              f"DOCUMENTS\n{json.dumps(view['documents'])}\n\nCONTRACT\n{ctx.tools.contract(cid) if cid else ''}\n\n"
              f"AUTHORITY\n{json.dumps([{k: d[k] for k in ('decision_id', 'question', 'treatment', 'reason_text')} for d in view['decisions']])}\n"
              f"{json.dumps(view['policy_uses'])}\n\nPOSTED ENTRIES (cents)\n{json.dumps(view['entries'])}")
    return ctx.model.ask("audit", SYSTEM, prompt, SCHEMA, strong=True)


def controls(ctx, period: str) -> list[dict]:
    out = []
    for r in ctx.store.q("SELECT entry_id, prepared_by, approved_by, memo, erp_id FROM entries WHERE period=? AND source='manual'", period):
        if r["prepared_by"] == r["approved_by"]:
            out.append({"severity": "high", "control": "preparer_is_not_approver", "entry_id": r["entry_id"], "erp_id": r["erp_id"],
                        "detail": f"{r['entry_id']} \"{r['memo']}\" was prepared and approved by {r['prepared_by']}"})
    for r in ctx.store.q("SELECT e.entry_id FROM entries e JOIN periods p ON p.period=e.period WHERE p.locked=1 AND e.posted_on > "
                         "date(e.period || '-01', '+1 month', '+5 day')"):
        out.append({"severity": "high", "control": "no_entry_in_locked_month", "entry_id": r["entry_id"], "detail": "posted into a locked month"})
    for c in ctx.store.cards(period):
        for claim in c["claims"]:
            if not claim.get("evidence"):
                out.append({"severity": "medium", "control": "every_claim_has_evidence", "card_id": c["card_id"],
                            "detail": f"{claim['desk']} pinned a claim with no evidence"})
    return out


def run(ctx, period: str, seed: int, use_model: bool = False) -> dict:
    picked, findings, results = sample(ctx, period, seed), [], {}
    for card_id, why in picked.items():
        card = ctx.store.card(card_id)
        res = reperform(ctx, card)
        if use_model and ctx.model.on:
            m = model_reperform(ctx, card)
            if m is not None:
                res["model"] = m
                if not m["agree"]:
                    res["agree"] = False
                    res["problems"].append(f"model re-performance: {m['finding']}")
        results[card_id] = {**res, "sampled_because": why}
        card["claims"] = [c for c in card["claims"] if c["desk"] != "audit"] + [
            {"desk": "audit", "agree": res["agree"], "sampled_because": why, "method": res["method"] + ("+model" if res.get("model") else ""),
             "problems": res["problems"], "evidence": [f"bank:{card['bank_line']['line_id']}"] + [f"entry:{e['entry_id']}" for e in ctx.tools.ledger.card_entries(card_id)]}]
        ctx.store.save_card(card)
        if not res["agree"]:
            findings.append({"severity": "high", "control": "reperformance", "card_id": card_id, "detail": "; ".join(res["problems"])})
    findings += controls(ctx, period)
    for n, f in enumerate(findings):
        ctx.store.x("INSERT OR REPLACE INTO findings VALUES (?,?,?,?)", f"F-{period}-{n + 1:03d}", period, f.get("card_id"), json.dumps(f))
    return {"sampled": len(picked), "agreed": sum(1 for r in results.values() if r["agree"]), "findings": findings}
