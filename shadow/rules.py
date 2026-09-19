"""Tier 1: playbook rules compiled to checkable conditions. No model calls.

The playbook is written by the model from the client's own history and signed off by a human; this module only
executes it. A rule is a flat `when` (all conditions must hold) and a `then`. Disagreeing approved rules defer to review.
A matching rule with executable=false does not resolve anything: it hands the item to the investigator with the
rule attached, which is how judgement calls pre-empt the mechanical rules that follow them.

when (bank items unless item_kind says otherwise):
  item_kind            "bank" | "ledger"
  direction            "in" | "out"
  description_regex    case-insensitive, on the bank description (ledger items: memo)
  counterparty_regex   case-insensitive
  amount_min, amount_max          on the absolute amount
  candidate            how to find the ledger side: {"by": "ref" | "ref_in_description" | "counterparty" | "amount_near",
                       "window_days": 10, "account": "<ledger_account>", "max_entries": 1}; omitted means the rule expects none
  diff_min, diff_max   signed, diff = ledger total - bank amount (positive: bank received less / paid more)
  diff_abs_max, diff_pct_min, diff_pct_max   pct is |diff| / |ledger total| * 100
  invoice_age_days_max days from the matched entry's invoice date to the bank date (terms that only hold inside a window);
                       invoice_age_days_min is the mirror
  doc                  {"type": "<document_type>", "net_diff_abs_min": 0, "net_diff_abs_max": 0}: a structured document of
                       that type whose meta.batch_id equals the bank ref; net_diff = meta.net - bank amount
  age_days_max         ledger items: days between the entry date and period end
  posted_after_close   ledger items: true when posted more than close_days after the end of the entry's own month
then:
  action               match | match_adjust | book | escalate | carry_forward
  account              where the diff goes (match_adjust) or the whole amount goes (book)
  adjust_from_doc      {"<meta_field>": "<account>", ...}: document meta fields booked to accounts
  remainder_account    with adjust_from_doc: where any net_diff left over goes
  escalate_to          role
"""
import re
from datetime import date, timedelta
from itertools import combinations

from shadow import db
from shadow.matcher import days_between, period_end, posted_late, tokens, _evidenced


_num, _str = {"type": "number"}, {"type": "string"}
WHEN_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "item_kind": {"type": "string", "enum": ["bank", "ledger"]}, "direction": {"type": "string", "enum": ["in", "out"]},
    "description_regex": _str, "counterparty_regex": _str, "amount_min": _num, "amount_max": _num,
    "candidate": {"type": "object", "additionalProperties": False, "required": ["by"], "properties": {
        "by": {"type": "string", "enum": ["ref", "ref_in_description", "counterparty", "amount_near"]},
        "window_days": _num, "account": _str, "max_entries": _num}},
    "diff_min": _num, "diff_max": _num, "diff_abs_max": _num, "diff_pct_min": _num, "diff_pct_max": _num,
    "invoice_age_days_max": _num, "invoice_age_days_min": _num,
    "doc": {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {
        "type": _str, "net_diff_abs_min": _num, "net_diff_abs_max": _num}},
    "age_days_max": _num, "posted_after_close": {"type": "boolean"}}}
THEN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action"], "properties": {
    "action": {"type": "string", "enum": ["match", "match_adjust", "book", "escalate", "carry_forward"]},
    "account": _str, "remainder_account": _str, "escalate_to": _str,
    # document meta field -> account; the field names belong to the client's documents, so this one stays open
    "adjust_from_doc": {"type": "object", "additionalProperties": _str}}}


class Ctx:
    def __init__(self, con, period: str, ledger_open: list[dict], used: set[str]):
        self.con, self.period, self.used = con, period, used
        self.ledger_open = ledger_open
        self.end = period_end(period)
        self.close_days = db.q(con, "SELECT close_days FROM client")[0]["close_days"]

    def pool(self):
        return [e for e in self.ledger_open if e["id"] not in self.used]


def _rx(pattern, text):
    try:
        return re.search(pattern, text or "", re.I) is not None
    except re.error:
        return False


def _find_candidates(item: dict, spec: dict, when: dict, ctx: Ctx) -> list[list[dict]]:
    window = spec.get("window_days", 10)
    pool = [e for e in ctx.pool() if not posted_late(e, ctx.close_days)
            and -3 <= days_between(item["date"], e["date"]) <= window
            and (e["amount"] > 0) == (item["amount"] > 0)]
    by = spec.get("by")
    if by == "ref":
        pool = [e for e in pool if item["ref"] and e["ref"] == item["ref"]]
    elif by == "ref_in_description":
        pool = [e for e in pool if e["ref"] and len(e["ref"]) >= 4 and e["ref"].lower() in item["description"].lower()]
    elif by == "counterparty":
        want = tokens(item["counterparty"]) or tokens(item["description"])
        pool = [e for e in pool if want & tokens(e["counterparty"])]
    elif by != "amount_near":
        return []
    if spec.get("account"):
        pool = [e for e in pool if e["account"] == spec["account"]]
    hits = []
    for k in range(1, min(spec.get("max_entries", 1), 4) + 1):
        if len(pool) > 16 and k > 2:
            break
        for combo in combinations(pool, k):
            if k > 1 and not _evidenced(ctx.con, item, combo):
                continue
            if _diff_ok(round(sum(e["amount"] for e in combo) - item["amount"], 2), sum(e["amount"] for e in combo), when):
                hits.append(list(combo))
        if hits:
            break
    return hits


def _diff_ok(diff: float, ledger_total: float, when: dict) -> bool:
    if "diff_min" in when and diff < when["diff_min"] - 1e-9:
        return False
    if "diff_max" in when and diff > when["diff_max"] + 1e-9:
        return False
    if "diff_abs_max" in when and abs(diff) > when["diff_abs_max"] + 1e-9:
        return False
    pct = abs(diff) / abs(ledger_total) * 100 if ledger_total else 0.0
    if "diff_pct_min" in when and pct < when["diff_pct_min"] - 1e-9:
        return False
    if "diff_pct_max" in when and pct > when["diff_pct_max"] + 1e-9:
        return False
    return True


BANDED = {  # numeric condition -> (which measured value it bounds, side)
    "amount_max": ("amount", "upper"), "amount_min": ("amount", "lower"),
    "diff_max": ("diff", "upper"), "diff_min": ("diff", "lower"), "diff_abs_max": ("diff_abs", "upper"),
    "diff_pct_max": ("diff_pct", "upper"), "diff_pct_min": ("diff_pct", "lower"),
    "doc.net_diff_abs_max": ("net_diff_abs", "upper"), "doc.net_diff_abs_min": ("net_diff_abs", "lower"),
}
RELAX = 10.0


def get_cond(when: dict, cond: str):
    return (when.get("doc") or {}).get(cond[4:]) if cond.startswith("doc.") else when.get(cond)


def with_cond(when: dict, cond: str, value) -> dict:
    """Copy of `when` with one numeric condition replaced (None removes it)."""
    w = dict(when)
    if cond.startswith("doc."):
        w["doc"] = {k: v for k, v in (when.get("doc") or {}).items() if k != cond[4:]} | ({cond[4:]: value} if value is not None else {})
    elif value is None:
        w.pop(cond, None)
    else:
        w[cond] = value
    return w


def relaxed(when: dict, cond: str) -> dict:
    """The rule's scope with one threshold pushed far out, to see what the client did on the other side of it."""
    const, (dim, side) = get_cond(when, cond), BANDED[cond]
    if dim == "amount":
        return with_cond(when, cond, None)
    return with_cond(when, cond, (abs(const) * RELAX + 50) if side == "upper" else (0 if const >= 0 else const * RELAX - 50))


def evaluate(rule: dict, item: dict, kind: str, ctx: Ctx) -> dict | None:
    """Apply one rule. Thresholds are bands, not constants: for each numeric condition the rule knows `lo`, the
    furthest value at which the client is known to have taken this action, and `hi`, the nearest value at which it
    is known to have done something else. The rule fires on the known side, stays silent beyond `hi`, and an item
    in between is nobody's guess to make: it comes back as {"in_band": ...} and is escalated with both precedents."""
    bands = rule.get("bands") or {}
    if not bands or not rule.get("executable"):
        return _evaluate(rule, item, kind, ctx)
    if any(b.get("source") == "trail" and b.get("n_known") == 0 for b in bands.values()):
        probe = _evaluate(rule | {"executable": False}, item, kind, ctx)
        return {"defer": True, "reason": "thin_precedent"} if probe else None
    when = rule["when"]
    for cond, b in bands.items():
        side = BANDED[cond][1]
        outer = b["hi"] if side == "upper" else b["lo"]
        when = relaxed(when, cond) if outer is None else with_cond(when, cond, outer)
    out = _evaluate(rule | {"when": when}, item, kind, ctx)
    if not out or out.get("defer"):
        return out
    for cond, b in bands.items():
        dim, side = BANDED[cond]
        v = out["values"].get(dim)
        if v is None:
            continue
        if side == "upper" and b["hi"] is not None and v >= b["hi"] - 1e-9 or side == "lower" and b["lo"] is not None and v <= b["lo"] + 1e-9:
            return None
        if side == "upper" and v > b["lo"] + 1e-9 or side == "lower" and v < b["hi"] - 1e-9:
            return {"in_band": {"condition": cond, "side": side, "value": round(v, 2), "lo": b["lo"], "hi": b["hi"],
                                "lo_precedent": b.get("lo_precedent"), "hi_precedent": b.get("hi_precedent")},
                    "evidence_ids": out["evidence_ids"], "would": out["resolution"]}
    return out


def _evaluate(rule: dict, item: dict, kind: str, ctx: Ctx) -> dict | None:
    """Returns {"resolution": ...} when the rule fires, {"defer": True} when a non-executable rule claims the item
    for the investigator, or None."""
    if item["date"] < rule.get("valid_from", ""):
        return None
    when, then = rule.get("when") or {}, rule.get("then") or {}
    if when.get("item_kind", "bank") != kind:
        return None
    text = item["description"] if kind == "bank" else item["memo"]
    if "description_regex" in when and not _rx(when["description_regex"], text):
        return None
    if "counterparty_regex" in when and not _rx(when["counterparty_regex"], item["counterparty"]):
        return None
    if when.get("direction") and (when["direction"] == "in") != (item["amount"] > 0):
        return None
    if "amount_min" in when and abs(item["amount"]) < when["amount_min"]:
        return None
    if "amount_max" in when and abs(item["amount"]) > when["amount_max"]:
        return None

    evidence, ledger, diff, doc, net_diff = [], [], None, None, None
    if kind == "ledger":
        age = days_between(ctx.end, item["date"])
        if "age_days_max" in when and age > when["age_days_max"]:
            return None
        if "posted_after_close" in when:
            y, m = map(int, item["date"][:7].split("-"))
            close = date(y + (m == 12), m % 12 + 1, 1) + timedelta(days=ctx.close_days + 2)
            if (item["posted_at"] > close.isoformat()) != bool(when["posted_after_close"]):
                return None
    elif when.get("candidate"):
        hits = _find_candidates(item, when["candidate"], when, ctx)
        if len(hits) != 1:
            return None
        ledger = hits[0]
        diff = round(sum(e["amount"] for e in ledger) - item["amount"], 2)
        evidence += [e["id"] for e in ledger]
        if "invoice_age_days_max" in when or "invoice_age_days_min" in when:
            ages = [days_between(item["date"], inv[0]["date"]) for e in ledger
                    for inv in [db.q(ctx.con, "SELECT date FROM invoice WHERE id=?", e["invoice_id"] or "")] if inv]
            if len(ages) != len(ledger) or max(ages) > when.get("invoice_age_days_max", 10**6) or min(ages) < when.get("invoice_age_days_min", -10**6):
                return None
    elif any(k.startswith("diff_") for k in when):
        return None
    if when.get("doc"):
        spec = when["doc"]
        docs = [x for x in db.q(ctx.con, "SELECT * FROM document WHERE type=?", spec.get("type", ""))
                if item.get("ref") and x["meta"].get("batch_id") == item["ref"]]
        if len(docs) != 1:
            return None
        doc = docs[0]
        net_diff = round(doc["meta"].get("net", 0) - item["amount"], 2)
        if abs(net_diff) < spec.get("net_diff_abs_min", 0) - 1e-9 or abs(net_diff) > spec.get("net_diff_abs_max", 1e12) + 1e-9:
            return None
        evidence.append(doc["id"])

    if not rule.get("executable", False):
        return {"defer": True}

    action = then.get("action")
    res = {"action": action, "ledger_ids": [], "adjustments": [], "escalate_to": None}
    if action in ("match", "match_adjust"):
        if not ledger or (action == "match" and abs(diff) > 0.004):
            return None
        res["ledger_ids"] = sorted(e["id"] for e in ledger)
        if action == "match_adjust":
            if doc and then.get("adjust_from_doc"):
                fields = dict(then["adjust_from_doc"])
                remainder = then.get("remainder_account") or fields.pop("remainder_account", None)   # accept either placement
                for field, account in fields.items():
                    if doc["meta"].get(field):
                        res["adjustments"].append({"account": account, "amount": round(doc["meta"][field], 2)})
                rest = round(diff - sum(a["amount"] for a in res["adjustments"]), 2)
                if abs(rest) > 0.004:
                    if not remainder:
                        return None
                    res["adjustments"].append({"account": remainder, "amount": rest})
            elif then.get("account") and abs(diff) > 0.004:
                res["adjustments"] = [{"account": then["account"], "amount": diff}]
            else:
                return None
    elif action == "book":
        if not then.get("account") or kind != "bank":
            return None
        res["adjustments"] = [{"account": then["account"], "amount": round(-item["amount"], 2)}]
    elif action == "escalate":
        res["escalate_to"] = then.get("escalate_to")
    elif action != "carry_forward" or kind != "ledger":
        return None
    total = sum(e["amount"] for e in ledger)
    values = {"amount": abs(item["amount"]), "diff": diff, "diff_abs": abs(diff) if diff is not None else None,
              "diff_pct": abs(diff) / abs(total) * 100 if ledger and total else None,
              "net_diff_abs": abs(net_diff) if net_diff is not None else None}
    return {"resolution": res, "evidence_ids": evidence, "diff": diff, "values": values}


def apply(playbook: dict, item: dict, kind: str, ctx: Ctx, include_proposed: bool = False) -> tuple[dict | None, dict | None]:
    """Matching approved rules must agree. Returns (rule, outcome), or (None, None).

    Proposed rules never resolve anything in a live run: a matching proposed rule hands the item to the investigator
    as guidance, exactly like a judgement rule."""
    selected = None
    for rule in playbook.get("rules", []):
        if rule.get("status") == "retired":
            continue
        unresolved = rule.get("awaiting_senior") or (rule.get("open_question") and not rule.get("human_confirmed"))
        if (rule.get("status") != "approved" or unresolved) and not include_proposed:
            out = evaluate(rule | {"executable": False}, item, kind, ctx)
            if out:
                return rule, out
            continue
        out = evaluate(rule, item, kind, ctx)
        if out and out.get("in_band"):
            # borrow the addressee from whichever rule covers the far side of the line, if one does
            later = playbook["rules"][playbook["rules"].index(rule) + 1:]
            far = next((r for r in later if r.get("status") != "retired" and (r.get("then") or {}).get("action") == "escalate"
                        and _evaluate(r | {"executable": True}, item, kind, ctx)), None)
            out["escalate_to"] = (far or {}).get("then", {}).get("escalate_to")
        if out:
            if include_proposed:  # Audit the ordered proposal; live execution still rejects disagreement.
                return rule, out
            if selected is None:
                selected = (rule, out)
            elif out != selected[1] and out.get("resolution") != selected[1].get("resolution"):
                return selected[0], {"defer": True, "reason": "conflicting_precedents",
                                     "conflicting_rules": [selected[0]["id"], rule["id"]]}
    return selected or (None, None)


def plan(playbook: dict, items: list[tuple[str, dict]], ctx: Ctx, include_proposed: bool = False) -> dict[str, tuple]:
    """Evaluate every item against the playbook without consuming anything, then abstain wherever two items would
    claim the same ledger entry. Same principle as the matcher: a claim that is not mutually unique is not made.

    Returns {item_id: (rule, outcome)}; a contested item comes back as a deferral under the rule that claimed it.
    """
    out, claims = {}, {}
    for kind, item in items:
        rule, res = apply(playbook, item, kind, ctx, include_proposed)
        out[item["id"]] = (rule, res)
        for le in ((res or {}).get("resolution") or (res or {}).get("would") or {}).get("ledger_ids", []):
            claims.setdefault(le, []).append(item["id"])
    for le, claimants in claims.items():
        if len(claimants) > 1:
            for i in claimants:
                if not (out[i][1] or {}).get("in_band"):
                    out[i] = (out[i][0], {"defer": True, "contested": le})
    return out
