"""The learning loop. A human correction, or a controller's answer to an open question, becomes a playbook diff.

The model proposes the smallest patch that would have produced the human's answer; code then checks that the
patched playbook really does reproduce it on the corrected item, back-tests the result against history, and saves
a new version whose cause is the correction. Works from an empty playbook, which is the cold-start path.
"""
import json
from datetime import datetime

from grade import same
from shadow import db, guardrails, llm, matcher, playbook as pbmod, rules

PATCH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["explanation", "ops"],
    "properties": {"explanation": {"type": "string"}, "ops": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["op"],
        "properties": {"op": {"type": "string", "enum": ["add", "modify", "retire", "approve"]},
                       "rule_id": {"type": ["string", "null"]}, "insert_before": {"type": ["string", "null"]},
                       "text": {"type": "string"}, "executable": {"type": "boolean"},
                       "when": rules.WHEN_SCHEMA, "then": rules.THEN_SCHEMA}}}},
}

SYSTEM = """You maintain a client's reconciliation playbook. Someone at the client has just told you something: either \
they corrected how an item was handled, or they answered a question the playbook had left open. When they answer a \
question, act on what they actually said: approve the rule only if they confirmed it, modify it if they gave a \
different threshold, account or addressee, retire it if they said that is not how they work. Turn what they said \
into the smallest change to the playbook that makes it true from now on.

- Generalise exactly as far as their words justify. "That supplier always rounds to whole units, anything under one unit goes to the \
rounding account" is a rule about that supplier and differences under one unit, not about all suppliers.
- Prefer modifying the rule that got it wrong (a threshold, an account, who it goes to) over adding a new one. Add a \
rule when nothing covers the case. Retire a rule they contradicted outright. Approve a proposed rule they confirmed.
- A new rule that should win over an existing broader rule must be inserted before it (insert_before). Rules are tried \
in order and the first match wins.
- Write the rule text as one plain sentence in the client's terms that stands alone: every amount and account stated \
in the sentence, no reference to other rules. No hedging, no em dashes.
- executable is true only when the rule language below can fully check the condition. Otherwise false, with a `when` \
that still scopes which items the rule claims for the investigator.
- If what they said changes nothing (they confirmed the playbook was right), return no ops.

Rule language:
when (bank items""" + rules.__doc__.split("when (bank items", 1)[1]


def _summary(e: dict) -> str:
    """One line a person can read in a list of everything the playbook was ever told."""
    t = e.get("type")
    if t == "correction":
        return f"Corrected {e.get('item_id')}: {e.get('note')}"
    if t == "interview" and "condition" in e:
        return f"Asked about {e['value']:,.2f} on {e['rule_id']}: " + ("send for review" if e["review"] else "handle the usual way")
    if t == "interview":
        return f"Answered {e.get('rule_id')}: {e.get('answer')}"
    if t == "conflict":
        return f"Conflict with {e.get('rule_id')} raised by {e.get('by_role') or 'a reviewer'}: {e.get('note')}"
    if t == "conflict_resolved":
        return f"Conflict {e.get('conflict_id')} settled as {e.get('outcome')}"
    if t == "retraction":
        return f"Retracted {e.get('retracted')}" + (f": {e['note']}" if e.get("note") else "")
    return t or ""


def _log(client: str, entry: dict) -> dict:
    path = db.DATA / client / "corrections.jsonl"
    n = len(path.read_text().splitlines()) if path.exists() else 0
    entry = {"correction_id": f"{client}-COR-{n + 1:04d}", "at": datetime.now().isoformat(timespec="seconds")} | entry
    entry["summary"] = _summary(entry)
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry


def _stated_bands(rule: dict) -> dict:
    """A threshold a person stated is a closed band: no ignorance left on that condition."""
    out = {}
    for cond, (dim, side) in rules.BANDED.items():
        v = rules.get_cond(rule.get("when") or {}, cond)
        if v is not None and pbmod._is_policy_line(cond, v, (rule.get("then") or {}).get("action")):
            out[cond] = {"lo": v, "hi": round(v + 0.01, 2), "side": side, "source": "stated", "written": v, "n_known": 0, "n_other": 0,
                         "lo_precedent": None, "hi_precedent": None, "beyond": []} if side == "upper" else \
                        {"lo": round(v - 0.01, 2), "hi": v, "side": side, "source": "stated", "written": v, "n_known": 0, "n_other": 0,
                         "lo_precedent": None, "hi_precedent": None, "beyond": []}
    return out


def _apply_ops(pb: dict, ops: list[dict], client: str, origin: str) -> dict:
    rs = [dict(r) for r in pb["rules"]]
    nxt = max([int(r["id"].rsplit("-", 1)[1]) for r in rs] + [0]) + 1
    for op in ops:
        idx = next((i for i, r in enumerate(rs) if r["id"] == op.get("rule_id")), None)
        if op["op"] == "retire" and idx is not None:
            rs[idx]["status"] = "retired"
        elif op["op"] == "approve" and idx is not None:
            rs[idx] |= {"status": "approved", "human_confirmed": True, "open_question": None}
            if rs[idx].pop("executable_if_approved", None):
                rs[idx]["executable"] = True
        elif op["op"] == "modify" and idx is not None:
            rs[idx] |= {k: op[k] for k in ("text", "executable", "when", "then") if k in op}
            rs[idx] |= {"status": "approved", "human_confirmed": True, "open_question": None, "origin_of_change": origin}
            if "when" in op:
                rs[idx]["bands"] = _stated_bands(rs[idx])
        elif op["op"] == "add" and "text" in op:
            op.setdefault("assigned_id", f"{client}-R-{nxt:03d}")    # kept in the stored patch so a replay gives the same id
            rule = {"id": op["assigned_id"], "text": op["text"], "executable": bool(op.get("executable")),
                    "when": op.get("when") or {}, "then": op.get("then") or {}, "origin": origin, "precedent_ids": [],
                    "status": "approved", "human_confirmed": True, "open_question": None, "version_added": pb.get("version", 0) + 1}
            rule["bands"] = _stated_bands(rule)
            nxt += 1
            at = next((i for i, r in enumerate(rs) if r["id"] == op.get("insert_before")), len(rs))
            rs.insert(at, rule)
    return pb | {"rules": rs}


def _reproduces(con, pb: dict, period: str, item: dict, kind: str, human: dict) -> tuple[bool, str]:
    flagged = guardrails.scan(con, period)
    _, used = matcher.run(con, period, skip=set(flagged))
    ctx = rules.Ctx(con, period, matcher.open_ledger(con, period), set(used))
    rule, out = rules.apply(pb, item, kind, ctx)
    if not rule:
        return False, "no rule in the patched playbook matches the corrected item"
    if out.get("defer"):
        return True, f"{rule['id']} now sends this kind of item to the investigator with the new guidance"
    if same(out["resolution"], human):
        return True, f"{rule['id']} now reproduces the human's resolution on this item"
    return False, f"{rule['id']} fires first and gives {json.dumps(out['resolution'])} instead of the human's resolution"


def _finish(con, client, track, pb_old, pb_new, cause, period=None):
    before = pb_new.get("trained_before") or period
    pb_new["trained_before"] = before
    keep = {r["id"]: (r["status"], r.get("human_confirmed")) for r in pb_new["rules"]}
    pbmod.backtest(con, pb_new)
    for r in pb_new["rules"]:       # what a human stated stays approved whatever the noisy trail says
        status, confirmed = keep[r["id"]]
        if status == "retired" or confirmed:
            r["status"] = status
            if confirmed:
                r["open_question"] = None
    saved = pbmod.save(client, track, {k: v for k, v in pb_new.items() if k not in ("version", "created_at", "cause")}, cause)
    return saved, pbmod.diff(pb_old | {"version": pb_old.get("version", 0)}, saved)


def answer_band(client: str, track: str, rule_id: str, condition: str, value: float, review: bool) -> dict:
    """Yes/no answer to a band question. Instant: no model call."""
    entry = _log(client, {"type": "interview", "source": "interview", "rule_id": rule_id, "condition": condition,
                          "value": value, "review": review})
    return pbmod.answer_band(client, track, rule_id, condition, value, review, entry["correction_id"]) | {"correction_id": entry["correction_id"]}


def correct(client: str, track: str, item: dict, human: dict, note: str, run_id: str = "", usage: llm.Usage | None = None,
            role: str | None = None, force: bool = False, valid_from: str | None = None) -> dict:
    """item: an Item from a run (record, item_kind, resolution, trace). human: the Resolution the person chose.

    Who may teach: a correction that makes the agent more cautious applies at once. One that contradicts a signed-off
    rule with healthy support is not applied; it is raised as a conflict for a senior to settle. One that widens what
    is auto-resolved, taught by someone who is not senior, lands as a proposed rule until a senior confirms it.
    """
    con = db.connect(client, readonly=True)
    pb_old = pbmod.load(client, track) or {"version": 0, "rules": [], "trained_before": None}
    period = item["record"]["period"]
    seniors = {u["role"].lower().replace(" ", "_") for u in db.q(con, "SELECT * FROM user WHERE senior=1")}
    is_senior = role is None or role in seniors
    fired = next((r for r in pb_old["rules"] if r["id"] == item["resolution"].get("rule_id")), None)
    if (not force and fired and item.get("tier") == "rule" and item["resolution"]["action"] != "escalate" and human["action"] != "escalate"
            and fired["status"] == "approved" and fired.get("backtest", {}).get("support", 0) >= pbmod.APPROVE_MIN_SUPPORT
            and not same(item["resolution"], human)):
        conflict = _log(client, {"type": "conflict", "status": "open", "run_id": run_id, "item": item, "human": human, "note": note,
                                 "by_role": role, "rule_id": fired["id"], "rule_text": fired["text"],
                                 "rule_support": fired["backtest"]["support"],
                                 "outcomes": ["one_off_exception", "policy_change", "mistake"]})
        return {"correction_id": conflict["correction_id"], "diff": None, "new_version": pb_old.get("version", 0),
                "conflict": {k: conflict[k] for k in ("correction_id", "rule_id", "rule_text", "rule_support", "note", "by_role", "outcomes")},
                "explanation": f"This contradicts {fired['id']}, which is signed off and agrees with {fired['backtest']['support']} past items. "
                               "Nothing was changed. A senior needs to say whether this is a one-off exception, a change of policy, or a mistake.",
                "check": "conflict raised"}
    entry = _log(client, {"type": "correction", "run_id": run_id, "item_id": item["item_id"], "note": note,
                          "agent": item["resolution"], "human": human, "playbook_version": pb_old.get("version", 0)})
    info = db.q(con, "SELECT * FROM client")[0]
    ask = {"chart_of_accounts": info["chart"], "item": item["record"], "item_kind": item["item_kind"],
           "what_the_system_did": {k: item["resolution"].get(k) for k in ("action", "ledger_ids", "adjustments", "escalate_to", "rule_id", "rationale")},
           "what_the_human_did": human, "what_the_human_said": note,
           "ledger_entries_involved": [db.q(con, "SELECT * FROM ledger_entry WHERE id=?", i)[0] for i in human.get("ledger_ids") or []],
           "current_playbook": [{k: r.get(k) for k in ("id", "status", "executable", "text", "when", "then")} for r in pb_old["rules"] if r["status"] != "retired"]}
    messages = [{"role": "user", "content": json.dumps(ask, indent=1, default=str)}]
    verdict = ""
    for attempt in range(2):
        reply = llm.call(SYSTEM, messages, schema=PATCH_SCHEMA, max_tokens=8000, usage=usage)
        patch = json.loads(reply.text)
        pb_new = _apply_ops(pb_old, patch["ops"], client, f"correction {entry['correction_id']}")
        touched = {op.get("assigned_id") or op.get("rule_id") for op in patch["ops"]}
        for r in pb_new["rules"]:
            if r["id"] in touched and valid_from:
                r["valid_from"] = valid_from          # precedents before this date stop counting toward its bands
            if r["id"] in touched and not is_senior and (r.get("then") or {}).get("action") != "escalate" and r["status"] == "approved":
                r |= {"status": "proposed", "human_confirmed": False, "awaiting_senior": True,
                      "open_question": f"Taught by {role}: \"{note}\". This widens what is resolved without review. Does a senior confirm it?"}
        if not patch["ops"] or human["action"] == "escalate" and not patch["ops"]:
            verdict = "no change needed"
            break
        ok, verdict = _reproduces(con, pb_new, period, item["record"], item["item_kind"], human)
        if ok:
            break
        messages += [{"role": "assistant", "content": reply.text},
                     {"role": "user", "content": f"Checked by code: {verdict}. Fix the patch (conditions, order or amounts) so the corrected item comes out the human's way."}]
    if not patch["ops"]:
        return {"correction_id": entry["correction_id"], "diff": None, "new_version": pb_old.get("version", 0),
                "explanation": patch["explanation"], "check": verdict}
    cause = {"type": "correction", "correction_id": entry["correction_id"], "item_id": item["item_id"], "note": note,
             "explanation": patch["explanation"], "check": verdict, "by_role": role,
             "patch": {"kind": "ops", "ops": patch["ops"], "origin": f"correction {entry['correction_id']}"}}
    saved, d = _finish(con, client, track, pb_old, pb_new, cause, period)
    return {"correction_id": entry["correction_id"], "diff": d, "new_version": saved["version"],
            "explanation": patch["explanation"], "check": verdict}


def answer(client: str, track: str, rule_id: str, answer_text: str, usage: llm.Usage | None = None) -> dict:
    """The controller answers a proposed rule's open question; the playbook absorbs the answer."""
    con = db.connect(client, readonly=True)
    pb_old = pbmod.load(client, track)
    rule = next(r for r in pb_old["rules"] if r["id"] == rule_id)
    entry = _log(client, {"type": "interview", "rule_id": rule_id, "question": rule.get("open_question"), "answer": answer_text,
                          "playbook_version": pb_old["version"]})
    info = db.q(con, "SELECT * FROM client")[0]
    ask = {"chart_of_accounts": info["chart"],
           "rule_in_question": {k: rule.get(k) for k in ("id", "status", "executable", "text", "when", "then")},
           "question_you_asked": rule.get("open_question"), "what_the_controller_answered": answer_text,
           "current_playbook": [{k: r.get(k) for k in ("id", "status", "executable", "text", "when", "then")} for r in pb_old["rules"] if r["status"] != "retired"]}
    reply = llm.call(SYSTEM, [{"role": "user", "content": json.dumps(ask, indent=1, default=str)}], schema=PATCH_SCHEMA, max_tokens=8000, usage=usage)
    patch = json.loads(reply.text)
    if not patch["ops"]:       # no explicit change: the rule stays exactly as it was, still proposed
        return {"correction_id": entry["correction_id"], "diff": None, "new_version": pb_old["version"], "explanation": patch["explanation"]}
    pb_new = _apply_ops(pb_old, patch["ops"], client, f"interview {entry['correction_id']}")
    cause = {"type": "interview", "correction_id": entry["correction_id"], "rule_id": rule_id, "note": answer_text,
             "explanation": patch["explanation"],
             "patch": {"kind": "ops", "ops": patch["ops"], "origin": f"interview {entry['correction_id']}"}}
    saved, d = _finish(con, client, track, pb_old, pb_new, cause)
    return {"correction_id": entry["correction_id"], "diff": d, "new_version": saved["version"], "explanation": patch["explanation"]}


def resolve_conflict(client: str, track: str, conflict_id: str, outcome: str, role: str | None = None, usage: llm.Usage | None = None) -> dict:
    """A senior settles a conflict: one_off_exception | policy_change | mistake."""
    path = db.DATA / client / "corrections.jsonl"
    conflict = next(json.loads(l) for l in path.read_text().splitlines() if json.loads(l).get("correction_id") == conflict_id)
    item = conflict["item"]
    result = {"conflict_id": conflict_id, "outcome": outcome, "diff": None}
    if outcome == "policy_change":
        result |= correct(client, track, item, conflict["human"], conflict["note"], conflict.get("run_id", ""), usage, role=role,
                          force=True, valid_from=item["record"]["date"])
    elif outcome == "one_off_exception":     # recorded, never becomes a precedent
        pb = pbmod.load(client, track)
        pb["excluded_precedents"] = sorted(set(pb.get("excluded_precedents") or []) | {item["item_id"]})
        saved = pbmod.save(client, track, {k: v for k, v in pb.items() if k not in ("version", "created_at", "cause")},
                           {"type": "one_off_exception", "correction_id": conflict_id, "item_id": item["item_id"], "note": conflict["note"]})
        result["new_version"] = saved["version"]
    _log(client, {"type": "conflict_resolved", "conflict_id": conflict_id, "outcome": outcome, "by_role": role})
    return result
