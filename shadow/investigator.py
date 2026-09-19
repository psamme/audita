"""Tier 2: the investigator. A tool-using model works one exception with the client's playbook in hand.

It starts from a case file assembled without model calls (the item, open ledger candidates, nearby documents,
similar past items), digs further with tools only when that is not enough, and must end by submitting one
resolution with its evidence. Code, not the model, enforces the arithmetic, the guardrails and the confidence floor.
"""
import json
import re
from datetime import date, timedelta

from shadow import db, history, llm, playbook as pbmod
from shadow.matcher import days_between, period_end, tokens

MAX_TURNS = 7
CONFIDENCE_FLOOR = 0.6
HARD_FLAGS = {"payee_bank_details_changed"}

SYSTEM = """You reconcile one bank account for {name}. {blurb}

A deterministic matcher and the client's mechanical playbook rules have already cleared everything they could. You \
get the residue, one item at a time: a bank line (or a ledger entry) that did not clear on its own. Work out what it \
is and resolve it the way this client's finance team would, or send it to the right person when you cannot be sure.

Resolutions:
- match: the bank line clears these open ledger entries exactly.
- match_adjust: it clears them with a difference, and the difference is booked to named accounts.
- book: no ledger entry exists; book the whole amount to an account.
- carry_forward: a timing item (deposit in transit, outstanding payment) that needs no entry this period.
- escalate: send it to a named role with the question they need to answer.
Adjustment amounts must sum to (sum of matched ledger amounts) minus (bank amount); for book, to minus the bank amount. \
A fee or shortfall that reduces cash is positive. Income booked with no ledger entry is negative.

A wrong match or a wrong entry is worse than an escalation. Escalate when the evidence is missing or conflicts, when \
the client's own past handling is thin or inconsistent for this kind of item, or when it looks like fraud or error \
(changed payee bank details, a payment made twice, an entry posted into a closed period). A control flag on the item \
is never cleared on the strength of a matching amount.
{playbook_block}
Chart of accounts: {chart}
Roles you can escalate to: {roles}
Period being reconciled: {period}. Nothing after it exists yet.

Read the case file first. It often contains everything you need; use tools only for what is missing. Cite what you \
relied on: evidence_ids are record ids (ledger entries, documents, invoices, bank lines), precedent_ids are past items \
handled the same way, rule_id is the playbook rule you applied. Finish by calling submit_resolution exactly once."""

PLAYBOOK_BLOCK = """
This client's playbook, induced from its own history. [approved] rules are signed off; follow them. [proposed] rules \
are unconfirmed readings of thin or noisy history: weigh them, and prefer escalating with the open question over \
acting on a guess. When nothing in the playbook or the precedents covers an item, that is itself a reason to escalate.

{rules}
"""

ZERO_SHOT_BLOCK = """
You have no information about this client's conventions or past handling. Use your professional judgement.
"""


def tool_specs(with_history: bool) -> list[dict]:
    num, s = {"type": "number"}, {"type": "string"}
    specs = [
        {"name": "search_ledger", "description": "Search ledger entries visible this period. Filters combine with AND. open_only hides entries already reconciled.",
         "input_schema": {"type": "object", "properties": {"text": s, "amount_min": num, "amount_max": num, "date_from": s, "date_to": s,
                                                           "open_only": {"type": "boolean"}}}},
        {"name": "search_bank", "description": "Search bank lines up to the end of this period (absolute amounts).",
         "input_schema": {"type": "object", "properties": {"text": s, "amount_min": num, "amount_max": num, "date_from": s, "date_to": s}}},
        {"name": "search_documents", "description": "Search emails, remittance advices, processor reports and count sheets by keyword.",
         "input_schema": {"type": "object", "properties": {"text": s, "type": s, "date_from": s, "date_to": s}}},
        {"name": "list_invoices", "description": "Invoices for a customer or vendor.",
         "input_schema": {"type": "object", "properties": {"party": s}, "required": ["party"]}},
        {"name": "get_record", "description": "Fetch any record in full by id (bank line, ledger entry, invoice, document).",
         "input_schema": {"type": "object", "properties": {"id": s}, "required": ["id"]}},
    ]
    if with_history:
        specs.append({"name": "find_precedents", "description": "Past items from earlier periods and what the ERP trail shows was done with them: links, entries booked, who handled it, how long it took.",
                      "input_schema": {"type": "object", "properties": {"text": s, "diff_min": num, "diff_max": num, "amount_min": num, "amount_max": num}}})
    specs.append({"name": "submit_resolution", "description": "Final answer for this item. Call exactly once.",
                  "input_schema": {"type": "object", "required": ["action", "rationale", "confidence"], "properties": {
                      "action": {"type": "string", "enum": ["match", "match_adjust", "book", "carry_forward", "escalate"]},
                      "ledger_ids": {"type": "array", "items": s},
                      "adjustments": {"type": "array", "items": {"type": "object", "properties": {"account": s, "amount": num}, "required": ["account", "amount"]}},
                      "escalate_to": s, "rationale": s, "rule_id": s,
                      "questions": {"type": "array", "items": s, "description": "when escalating: the specific questions the person needs to answer, one per entry"},
                      "precedent_ids": {"type": "array", "items": s}, "evidence_ids": {"type": "array", "items": s},
                      "confidence": num}}})
    return specs


class Desk:
    """Everything the investigator may look at, cut off at the period being reconciled."""

    def __init__(self, con, period: str, used: set[str], with_history: bool):
        self.con, self.period, self.used, self.with_history = con, period, used, with_history
        self.end = period_end(period)
        self.doc_cutoff = (date.fromisoformat(self.end) + timedelta(days=5)).isoformat()
        self.prior_links = {r["ledger_id"] for r in db.q(con, "SELECT ledger_id FROM reconcile_link WHERE period < ? AND undone_at IS NULL", period)}
        self._precedents = None

    # -- tools
    def search_ledger(self, text="", amount_min=None, amount_max=None, date_from=None, date_to=None, open_only=True, limit=12):
        want = tokens(text)
        out = []
        for e in db.q(self.con, "SELECT * FROM ledger_entry WHERE period <= ? ORDER BY date DESC", self.period):
            if open_only and (e["id"] in self.used or e["id"] in self.prior_links):
                continue
            if want and not want & tokens(e["memo"], e["counterparty"], e["ref"], e["invoice_id"] or ""):
                continue
            if (amount_min is not None and abs(e["amount"]) < amount_min) or (amount_max is not None and abs(e["amount"]) > amount_max):
                continue
            if (date_from and e["date"] < date_from) or (date_to and e["date"] > date_to):
                continue
            out.append(e | {"open": e["id"] not in self.used and e["id"] not in self.prior_links})
        return out[:limit]

    def search_bank(self, text="", amount_min=None, amount_max=None, date_from=None, date_to=None, limit=12):
        want = tokens(text)
        out = []
        for b in db.q(self.con, "SELECT * FROM bank_line WHERE date <= ? ORDER BY date DESC", self.end):
            if want and not want & tokens(b["description"], b["counterparty"], b["ref"]):
                continue
            if (amount_min is not None and abs(b["amount"]) < amount_min) or (amount_max is not None and abs(b["amount"]) > amount_max):
                continue
            if (date_from and b["date"] < date_from) or (date_to and b["date"] > date_to):
                continue
            out.append(b)
        return out[:limit]

    def _docs(self):
        for d in db.q(self.con, "SELECT * FROM document WHERE date <= ? ORDER BY date DESC", self.doc_cutoff):
            if d["type"] == "internal_email" and (not self.with_history or d["meta"].get("period", "") >= self.period):
                continue
            yield d

    def search_documents(self, text="", type=None, date_from=None, date_to=None, limit=6):
        want = tokens(text)
        scored = []
        for d in self._docs():
            if type and d["type"] != type:
                continue
            if (date_from and d["date"] < date_from) or (date_to and d["date"] > date_to):
                continue
            hit = len(want & tokens(d["subject"], d["body"], d["sender"], json.dumps(d["meta"])))
            if want and not hit:
                continue
            scored.append((hit, d))
        scored.sort(key=lambda x: (-x[0], x[1]["date"]), reverse=False)
        return [d | {"body": d["body"][:900]} for _, d in scored[:limit]]

    def list_invoices(self, party):
        want = tokens(party)
        return [i for i in db.q(self.con, "SELECT * FROM invoice WHERE date <= ? ORDER BY date DESC", self.end)
                if want & tokens(i["party"])][:15]

    def get_record(self, id):
        for table in ("bank_line", "ledger_entry", "invoice", "document"):
            r = db.q(self.con, f"SELECT * FROM {table} WHERE id=?", id)
            if r and (r[0].get("date", "") <= self.doc_cutoff):
                return r[0]
        return {"error": "no such record visible in this period"}

    def precedents(self):
        if self._precedents is None:
            self._precedents = pbmod.cases(self.con, self.period) if self.with_history else []
        return self._precedents

    def find_precedents(self, text="", diff_min=None, diff_max=None, amount_min=None, amount_max=None, limit=6):
        want = tokens(text)
        scored = []
        for c in self.precedents():
            if diff_min is not None and (c.get("diff") is None or c["diff"] < diff_min):
                continue
            if diff_max is not None and (c.get("diff") is None or c["diff"] > diff_max):
                continue
            if (amount_min is not None and abs(c["amount"]) < amount_min) or (amount_max is not None and abs(c["amount"]) > amount_max):
                continue
            hit = len(want & tokens(c["text"], c["counterparty"]))
            if want and not hit:
                continue
            scored.append((hit, c))
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:limit]]

    # -- case file
    def case_file(self, item: dict, kind: str, flags: list, rule: dict | None) -> dict:
        text = item.get("description") or item.get("memo")
        words = " ".join([text, item["counterparty"] or "", item["ref"] or ""])
        near = lambda e: -8 <= days_between(item["date"], e["date"]) <= 25
        cands = []
        if kind == "bank":
            pool = [e for e in self.search_ledger(open_only=True, limit=10_000) if near(e) and (e["amount"] > 0) == (item["amount"] > 0)]
            by_text = [e for e in pool if tokens(words) & tokens(e["memo"], e["counterparty"], e["ref"])]
            by_amt = sorted(pool, key=lambda e: abs(e["amount"] - item["amount"]))[:5]
            seen = set()
            for e in by_text[:10] + by_amt:
                if e["id"] not in seen:
                    seen.add(e["id"])
                    cands.append(e)
        lo = (date.fromisoformat(item["date"]) - timedelta(days=20)).isoformat()
        docs = self.search_documents(words, date_from=lo, limit=5)
        cf = {"item_kind": kind, "item": item, "control_flags": flags, "open_ledger_candidates": cands, "documents": docs}
        if rule:
            cf["playbook_rule_that_claimed_this_item"] = {"id": rule["id"], "status": rule["status"], "text": rule["text"],
                                                           "open_question": rule.get("open_question")}
        if self.with_history:
            cf["similar_past_items"] = self.find_precedents(re.sub(r"\d+", " ", words), limit=5)
        return cf


def investigate(con, client_info: dict, period: str, item: dict, kind: str, used: set[str], pb: dict | None,
                flags: list, rule: dict | None) -> dict:
    """Returns {"resolution", "trace", "usage"} for one item."""
    with_history = pb is not None
    desk = Desk(con, period, used, with_history)
    roles = sorted({history.role_key(u["role"]) for u in db.q(con, "SELECT * FROM user WHERE senior=1")})
    block = PLAYBOOK_BLOCK.format(rules=pbmod.render(pb)) if with_history else ZERO_SHOT_BLOCK
    system = SYSTEM.format(name=client_info["name"], blurb=client_info["blurb"], playbook_block=block,
                           chart=json.dumps(client_info["chart"]), roles=roles, period=period)
    usage, trace = llm.Usage(), []
    cf = desk.case_file(item, kind, flags, rule)
    trace.append({"step": 1, "kind": "case_file", "label": "case file assembled (no model call)", "input": {},
                  "output": f"{len(cf['open_ledger_candidates'])} ledger candidates, {len(cf['documents'])} documents, "
                            f"{len(cf.get('similar_past_items', []))} similar past items",
                  "ids": [e["id"] for e in cf["open_ledger_candidates"]] + [d["id"] for d in cf["documents"]]})
    messages = [{"role": "user", "content": "Case file:\n" + json.dumps(cf, indent=1, default=str)}]
    tools = tool_specs(with_history)
    final, complaint = None, None
    for turn in range(MAX_TURNS):
        reply = llm.call(system, messages, tools=tools, max_tokens=8000, usage=usage)
        if reply.stop_reason == "refusal" or not reply.tool_calls:
            if reply.tool_calls == [] and turn < MAX_TURNS - 1 and reply.stop_reason != "refusal":
                messages += [{"role": "assistant", "content": reply.raw_content or "(no tool call)"},
                             {"role": "user", "content": "Call submit_resolution to finish, or another tool to keep looking."}]
                continue
            break
        messages.append({"role": "assistant", "content": reply.raw_content})
        results = []
        for call in reply.tool_calls:
            if call["name"] == "submit_resolution":
                complaint = validate(call["input"], item, kind, desk, client_info)
                if complaint and turn < MAX_TURNS - 1:
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": "Rejected: " + complaint, "is_error": True})
                    trace.append({"step": len(trace) + 1, "kind": "check", "label": "resolution rejected by code", "input": call["input"], "output": complaint, "ids": []})
                else:
                    final = call["input"]
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": "recorded"})
                continue
            fn = getattr(desk, call["name"], None)
            try:
                out = fn(**call["input"]) if fn and call["name"] in {t["name"] for t in tools} else {"error": "unknown tool"}
            except TypeError as e:
                out = {"error": str(e)}
            body = json.dumps(out, default=str)
            ids = [r["id"] for r in out if isinstance(r, dict) and "id" in r] if isinstance(out, list) else ([out["id"]] if isinstance(out, dict) and "id" in out else [])
            trace.append({"step": len(trace) + 1, "kind": "tool_call", "label": call["name"], "input": call["input"],
                          "output": f"{len(out)} results" if isinstance(out, list) else body[:200], "ids": ids})
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": body[:12000]})
        if final:
            break
        messages.append({"role": "user", "content": results})

    res = finalize(final, complaint, flags, roles)
    trace.append({"step": len(trace) + 1, "kind": "final", "label": res["action"], "input": {}, "output": res["rationale"],
                  "ids": res["evidence_ids"]})
    return {"resolution": res, "trace": trace, "usage": usage.as_dict()}


def validate(r: dict, item: dict, kind: str, desk: Desk, info: dict) -> str | None:
    action = r.get("action")
    ids = r.get("ledger_ids") or []
    adj = r.get("adjustments") or []
    if action in ("match", "match_adjust"):
        if kind != "bank" or not ids:
            return "match needs a bank item and at least one ledger id"
        entries = [db.q(desk.con, "SELECT * FROM ledger_entry WHERE id=? AND period <= ?", i, desk.period) for i in ids]
        if not all(entries):
            return "one of those ledger ids does not exist in this period"
        if any(i in desk.used or i in desk.prior_links for i in ids):
            return "one of those ledger entries is already reconciled to another bank line"
        total = round(sum(e[0]["amount"] for e in entries), 2)
        want = round(total - item["amount"], 2)
        got = round(sum(a["amount"] for a in adj), 2)
        if action == "match" and abs(want) > 0.004:
            return f"ledger total {total} differs from the bank amount {item['amount']} by {want}; that is not an exact match"
        if action == "match_adjust" and abs(want - got) > 0.011:
            return f"adjustments sum to {got} but ledger total minus bank amount is {want}"
    if action == "book":
        if kind != "bank" or abs(round(sum(a["amount"] for a in adj), 2) + item["amount"]) > 0.011:
            return f"book adjustments must sum to {-item['amount']}"
    if action in ("match_adjust", "book") and any(a["account"] not in info["chart"] for a in adj):
        return "an adjustment account is not in the chart of accounts"
    if action == "escalate" and not r.get("escalate_to"):
        return "escalate needs escalate_to"
    return None


def finalize(final: dict | None, complaint: str | None, flags: list, roles: list[str]) -> dict:
    base = {"action": "escalate", "ledger_ids": [], "adjustments": [], "escalate_to": roles[0] if roles else None,
            "rationale": "", "rule_id": None, "precedent_ids": [], "evidence_ids": [], "confidence": 0.0, "proposed": None,
            "questions": []}
    if final is None or complaint:
        return base | {"rationale": "The investigator could not produce a valid resolution" + (f" ({complaint})" if complaint else "") + ". Left for review."}
    res = base | {k: final.get(k) or base[k] for k in base if k != "proposed"} | {"action": final["action"], "confidence": float(final.get("confidence") or 0)}
    hard = [f for f in flags if f["flag"] in HARD_FLAGS]
    if res["action"] != "escalate" and hard:
        return res | {"action": "escalate", "ledger_ids": [], "adjustments": [], "proposed": final, "escalate_to": final.get("escalate_to") or base["escalate_to"],
                      "rationale": f"Control: {hard[0]['detail']}. Held for verification regardless of the match. Investigator's view: {res['rationale']}"}
    if res["action"] != "escalate" and res["confidence"] < CONFIDENCE_FLOOR:
        return res | {"action": "escalate", "ledger_ids": [], "adjustments": [], "proposed": final, "escalate_to": final.get("escalate_to") or base["escalate_to"],
                      "rationale": f"Confidence {res['confidence']:.2f} is under the floor. Proposed: {res['rationale']}"}
    if res["action"] != "escalate":
        res["escalate_to"] = None
    return res
