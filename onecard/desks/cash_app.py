"""Cash application desk. Works down the ladder and stops at the first rung that decides.

  1  invoice numbers in the bank reference, or a structured remittance advice, and the amount matches exactly
  2  the amount equals exactly one of the payer's open invoices
  3  exactly one combination of the payer's open invoices sums to the amount
  4  a small gap is fully explained by residuals that active policies cover
  5  the model reads emails and the contract; every conclusion must carry a verbatim quote, checked in code
  6  escalate, with the candidates ranked

Rungs 1 to 4 are plain code. The model may decide allocation, payer aliases and contract terms from quoted evidence.
It may never decide to absorb, write off or hold money: those need a policy or a human.
"""
import re
from itertools import combinations

from spine.contract import NEAR_TOL_CENTS, NEAR_TOL_PCT, TREATMENTS, short_total, usd
from spine.policy import ref as policy_ref
from spine.tools import days_between

ROUND_FEES = range(1000, 8000, 500)
DISCOUNT_BPS = (50, 100, 200)
MODEL_MAY = {"contract_discount", "deferred_revenue"}

SYSTEM = """You are the cash application desk at Kestrel Inference. One bank receipt could not be applied by the
deterministic rules. Decide only what the evidence in front of you proves.

You may conclude: which of the listed open invoices the payment settles; which customer an unknown payer is paying for;
that a deduction is a discount the contract allows (treatment contract_discount); that the money is a prepaid credits
purchase (treatment deferred_revenue). Every such conclusion needs `quote`: a verbatim passage copied from the email or
contract that proves it, and `source_id`: that document's id. Quotes are checked character by character.

You may NOT decide to absorb a bank fee, write off a short payment or hold money as credit: that is company policy you
cannot see. Leave those residuals out and set confident to false, or explain them with reason UNKNOWN.
If the evidence does not settle the question, set confident to false. A wrong posting is far worse than a question."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["confident", "customer_id", "invoice_ids", "residuals", "quote", "source_id", "explanation"],
          "properties": {
              "confident": {"type": "boolean"}, "customer_id": {"type": ["string", "null"]},
              "invoice_ids": {"type": "array", "items": {"type": "string"}},
              "residuals": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False,
                  "required": ["amount_cents", "reason", "treatment", "quote", "source_id"],
                  "properties": {"amount_cents": {"type": "integer"}, "reason": {"type": "string"},
                                 "treatment": {"type": "string"}, "quote": {"type": "string"}, "source_id": {"type": "string"}}}},
              "quote": {"type": "string"}, "source_id": {"type": "string"}, "explanation": {"type": "string"}}}


def run(ctx, card: dict) -> dict:
    if card["kind"] == "payout":
        return payout_claim(ctx, card)
    line, res = card["bank_line"], card.get("resolutions", {})
    amount, text = line["amount"], line["text"]
    claim = {"desk": "cash_app", "customer_id": None, "settles": {}, "residuals": [], "rung": None, "basis": None,
             "evidence": [f"bank:{line['line_id']}"], "needs_human": None, "note": ""}
    if res.get("forced", {}).get("treatment") == "unapplied_cash":
        # a human parked the money: nothing is settled and the whole receipt is held
        claim.update(customer_id=card["payer"]["customer_id"], rung=6, basis="human")
        claim["residuals"] = [{"amount": amount, "side": "over", "reason": "UNKNOWN", "candidate": None, "basis": "held by a human",
                               "treatment": "unapplied_cash", "decision": res["forced"]["decision_id"]}]
        claim["evidence"].append(f"decision:{res['forced']['decision_id']}")
        return claim
    cid = res.get("payer") or card["payer"]["customer_id"]
    emails =[ctx.tools.email(e.split(":")[1]) for e in card["evidence"] if e.startswith("email:")]
    emails = [e for e in emails if e]
    model_out = None

    if not cid:
        model_out = ask_model(ctx, card, None, [], emails, "The payer name matches no customer.")
        found = verified_customer(ctx, model_out, emails)
        if not found:
            claim["needs_human"] = {"type": "unknown_payer", "why": "payer_matches_no_customer",
                                    "candidates": payer_candidates(ctx, amount)}
            claim["rung"] = 6
            return claim
        cid = found
        claim.update(alias={"payer_key": card["payer"]["printed"], "customer_id": cid, "source": f"email:{model_out['source_id']}"},
                     model=True, note=model_out["explanation"])
        claim["evidence"].append(f"email:{model_out['source_id']}")
    elif res.get("payer"):
        claim["alias"] = {"payer_key": card["payer"]["printed"], "customer_id": cid, "source": res["payer_decision"]}
        claim["evidence"].append(f"decision:{res['payer_decision']}")
    elif card["payer"]["how"] == "alias":
        claim["evidence"].append(f"alias:{card['payer']['printed']}")
    claim["customer_id"] = cid

    open_ = ctx.tools.open_invoices(cid)[:12]
    by_id = {i["invoice_id"]: i for i in open_}
    combo, gap = None, 0

    # a human already chose the allocation
    if res.get("allocation"):
        combo = [by_id[i] for i in res["allocation"] if i in by_id]
        gap = max(sum(i["open"] for i in combo) - amount, 0)  # the human chose the invoices; any shortfall is still a residual
        claim.update(rung=6, basis="human")
        claim["evidence"].append(f"decision:{res['allocation_decision']}")
    # rung 1: invoice numbers in the reference, or a structured remittance advice
    if combo is None:
        named, source = re.findall(r"INV-\d+", text), None
        if not named:
            for e in emails:
                if e["subject"].startswith("Remittance advice") and f"{amount / 100:,.2f}" in e["subject"] + e["body"]:
                    named, source = re.findall(r"INV-\d+", e["body"]), f"email:{e['email_id']}"
        if named and all(n in by_id for n in named):
            g = sum(by_id[n]["open"] for n in named) - amount
            if 0 <= g < amount + g:  # the payer named the invoices, so any shortfall is a residual to explain, not a guess
                combo, gap = [by_id[n] for n in named], g
                claim.update(rung=1 if g == 0 else 4, basis="remittance" if source else "reference")
                if source:
                    claim["evidence"].append(source)
    # the reference names only invoices this payer has already settled: they think they are paying those, so amount
    # matching against other open invoices would be a guess. Straight to the overpayment path.
    settled = {i["invoice_id"] for i in ctx.tools.settled_invoices(cid)} - set(by_id)
    paid_again = combo is None and bool(re.findall(r"INV-\d+", text)) and set(re.findall(r"INV-\d+", text)) <= settled
    # rungs 2 and 3: exact matches among the payer's open invoices
    if combo is None and not paid_again:
        exact = [c for r in range(1, 5) for c in combinations(open_, r) if sum(i["open"] for i in c) == amount]
        if len(exact) == 1:
            combo = list(exact[0])
            claim.update(rung=2 if len(combo) == 1 else 3, basis="exact" if len(combo) == 1 else "unique_combination")
        elif len(exact) > 1:
            combo = choose_among(ctx, card, claim, cid, amount, exact, emails)
            if combo is None:
                return claim
    # rung 4 entry: exactly one combination sits just above the amount
    if combo is None and not paid_again:
        near = [c for r in range(1, 5) for c in combinations(open_, r)
                if 0 < sum(i["open"] for i in c) - amount <= tolerance(sum(i["open"] for i in c))]
        if len(near) > 1:  # keep only the combinations whose gap is a recognised pattern: a bank fee, a discount, or both
            explained = [c for c in near if all(r.get("candidate") for r in
                                                decompose(ctx, sum(i["open"] for i in c) - amount, list(c), text, cid))]
            near = explained if len(explained) == 1 else near
        if len(near) == 1:
            combo, gap = list(near[0]), sum(i["open"] for i in near[0]) - amount
            claim.update(rung=4, basis="unique_near_combination")
        elif len(near) > 1:
            claim["needs_human"] = {"type": "ambiguous_allocation", "why": "several_near_combinations",
                                    "combos": [[i["invoice_id"] for i in c] for c in near[:4]]}
            claim["rung"] = 6
            return claim

    if combo is not None:
        claim["settles"] = {i["invoice_id"]: i["open"] for i in combo}
        claim["evidence"] += [f"inv:{i['invoice_id']}" for i in combo]
        if gap:
            claim["residuals"] = decompose(ctx, gap, combo, text, cid)
    else:
        claim["residuals"] = [overpayment(ctx, card, cid, amount, text, emails)]
        claim.update(rung=4, basis="no_open_invoice")

    # resolve each residual: a human answer on this card, then the policy book
    for r in claim["residuals"]:
        resolve(ctx, card, claim, r)
    # rung 5: evidence the rules cannot read
    open_residuals = [r for r in claim["residuals"] if not r.get("treatment")]
    if open_residuals and ctx.model.on and (emails or "Early payment" in ctx.tools.contract(cid)):
        model_out = ask_model(ctx, card, cid, open_, emails, f"Unexplained residuals: {[(r['side'], r['amount']) for r in open_residuals]}")
        apply_model_residuals(ctx, claim, model_out, emails, cid)
    open_residuals = [r for r in claim["residuals"] if not r.get("treatment")]
    if open_residuals:
        r = open_residuals[0]
        claim["needs_human"] = {"type": "residual", "why": r.get("escalate_why") or "unknown_residual_no_policy",
                                "residual": claim["residuals"].index(r), "near_policy": r.get("near_policy")}
        claim["rung"] = 6
    for r in claim["residuals"]:
        if r.get("treatment") == "leave_open_chase":
            on = r.get("on") or max(claim["settles"])
            r["on"] = on
            claim["settles"][on] -= r["amount"]
    claim["settles"] = {k: v for k, v in claim["settles"].items() if v > 0}
    return claim


def tolerance(invoice_total: int) -> int:
    return min(NEAR_TOL_CENTS, int(invoice_total * NEAR_TOL_PCT))


def choose_among(ctx, card, claim, cid, amount, exact, emails):
    """Several combinations tie. A saved allocation policy, then the model with a quote, then a human."""
    options = [[i["invoice_id"] for i in c] for c in exact[:4]]
    oldest = sorted(exact, key=lambda c: (max(i["due_date"] for i in c), len(c)))
    unique_oldest = max(i["due_date"] for i in oldest[0]) < max(i["due_date"] for i in oldest[1])
    if ctx.cfg.policies and unique_oldest:
        m = ctx.book.match(cid, "AMBIGUOUS_ALLOCATION", amount, ctx.day)
        if m and m["verdict"] == "fire":
            claim.update(rung=4, basis="policy", allocation_policy=policy_ref(m["policy"]))
            claim["evidence"].append(f"policy:{policy_ref(m['policy'])}")
            return list(oldest[0])
    if ctx.model.on and emails:
        out = ask_model(ctx, card, cid, [i for c in exact for i in c], emails,
                        f"Several invoice combinations sum to the amount exactly: {options}")
        if out and out["confident"] and sorted(out["invoice_ids"]) in [sorted(o) for o in options] and quoted(out, emails):
            claim.update(rung=5, basis="email", model=True, note=out["explanation"])
            claim["evidence"].append(f"email:{out['source_id']}")
            return [i for c in exact for i in c if sorted(x["invoice_id"] for x in c) == sorted(out["invoice_ids"])][:len(out["invoice_ids"])]
    claim["needs_human"] = {"type": "ambiguous_allocation", "why": "several_combinations_tie", "combos": options,
                            "oldest_first": [i["invoice_id"] for i in oldest[0]] if unique_oldest else None}
    claim["rung"] = 6
    return None


def decompose(ctx, gap: int, combo: list[dict], text: str, cid: str) -> list[dict]:
    """Split a short payment into components with a candidate cause each. Anything not uniquely explained stays UNKNOWN."""
    out, rest = [], gap
    m = re.search(r"/CHGS USD(\d+\.\d\d)", text)
    if m and round(float(m.group(1)) * 100) <= gap:
        fee = round(float(m.group(1)) * 100)
        out.append({"amount": fee, "side": "short", "reason": "UNKNOWN", "candidate": "BANK_FEE",
                    "basis": f"bank advice shows charges of {usd(fee)}", "evidence": "bank_text"})
        rest -= fee
    if rest == 0:
        return out

    def discounts(x):
        hits = [(i["invoice_id"], bps) for i in combo for bps in DISCOUNT_BPS if i["open"] * bps // 10000 == x]
        for bps in DISCOUNT_BPS:
            if len(combo) > 1 and sum(i["open"] * bps // 10000 for i in combo) == x:
                hits.append((None, bps))
        return hits

    wire = text.startswith("WIRE")
    singles = [("DISCOUNT_TAKEN", h) for h in discounts(rest)] + ([("BANK_FEE", None)] if wire and rest in ROUND_FEES and not m else [])
    pairs = [(f, h) for f in ROUND_FEES if wire and not m and f < rest for h in discounts(rest - f)]
    if len(singles) == 1:
        reason, h = singles[0]
        out.append(component(rest, reason, h))
    elif not singles and len(pairs) == 1:
        f, h = pairs[0]
        out += [component(f, "BANK_FEE", None), component(rest - f, "DISCOUNT_TAKEN", h)]
    else:
        out.append({"amount": rest, "side": "short", "reason": "UNKNOWN", "candidate": None,
                    "basis": "no single explanation fits" if singles or pairs else "no pattern fits"})
    return out


def component(amount, reason, hit):
    basis = "round amount on a wire: looks like a bank fee"
    r = {"amount": amount, "side": "short", "reason": "UNKNOWN", "candidate": reason}
    if reason == "DISCOUNT_TAKEN":
        inv, bps = hit
        basis = f"{bps / 100:g}% of {inv or 'every invoice paid'}"
        if inv:
            r["on"] = inv
    return {**r, "basis": basis}


def overpayment(ctx, card, cid, amount, text, emails) -> dict:
    """Money that matches no open invoice: a duplicate, prepaid credits, or unknown."""
    r = {"amount": amount, "side": "over", "reason": "UNKNOWN", "candidate": None, "basis": "matches no open invoice"}
    named = re.findall(r"INV-\d+", text)
    for inv in ctx.tools.settled_invoices(cid):
        recent = days_between(inv["settled_on"], card["bank_line"]["date"]) <= 45
        if inv["amount"] == amount and recent and (not named or inv["invoice_id"] in named):
            r.update(candidate="DUPLICATE_PAYMENT", basis=f"{inv['invoice_id']} was already settled by {inv['card_id']}",
                     duplicate_of=inv["invoice_id"], evidence=f"card:{inv['card_id']}")
            return r
    proof = None
    if "PREPAID" in text.upper():
        proof = "bank_text"
    for e in emails:
        if "prepaid" in e["body"].lower() and f"{amount / 100:,.2f}" in e["body"]:
            proof = f"email:{e['email_id']}"
    if proof and amount % 100000 == 0:
        # the customer's own words name the money: the entry follows from the chart of accounts, not from policy
        r.update(reason="PREPAID_CREDITS", candidate="PREPAID_CREDITS", treatment="deferred_revenue",
                 basis="customer states this is a prepaid credits purchase", evidence=proof)
    return r


def resolve(ctx, card, claim, r):
    if r.get("treatment"):
        if r.get("evidence", "").startswith("email:"):
            claim["evidence"].append(r["evidence"])
        return
    human = card.get("resolutions", {}).get("residuals", {}).get(f"{r['side']}:{r['amount']}")
    if human:
        r.update(treatment=human["treatment"], reason=human["reason"], decision=human["decision_id"])
        claim["evidence"].append(f"decision:{human['decision_id']}")
        return
    if not (ctx.cfg.policies and r.get("candidate")):
        return
    m = ctx.book.match(claim["customer_id"], r["candidate"], r["amount"], ctx.day)
    if m and m["verdict"] == "fire":
        p = m["policy"]
        r.update(treatment=p["action"]["treatment"], reason=r["candidate"], policy=policy_ref(p))
        claim["evidence"].append(f"policy:{policy_ref(p)}")
    elif m:
        r.update(escalate_why=m["why"], near_policy=policy_ref(m["policy"]), uses_before=m.get("uses_before"))


# ---- rung 5 ------------------------------------------------------------------------------------------------
def ask_model(ctx, card, cid, invoices, emails, problem):
    if not ctx.model.on:
        return None
    line = card["bank_line"]
    docs = [f"[{e['email_id']}] From: {e['from']} Date: {e['date']} Subject: {e['subject']}\n{e['body']}" for e in emails]
    if cid:
        docs.append(f"[contract:{cid}]\n{ctx.tools.contract(cid)}")
    inv = "\n".join(f"  {i['invoice_id']}  issued {i['issue_date']}  due {i['due_date']}  open {i['open']} cents  {i['description']}"
                    for i in invoices) or "  (none listed)"
    customers = "" if cid else "Customers:\n" + "\n".join(f"  {c['customer_id']}  {c['name']}" for c in ctx.tools.customers()) + "\n\n"
    prompt = (f"Bank line {line['line_id']}: {line['date']}  {line['amount']} cents  \"{line['text']}\"\n"
              f"Payer on file: {cid or 'unknown'}\nProblem: {problem}\n\n{customers}Open invoices:\n{inv}\n\nDocuments:\n\n"
              + "\n\n".join(docs))
    return ctx.model.ask("cash_app", SYSTEM, prompt, SCHEMA)


def source_text(ctx, source_id, emails, cid=None):
    if source_id.startswith("contract"):
        return ctx.tools.contract(cid or source_id.split(":")[-1])
    return next((f"{e['subject']}\n{e['body']}" for e in emails if e["email_id"] == source_id.replace("email:", "")), "")


def quoted(out, emails, ctx=None, cid=None) -> bool:
    q = " ".join((out.get("quote") or "").split())
    if len(q) < 12:
        return False
    text = source_text(ctx, out["source_id"], emails, cid) if ctx else next(
        (f"{e['subject']}\n{e['body']}" for e in emails if e["email_id"] == out["source_id"].replace("email:", "")), "")
    return q in " ".join(text.split())


def verified_customer(ctx, out, emails):
    if not (out and out["confident"] and out["customer_id"] and ctx.tools.customer(out["customer_id"])):
        return None
    name = ctx.tools.customer(out["customer_id"])["name"]
    out["source_id"] = out["source_id"].replace("email:", "")
    return out["customer_id"] if quoted(out, emails) and name.lower() in out["quote"].lower() else None


def apply_model_residuals(ctx, claim, out, emails, cid):
    if not (out and out["confident"]):
        return
    for mr in out["residuals"]:
        target = next((r for r in claim["residuals"] if r["amount"] == mr["amount_cents"] and not r.get("treatment")), None)
        ok = target and mr["treatment"] in MODEL_MAY and quoted(mr, emails, ctx, cid)
        if ok and mr["treatment"] == "contract_discount" and not mr["source_id"].startswith("contract"):
            ok = False
        if ok:
            target.update(treatment=mr["treatment"], reason=TREATMENTS[mr["treatment"]]["reason"], quote=mr["quote"],
                          basis=f"model, quoting {mr['source_id']}")
            claim["evidence"].append(mr["source_id"] if ":" in mr["source_id"] else f"email:{mr['source_id']}")
            claim.update(rung=5, model=True, note=out["explanation"])


def payer_candidates(ctx, amount) -> list[dict]:
    out = []
    for c in ctx.tools.customers():
        inv = ctx.tools.open_invoices(c["customer_id"])[:10]
        for r in range(1, 4):
            for combo in combinations(inv, r):
                if sum(i["open"] for i in combo) == amount:
                    out.append({"customer_id": c["customer_id"], "name": c["name"], "invoice_ids": [i["invoice_id"] for i in combo]})
    return out[:3]


# ---- Stripe payouts ------------------------------------------------------------------------------------------
def payout_claim(ctx, card):
    line = card["bank_line"]
    po = ctx.tools.payout_for(line["text"])
    claim = {"desk": "cash_app", "customer_id": None, "settles": {}, "residuals": [], "rung": 1, "basis": "payout_itemisation",
             "evidence": [f"bank:{line['line_id']}"], "needs_human": None, "note": ""}
    if not po or sum(t["net"] for t in po["balance_transactions"]) != line["amount"] or po["amount"] != line["amount"]:
        claim.update(rung=6, needs_human={"type": "payout_mismatch", "why": "payout_itemisation_does_not_tie"})
        return claim
    disputes = [t for t in po["balance_transactions"] if t["reporting_category"] == "dispute"]
    loss, fee = -sum(t["amount"] for t in disputes), sum(t["fee"] for t in disputes)
    claim["evidence"].append(f"payout:{po['id']}")
    claim["payout"] = {"id": po["id"], "charges": sum(t["amount"] for t in po["balance_transactions"] if t["type"] == "charge"),
                       "fees": sum(t["fee"] for t in po["balance_transactions"] if t["type"] == "charge"),
                       "refunds": -sum(t["amount"] for t in po["balance_transactions"] if t["type"] == "refund"),
                       "disputes": loss, "dispute_fees": fee}
    claim["extra_lines"] = [{"account": "stripe_clearing", "debit": 0, "credit": line["amount"] + loss + fee},
                            {"account": "chargeback_losses", "debit": loss, "credit": 0},
                            {"account": "dispute_fees", "debit": fee, "credit": 0}]
    if disputes:
        claim["components"] = [{"amount": loss, "reason": "CHARGEBACK"}, {"amount": fee, "reason": "PROCESSOR_FEE"}]
    return claim


def tie_gap(amount: int, claim: dict) -> int:
    """Zero when bank amount = settled invoices - short residuals + over residuals."""
    if claim.get("payout"):
        return 0
    over = sum(r["amount"] for r in claim["residuals"] if r["side"] == "over")
    return amount - (sum(claim["settles"].values()) - short_total(claim["residuals"]) + over)
