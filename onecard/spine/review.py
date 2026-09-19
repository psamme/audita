"""The review queue. Turns a stuck card into one question with two to four options, each carrying its entry preview and
its effect on receivables and the forecast. Saves the human's answer as a decision trace and folds it back into the card.
"""
import copy
import json

from .contract import TREATMENTS, entry_lines, usd
from .policy import PolicyBook

WHY = {"unknown_residual_no_policy": "No policy covers this.", "outside_customer_scope": "A rule exists, but not for this customer.",
       "above_learned_amount": "A rule exists, but this amount is higher than any a human has approved.",
       "below_learned_amount": "A rule exists, but this amount is lower than any it has covered.",
       "repeat_guardrail": "A rule covers this, but it is this customer's third time this quarter.",
       "over_use_limit": "This customer has used up the rule's allowance for the quarter.",
       "above_materiality_cap": "A rule covers this, but the amount is above the materiality cap.",
       "probation_edge": "The rule is on probation and this case sits at the edge of its scope.",
       "conflicting_policies": "Two rules disagree about this case.",
       "several_combinations_tie": "Several invoice combinations fit and nothing says which the customer meant.",
       "several_near_combinations": "Several invoice combinations nearly fit.",
       "payer_matches_no_customer": "The payer name matches no customer.",
       "desks_disagree": "The desks still disagree after two rounds.",
       "audit_disagrees": "The audit desk's re-performance differs from the posted entry."}


def variant(claim: dict, idx: int, treatment: str) -> dict:
    c = copy.deepcopy(claim)
    r = c["residuals"][idx]
    r["treatment"] = treatment
    r["reason"] = TREATMENTS[treatment]["reason"] or r.get("candidate") or "SHORT_PAY"
    if treatment == "leave_open_chase":
        on = r.get("on") or max(c["settles"])
        c["settles"][on] -= r["amount"]
    return c


def build(ctx, card: dict) -> dict:
    ca = next(c for c in card["claims"] if c["desk"] == "cash_app")
    need, line = ca["needs_human"] or {"type": "desks_disagree", "why": "desks_disagree"}, card["bank_line"]
    amount, options = line["amount"], []
    name = (ctx.tools.customer(ca["customer_id"]) or {}).get("name", card["payer"]["printed"].title())

    def option(label, treatment=None, claim=None, **extra):
        o = {"id": chr(97 + len(options)), "label": label, "treatment": treatment, **extra}
        if claim is not None:
            o["entry"] = entry_lines(amount, claim)
            o["effects"] = {"receivables": -sum(claim["settles"].values()),
                            "forecast": "stays expected: " + usd(sum(r["amount"] for r in claim["residuals"] if r.get("treatment") == "leave_open_chase"))
                            if any(r.get("treatment") == "leave_open_chase" for r in claim["residuals"]) else "nothing further expected from this payment"}
        options.append(o)

    if need["type"] == "residual":
        idx = need["residual"]
        r = ca["residuals"][idx]
        if r["side"] == "short":
            invs = ", ".join(ca["settles"])
            question = f"{name} paid {usd(amount)} against {invs}, {usd(r['amount'])} short. What is the {usd(r['amount'])}?"
            order = ["absorb_bank_fee", "write_off_discount", "leave_open_chase"]
            if r.get("candidate") == "DISCOUNT_TAKEN":
                order = ["write_off_discount", "contract_discount", "leave_open_chase", "absorb_bank_fee"]
        else:
            question = f"{name} sent {usd(amount)} that matches no open invoice. Where does it go?"
            order = {"DUPLICATE_PAYMENT": ["customer_credit", "unapplied_cash"],
                     "PREPAID_CREDITS": ["deferred_revenue", "customer_credit", "unapplied_cash"]}.get(
                r.get("candidate"), ["customer_credit", "deferred_revenue", "unapplied_cash"])
        for t in order:
            option(TREATMENTS[t]["label"], t, variant(ca, idx, t), residual=idx)
        features = {"customer_id": ca["customer_id"], "reason": r.get("candidate"), "amount": r["amount"],
                    "trigger": need["why"], "uses_before": r.get("uses_before") or 0, "basis": r.get("basis")}
    elif need["type"] == "ambiguous_allocation":
        question = f"{name} paid {usd(amount)} with no remittance. Which invoices does it settle?"
        for combo in need["combos"][:4]:
            c = copy.deepcopy(ca)
            c["settles"] = {i: next(x["open"] for x in ctx.tools.open_invoices(ca["customer_id"]) if x["invoice_id"] == i) for i in combo}
            oldest = combo == need.get("oldest_first")
            option(" + ".join(combo) + (" (oldest first)" if oldest else ""), "oldest_first" if oldest else "allocation",
                   c if sum(c["settles"].values()) == amount else None, allocation=combo)
        features = {"customer_id": ca["customer_id"], "reason": "AMBIGUOUS_ALLOCATION", "amount": amount, "trigger": need["why"]}
    elif need["type"] == "unknown_payer":
        question = f"A payment of {usd(amount)} arrived from \"{card['payer']['printed']}\", who matches no customer. Whose money is it?"
        for cand in need["candidates"]:
            option(f"{cand['name']}: {' + '.join(cand['invoice_ids'])}", "alias", None, payer=cand["customer_id"])
        option(TREATMENTS["unapplied_cash"]["label"], "unapplied_cash", None)
        features = {"customer_id": None, "reason": "UNKNOWN_PAYER", "amount": amount, "trigger": need["why"]}
    else:
        failing = [c for c in card["checks"] if not c["ok"]]
        question = f"The desks could not agree on {usd(amount)} from {name}: " + "; ".join(c["detail"] or c["rule"] for c in failing)
        option("Accept the cash application desk's version", "accept_cash_app", ca)
        option(TREATMENTS["unapplied_cash"]["label"], "unapplied_cash", None)
        features = {"customer_id": ca["customer_id"], "reason": None, "amount": amount, "trigger": need["why"]}

    similar = [{"decision_id": d["decision_id"], "card_id": d["card_id"], "treatment": d["treatment"],
                "amount": d["features"]["amount"], "reason_text": d["reason_text"]}
               for d in ctx.tools.precedents(features["customer_id"], features["reason"])]
    return {"card_id": card["card_id"], "question": question, "why": need["why"], "why_text": WHY.get(need["why"], need["why"]),
            "type": need["type"], "options": options, "features": features, "similar": similar,
            "near_policy": need.get("near_policy"), "evidence": card["evidence"]}


def resolve(ctx, card: dict, item: dict, option_id: str, reason_text: str, approver: str, override: dict | None = None) -> dict:
    """Save the decision trace and fold the answer into the card. Returns the decision."""
    opt = next(o for o in item["options"] if o["id"] == option_id)
    n = ctx.store.one("SELECT COUNT(*) FROM decisions") + 1
    f = dict(item["features"])
    if opt["treatment"] in TREATMENTS and TREATMENTS[opt["treatment"]]["reason"] and item["type"] == "residual":
        # the human's choice names the cause; a chase keeps the desk's candidate cause
        f["reason"] = TREATMENTS[opt["treatment"]]["reason"]
    decision = {"decision_id": f"D-{n:04d}", "card_id": card["card_id"], "day": ctx.day, "question": item["question"],
                "options": [{"id": o["id"], "label": o["label"], "treatment": o["treatment"]} for o in item["options"]],
                "chosen": option_id, "treatment": opt["treatment"], "reason_text": reason_text, "approver": approver,
                "features": f, "saw": {"evidence": item["evidence"], "why": item["why"]}}
    res = card.setdefault("resolutions", {})
    if item["type"] == "residual":
        ca = next(c for c in card["claims"] if c["desk"] == "cash_app")
        r = ca["residuals"][opt["residual"]]
        res.setdefault("residuals", {})[f"{r['side']}:{r['amount']}"] = {
            "treatment": opt["treatment"], "decision_id": decision["decision_id"],
            "reason": TREATMENTS[opt["treatment"]]["reason"] or r.get("candidate") or "SHORT_PAY"}
    elif item["type"] == "ambiguous_allocation":
        res.update(allocation=opt["allocation"], allocation_decision=decision["decision_id"])
    elif item["type"] == "unknown_payer" and opt.get("payer"):
        res.update(payer=opt["payer"], payer_decision=decision["decision_id"])
    else:
        res["forced"] = {"treatment": opt["treatment"], "decision_id": decision["decision_id"]}
    ctx.store.x("INSERT INTO decisions VALUES (?,?,?,?)", decision["decision_id"], card["card_id"], ctx.day, json.dumps(decision))
    card["human_touch"] = True
    card.setdefault("decisions", []).append(decision["decision_id"])
    return decision
