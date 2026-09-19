"""The playbook: a client's exception-handling conventions, induced from how its humans resolved past exceptions.

Stored as versioned JSON (data/<client>/playbook/<track>/vN.json) so every change is a diff. Each rule is readable
text plus machine conditions, cites the ledger and bank records behind it, and carries a measured back-test: the
rule is replayed over the history it was induced from and scored against what the ERP trail shows was done.
Where the trail is thin or contradicts itself the rule stays proposed and carries a question for the controller.
"""
import json
import re
from collections import defaultdict
from datetime import datetime

from shadow import db, guardrails, history, llm, matcher, rules

APPROVE_MIN_SUPPORT = 3
ROUTINE_DAYS = 3          # handled within this many days by whoever usually does it counts as routine handling
CONFLICT_SHARE = 0.12   # tolerated disagreement with a noisy trail before a rule loses auto-approval


def pb_dir(client: str, track: str):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", track) or not re.fullmatch(r"[A-Za-z0-9_]{1,8}", client):
        raise ValueError("bad client or track name")
    return db.DATA / client / "playbook" / track


def versions(client: str, track: str = "main") -> list[int]:
    return sorted(int(p.stem[1:]) for p in pb_dir(client, track).glob("v*.json"))


def load(client: str, track: str = "main", version: int | None = None) -> dict | None:
    vs = versions(client, track)
    if not vs or (version and version not in vs):
        return None
    return json.loads((pb_dir(client, track) / f"v{version or vs[-1]}.json").read_text())


def save(client: str, track: str, pb: dict, cause: dict) -> dict:
    vs = versions(client, track)
    pb = pb | {"client": client, "track": track, "version": (vs[-1] + 1) if vs else 1,
               "created_at": datetime.now().isoformat(timespec="seconds"), "cause": cause}
    pb_dir(client, track).mkdir(parents=True, exist_ok=True)
    (pb_dir(client, track) / f"v{pb['version']}.json").write_text(json.dumps(pb, indent=1))
    return pb


def diff(a: dict, b: dict) -> dict:
    ra, rb = {r["id"]: r for r in a["rules"]}, {r["id"]: r for r in b["rules"]}
    core = lambda r: {k: r.get(k) for k in ("text", "when", "then", "executable", "status", "bands")}
    return {"from": a["version"], "to": b["version"], "cause": b.get("cause"),
            "added": [rb[i] for i in rb if i not in ra], "removed": [ra[i] for i in ra if i not in rb],
            "changed": [{"before": ra[i], "after": rb[i]} for i in rb if i in ra and core(ra[i]) != core(rb[i])]}


def render(pb: dict, chart: dict | None = None) -> str:
    lines = []
    for r in pb["rules"]:
        bt = r.get("backtest") or {}
        tag = "auto" if r.get("executable") else "judgement"
        lines.append(f"{r['id']} [{r['status']}, {tag}, {bt.get('support', 0)} precedents, {bt.get('conflicts', 0)} conflicts, "
                     f"confidence {r.get('confidence', 0):.2f}] {r['text']}"
                     + (f"  OPEN QUESTION: {r['open_question']}" if r.get("open_question") else ""))
    return "\n".join(lines)


# --- ERP trail -> cases -> clusters --------------------------------------------------------
def cases(con, before: str) -> list[dict]:
    """One compact case per non-routine item the ERP trail shows, in periods before `before`."""
    users = {u["id"]: u["role"] for u in db.q(con, "SELECT * FROM user")}
    senior_roles = {u["role"] for u in db.q(con, "SELECT * FROM user WHERE senior=1")}
    out = []
    for o in history.observe(con, before):
        if o["routine"]:
            continue
        it = o["item"]
        c = {"id": it["id"], "period": o["period"], "item_kind": o["item_kind"], "date": it["date"],
             "text": it.get("description") or it.get("memo"), "counterparty": it["counterparty"], "ref": it["ref"],
             "amount": it["amount"], "left_open": o["open"],
             "handled_by": [users.get(u, u) for u in o["handled_by"]], "days_to_handle": o["lag_days"],
             "booked": [{"account": a["account"], "amount": a["amount"], "memo": a["memo"], "by": users.get(a["by"], a["by"])}
                        for a in o["adjustments"]],
             "approvals": o["approvals"]}
        c["senior_involved"] = bool(o["approvals"]) or any(r in senior_roles for r in c["handled_by"])
        if o["ledger"]:
            total = sum(e["amount"] for e in o["ledger"])
            c |= {"linked_ledger": [{"id": e["id"], "amount": e["amount"], "account": e["account"], "memo": e["memo"],
                                     "ref": e["ref"], "days_before_bank": matcher.days_between(it["date"], e["date"])}
                                    for e in o["ledger"]],
                  "diff": o["diff"], "diff_pct": round(abs(o["diff"]) / abs(total) * 100, 3) if total else None}
        if o["item_kind"] == "ledger":
            c |= {"account": it["account"], "posted_at": it["posted_at"], "cleared_in_period": o.get("cleared_in"),
                  "age_days_at_period_end": o.get("age_days_at_period_end")}
        out.append(c)
    return out


def clusters(cs: list[dict], examples: int = 6) -> list[dict]:
    groups = defaultdict(list)
    for c in cs:
        shape = re.sub(r"[A-Z]*\d[\w-]*", "#", c["text"] or "")
        senior = c["senior_involved"]
        sig = (c["item_kind"], shape, tuple(sorted({b["account"] for b in c["booked"]})), len(c.get("linked_ledger", [])) > 1,
               senior, c["left_open"])
        groups[sig].append(c)
    out = []
    for sig, g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        amounts = [abs(c["amount"]) for c in g]
        diffs = [c["diff"] for c in g if c.get("diff") is not None]
        pcts = [c["diff_pct"] for c in g if c.get("diff_pct") is not None]
        lags = [c["days_to_handle"] for c in g if c["days_to_handle"] is not None]
        g_sorted = sorted(g, key=lambda c: abs(c.get("diff") or c["amount"]))
        picks = ([g_sorted[0], g_sorted[-1]] + g_sorted[1:-1][:: max(1, len(g) // examples)][: examples - 2]) if len(g) > 2 else g
        out.append({"count": len(g), "item_kind": sig[0], "text_shape": sig[1], "accounts_booked": list(sig[2]),
                    "several_ledger_entries": sig[3], "someone_senior_involved": sig[4], "left_open": sig[5],
                    "abs_amount_range": [min(amounts), max(amounts)],
                    "diff_range": [min(diffs), max(diffs)] if diffs else None,
                    "diff_pct_range": [min(pcts), max(pcts)] if pcts else None,
                    "days_to_handle_range": [min(lags), max(lags)] if lags else None,
                    "examples": picks})
    return out


def doc_samples(con, before: str) -> list[dict]:
    out = {}
    for d in db.q(con, "SELECT * FROM document WHERE date < ? AND type != 'internal_email' ORDER BY date", before + "-01"):
        out.setdefault(d["type"], {"type": d["type"], "count": 0, "sample": {k: d[k] for k in ("subject", "sender", "body", "meta")}})["count"] += 1
    return list(out.values())


# --- induction -----------------------------------------------------------------------------
RULE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["rules"],
    "properties": {"rules": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["text", "executable", "when", "then", "evidence_ids", "confidence", "open_question"],
        "properties": {
            "text": {"type": "string"}, "executable": {"type": "boolean"},
            "when": rules.WHEN_SCHEMA, "then": rules.THEN_SCHEMA,
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "open_question": {"type": ["string", "null"]}}}}},
}

INDUCE_SYSTEM = """You are onboarding a new client onto a bank reconciliation service. Nobody configured anything for this \
client and nobody wrote their policies down. What you have is what their ERP and inbox retained about past \
reconciliation work: which bank lines were linked to which ledger entries, what adjustment entries were posted to \
which accounts, by whom, how many days it took, the approvals log where one exists, and some internal email. The ERP \
records what was done, almost never why. Memos are terse or empty. Expect noise: a clerk who books the same thing two \
ways, a few outright mistakes (some reversed later, some never noticed), work done outside the system that left no \
links.

From those outcomes, infer the conventions this finance team actually follows and write them as a playbook a \
controller could read, recognise, correct and sign off.

Reading the trail:
- Routine handling looks like a clerk posting the same kind of entry to the same account within a day or two.
- An item that needed someone's judgement rarely says so. It shows up as handled days later, posted or approved by \
someone senior, mentioned in an email, or still open. Treat those as "this kind of item goes to that person", and write \
the rule as an escalation to that person's role, not as the entry they eventually posted.
- Where the same kind of item is handled routinely below some size and by someone senior above it, the line between \
them is the convention. Place thresholds so they are consistent with what you were shown; do not extrapolate a \
no-review limit beyond the largest amount handled routinely.
- When one clerk's handling disagrees with the majority, the majority is the convention. Say so in open_question if it \
matters.

A deterministic matcher already clears exact one-to-one matches before the playbook is consulted, so write rules for \
what is left: differences, items with no ledger counterpart, items that went to someone, entries open at month end.

Each rule has:
- text: one plain sentence in the client's terms, with the threshold, the account number and who it goes to. No \
hedging, no em dashes. Each rule's text is shown on its own, so it must stand alone: state every amount and account \
in the sentence itself and never refer to another rule ("more than that", "as above", "otherwise"). Name the item by \
what it is across payment methods (a customer receipt, a vendor payment) unless the convention really is specific to \
wires, ACH or checks.
- when / then: machine conditions in the rule language below.
- executable: true when `when` fully captures the convention so code can apply it with no judgement. False when it \
depends on something the rule language cannot check (a pattern across several items, the content of an email, a \
contract term, whether evidence exists). A non-executable rule still needs a `when` that scopes which items it covers: \
a matching non-executable rule sends the item to an investigator with your text attached.
- evidence_ids: ids of the bank lines or ledger entries from the examples that show this convention.
- confidence: 0 to 1, your honest estimate that the controller would confirm this rule as written.
- open_question: null when the trail is clear. A rule with an open question does not execute until someone answers \
it, so ask only when the answer could change the rule's action, account, threshold or addressee, never out of \
curiosity. Otherwise the one question you would ask the controller to settle it, \
stating what you saw ("I see 2 of these, both posted by the controller 3+ days later. Is controller approval \
required?"). Thin evidence (one or two cases), conflicting handling, or a threshold you had to guess all warrant one.

Numeric thresholds you write are only starting points. After you answer, code replays each rule over the whole \
trail and replaces every threshold with a measured band: the furthest value at which this client took the rule's \
action, and the nearest value at which it did something else. Items that fall between the two are escalated, never \
guessed. So write the threshold where you believe the line is and do not pad it.

Rules are tried in order and the first match wins. Put narrow rules before broad ones, and a judgement rule before any \
mechanical rule that would otherwise swallow its cases. Where there is a line, write both sides of it as separate \
rules so nothing falls through silently. Do not invent conventions the trail does not show.

Rule language:
when (bank items""" + rules.__doc__.split("when (bank items", 1)[1]


def induce(client: str, before: str, track: str = "main", usage: llm.Usage | None = None) -> dict:
    """Induce a playbook from everything the ERP retained in periods before `before`."""
    con = db.connect(client, readonly=True)
    info = db.q(con, "SELECT * FROM client")[0]
    cl = clusters(cases(con, before))
    mail = [{"date": m["date"], "from": m["sender"], "to": m["meta"].get("to"), "subject": m["subject"], "body": m["body"]}
            for m in history.internal_mail(con, before)]
    roles = sorted({history.role_key(u["role"]) for u in db.q(con, "SELECT * FROM user WHERE senior=1")})
    prompt = (f"Client: {info['name']}. {info['blurb']}\nChart of accounts: {json.dumps(info['chart'])}\n"
              f"Senior roles an item can be sent to (use these exact strings in escalate_to): {roles}\n"
              f"The trail covers periods before {before}. Below are the {len(cl)} kinds of non-routine item found in it, "
              f"each with counts, ranges and sample cases.\n\n{json.dumps(cl, indent=1)}\n\n"
              f"Internal email from the same periods:\n{json.dumps(mail, indent=1)}\n\n"
              f"Kinds of document on file, with one sample each (rules can require a document through `doc`):\n{json.dumps(doc_samples(con, before), indent=1)}")
    reply = llm.call(INDUCE_SYSTEM, [{"role": "user", "content": prompt}], schema=RULE_SCHEMA, max_tokens=32000, usage=usage)
    raw = json.loads(reply.text)["rules"]
    pb_rules = [{"id": f"{client}-R-{n:03d}", "text": r["text"], "executable": bool(r["executable"]), "when": r["when"],
                 "then": r["then"], "origin": "induced", "version_added": 1, "precedent_ids": r["evidence_ids"][:12],
                 "model_confidence": r["confidence"], "open_question": r["open_question"], "status": "proposed"}
                for n, r in enumerate(raw, 1)]
    pb = {"trained_before": before, "rules": pb_rules}
    backtest(con, pb)
    rounds = repair(con, pb, info, usage)
    pb["findings"] = findings(con, pb)
    return save(client, track, pb, {"type": "induction", "before": before, "clusters": len(cl), "repair_rounds": rounds})


# --- back-test -----------------------------------------------------------------------------
def backtest(con, pb: dict) -> dict:
    """Replay every history period: guardrails, matcher, then the rules. Score each rule against what the trail shows.

    The trail is noisy, so a rule is trusted on measured agreement, not perfection: confidence is the smoothed share
    of replayed items where the rule's outcome equals the observed one. Rules that fall short are demoted to guidance.
    """
    from grade import same  # comparison logic only; no answer key is involved
    before = pb["trained_before"]
    gone = set(pb.get("excluded_precedents") or [])
    observed = {o["item"]["id"]: o for o in history.observe(con, before) if o["item"]["id"] not in gone}
    compute_bands(con, pb, observed)
    periods = sorted({o["period"] for o in observed.values()})
    for r in pb["rules"]:
        r["backtest"] = {"support": 0, "conflicts": 0, "conflict_examples": [], "supported_by": [], "conflict_ids": []}
    uncovered = []
    for period in periods:
        flagged = guardrails.scan(con, period)
        matches, used = matcher.run(con, period, skip=set(flagged))
        ctx = rules.Ctx(con, period, matcher.open_ledger(con, period), set(used))
        done = {m["bank_id"] for m in matches}
        items = [("bank", b) for b in db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date, id", period)
                 if b["id"] not in done]
        items += [("ledger", o["item"]) for o in observed.values() if o["item_kind"] == "ledger" and o["period"] == period]
        planned = rules.plan(pb, [(k, i) for k, i in items if not flagged.get(i["id"])], ctx, include_proposed=True)
        for kind, item in items:
            o = observed.get(item["id"])
            if not o or not o["outcome"]:
                continue
            rule, out = planned.get(item["id"], (None, None))
            if not rule:
                if not o["routine"]:
                    uncovered.append(item["id"])
                continue
            if out.get("contested") or out.get("in_band"):
                continue
            bt = rule["backtest"]
            agrees = (o["outcome"]["action"] == "escalate" or not o["routine"]) if out.get("defer") \
                else same(out["resolution"], o["outcome"])
            if agrees:
                bt["support"] += 1
                bt["supported_by"].append(item["id"])
            else:
                bt["conflicts"] += 1
                bt["conflict_ids"].append(item["id"])
                if len(bt["conflict_examples"]) < 4:
                    bt["conflict_examples"].append({"item_id": item["id"], "observed": o["outcome"],
                                                    "rule_would": None if out.get("defer") else out["resolution"]})
    for r in pb["rules"]:
        bt = r["backtest"]
        n = bt["support"] + bt["conflicts"]
        r["confidence"] = round((bt["support"] + 1) / (n + 2) * (1 if n else 0.5), 3)   # smoothed agreement with the trail
        r["precedent_ids"] = bt["supported_by"][:12]         # only precedents the replay confirmed
        r["precedent_count"] = bt["support"]
        bt["supported_by"] = len(bt["supported_by"])
        odd = [b for b in (r.get("bands") or {}).values() if b.get("categorical") and b.get("source") == "trail"]
        if odd and not r.get("open_question") and not r.get("human_confirmed"):
            r["open_question"] = ("Every case I saw was one of a few round amounts, which looks like a fee schedule rather than a range. "
                                  "Is this about the amount at all, or about something else, such as whether the charge is evidenced?")
        trusted = (bt["support"] >= APPROVE_MIN_SUPPORT and bt["conflicts"] <= CONFLICT_SHARE * n
                   and not (r.get("open_question") and not r.get("human_confirmed")))     # an unanswered question blocks execution
        if r["executable"] and not trusted and n:
            r["executable_if_approved"] = True
        r["status"] = "approved" if trusted else "proposed"
        if not trusted and not r.get("open_question"):
            r["open_question"] = (f"The trail agrees with this {bt['support']} times and disagrees {bt['conflicts']} times. "
                                  "Is this how you want it handled?") if n else \
                "I could not replay this against any past item. Is this a convention you follow?"
    pb["backtest"] = {"periods": periods, "uncovered_items": len(uncovered), "uncovered_examples": uncovered[:10]}
    return pb


# --- ignorance bands -----------------------------------------------------------------------
def _replay(con, before: str):
    """Per history period: the residue the rules would see, with the matcher already applied."""
    periods = [r["period"] for r in db.q(con, "SELECT DISTINCT period FROM bank_line WHERE period < ? ORDER BY 1", before)]
    for period in periods:
        flagged = guardrails.scan(con, period)
        matches, used = matcher.run(con, period, skip=set(flagged))
        done = {m["bank_id"] for m in matches}
        ctx = rules.Ctx(con, period, matcher.open_ledger(con, period), set(used))
        bank = [b for b in db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date, id", period)
                if b["id"] not in done and b["id"] not in flagged]
        yield ctx, bank


def _is_policy_line(cond: str, written: float, action: str) -> bool:
    """Band the conditions that express how far a convention reaches: a dollar limit on a routine handling, or the
    floor of an escalation. Exactness checks (a difference of zero), rates and sanity bounds stay as written."""
    dim, side = rules.BANDED[cond]
    if "pct" in dim or abs(written) <= 0.05:
        return False
    return side == "lower" if action == "escalate" else side == "upper"


def compute_bands(con, pb: dict, observed: dict | None = None) -> None:
    """Replace each rule's invented thresholds with what the trail supports.

    For an upper limit: hi is the smallest value at which the client did something other than the rule's action,
    lo is the largest value below that at which it took the action. Lower limits mirror this. A value the client
    handled the rule's way beyond hi is not evidence for a wider rule; it is reported as a finding.
    Bands a person stated or answered are never recomputed.
    """
    from grade import same
    observed = observed or {o["item"]["id"]: o for o in history.observe(con, pb["trained_before"])}
    replay = list(_replay(con, pb["trained_before"]))
    for rule in pb["rules"]:
        if rule.get("status") == "retired" or not (rule.get("executable") or rule.get("executable_if_approved")):
            continue
        action = (rule.get("then") or {}).get("action")
        bands = {c: b for c, b in (rule.get("bands") or {}).items() if b.get("source") in ("stated", "interview")}
        for cond in rules.BANDED:
            written = rules.get_cond(rule["when"], cond)
            if written is None or cond in bands or not _is_policy_line(cond, written, action):
                continue
            probe = {"executable": True, "then": rule["then"], "when": rules.relaxed(rule["when"], cond)}
            seen = []
            for ctx, bank in replay:
                for b in bank:
                    o = observed.get(b["id"])
                    if not o or not o["outcome"] or b["date"] < rule.get("valid_from", ""):
                        continue
                    out = rules._evaluate(probe, b, "bank", ctx)
                    v = out and out.get("values", {}).get(rules.BANDED[cond][0])
                    if v is None:
                        continue
                    if same(out["resolution"], o["outcome"]):
                        if (o["lag_days"] or 0) > ROUTINE_DAYS:
                            continue      # booked the usual way but only after days of waiting: probably asked about, so it proves nothing
                        seen.append((round(v, 2), True, b["id"]))
                    elif o["outcome"]["action"] != action:      # same action to another account is noise, not a line
                        seen.append((round(v, 2), False, b["id"]))
            upper = rules.BANDED[cond][1] == "upper"
            other = [x for x in seen if not x[1]]
            edge = (min if upper else max)(other, default=None)
            known = [x for x in seen if x[1] and (edge is None or (x[0] < edge[0] if upper else x[0] > edge[0]))]
            near = (max if upper else min)(known, default=None)
            beyond = [x[2] for x in seen if x[1] and edge is not None and (x[0] > edge[0] if upper else x[0] < edge[0])]
            band = {"lo": near[0] if near else 0.0, "hi": edge[0] if edge else None, "lo_precedent": near[2] if near else None,
                    "hi_precedent": edge[2] if edge else None} if upper else \
                   {"lo": edge[0] if edge else None, "hi": near[0] if near else None, "lo_precedent": edge[2] if edge else None,
                    "hi_precedent": near[2] if near else None}
            if not upper and band["hi"] is None:
                band["hi"] = float(rules.get_cond(rule["when"], cond))   # nothing seen on the action side: keep the written line
            distinct = sorted({x[0] for x in known})
            band["categorical"] = len(known) >= 2 and len(distinct) <= 3 and all(abs(v / 5 - round(v / 5)) < 1e-9 for v in distinct)
            bands[cond] = band | {"side": "upper" if upper else "lower", "n_known": len(known), "n_other": len(other),
                                  "written": rules.get_cond(rule["when"], cond), "source": "trail", "beyond": beyond[:5]}
        rule["bands"] = bands


def band_questions(pb: dict, limit: int = 8) -> list[dict]:
    """One hypothetical per wide band, at its midpoint. A yes/no answer moves lo or hi."""
    out = []
    for r in pb["rules"]:
        if r.get("status") == "retired" or (r.get("then") or {}).get("action") == "escalate":
            continue
        for cond, b in (r.get("bands") or {}).items():
            if b.get("side") != "upper" or b.get("source") in ("stated", "rejected") or not b.get("n_known"):
                continue
            lo, hi = b["lo"], b["hi"]
            width = None if hi is None else (hi - lo) / max(lo, 1.0)
            if width is not None and width < 0.08:
                continue
            mid = round((lo + hi) / 2, 2) if hi is not None else round(lo * 1.5 + 1, 0)
            unit = "%" if "pct" in cond else ""
            fmt = (lambda x: f"{x:,.2f}%") if unit else (lambda x: f"${x:,.2f}")
            seen = f"I have seen your team do this up to {fmt(lo)}" + (f" and handle it differently from {fmt(hi)}." if hi is not None else " and never seen a larger one.")
            ask = "limit" if hi is None else "yes_no"
            text = (f"{r['text']} {seen} Up to what amount does your team handle these without review?" if ask == "limit" else
                    f"{r['text']} {seen} If one came in at {fmt(mid)}, would you want it sent to someone for review?")
            out.append({"question_id": f"{r['id']}:{cond}", "rule_id": r["id"], "condition": cond, "value": mid, "ask": ask,
                        "answers": ["limit", "review", "usual", "not_amount"],
                        "lo": lo, "hi": hi, "relative_width": width, "rule_text": r["text"], "text": text})
    out.sort(key=lambda q: -(q["relative_width"] if q["relative_width"] is not None else 1e9))
    return out[:limit]


def apply_band_answer(new: dict, rule_id: str, condition: str, value: float | None, review: bool | None = None,
                      limit: float | None = None, not_amount: bool = False) -> dict | None:
    """Move one band in place. Returns {"before": band, "held": reason | None}, or None when the rule or band is gone.

    limit       "the limit is $X": the band closes at X in one answer.
    review      True pulls hi down to the asked value. False ("handle as usual") may only push lo up when the rule has no
                open question and its back-test has no disagreements; otherwise the answer is recorded and nothing widens.
    not_amount  "it is not about the amount": the rule stops executing and the band question becomes an open question.
    """
    rule = next((r for r in new["rules"] if r["id"] == rule_id), None)
    if not rule or condition not in (rule.get("bands") or {}):
        return None
    band = rule["bands"][condition]
    before_band, held = dict(band), None
    upper = band.get("side", "upper") == "upper"
    if not_amount:
        band |= {"source": "rejected"}
        rule |= {"status": "proposed", "human_confirmed": False,
                 "open_question": "You said this is not about the amount. What does decide how these are handled?"}
    elif limit is not None:
        band |= ({"lo": limit, "hi": round(limit + 0.01, 2)} if upper else {"lo": round(limit - 0.01, 2), "hi": limit}) | {"source": "stated"}
    elif review:
        band |= {"hi": value, "source": "interview"}
    elif rule.get("open_question") or (rule.get("backtest") or {}).get("conflicts"):
        held = "not widened: the rule still has an open question or disagrees with part of the history"
    else:
        band |= {"lo": value, "source": "interview"}
    if not held and not not_amount:
        for other in new["rules"]:     # the far side of the same line (the rule that sends larger ones to someone) moves with it
            for c, ob in (other.get("bands") or {}).items():
                if other is not rule and ob.get("side") == "lower" and rules.BANDED[c][0] == rules.BANDED[condition][0] \
                        and ob.get("lo") == before_band["lo"] and ob.get("hi") == before_band["hi"]:
                    ob |= {"lo": band["lo"], "hi": band["hi"], "source": band["source"]}
    return {"before": before_band, "held": held}


def answer_band(client: str, track: str, rule_id: str, condition: str, value: float | None = None, review: bool | None = None,
                correction_id: str | None = None, limit: float | None = None, not_amount: bool = False) -> dict:
    """Instant, no model call. See apply_band_answer for what each kind of answer does."""
    old = load(client, track)
    new = json.loads(json.dumps(old))
    res = apply_band_answer(new, rule_id, condition, value, review, limit, not_amount)
    if res is None:
        raise ValueError("no such rule or band")
    band = next(r for r in new["rules"] if r["id"] == rule_id)["bands"][condition]
    unit = "%" if "pct" in condition else "$"
    said = ("it is not about the amount." if not_amount else f"the limit is {unit}{limit:,.2f}." if limit is not None
            else f"asked about {unit}{value:,.2f}: " + ("send it for review." if review else "handle it the usual way, no review."))
    cause = {"type": "interview", "source": "interview", "correction_id": correction_id, "rule_id": rule_id, "condition": condition,
             "value": value, "note": said[0].upper() + said[1:] + (f" ({res['held']})" if res["held"] else ""), "held": res["held"],
             "band_before": {"lo": res["before"]["lo"], "hi": res["before"]["hi"]}, "band_after": {"lo": band["lo"], "hi": band["hi"]},
             "patch": {"kind": "band", "rule_id": rule_id, "condition": condition, "value": value, "review": review,
                       "limit": limit, "not_amount": not_amount}}
    saved = save(client, track, {k: v for k, v in new.items() if k not in ("version", "created_at", "cause")}, cause)
    return {"new_version": saved["version"], "diff": diff(old, saved), "band": band, "held": res["held"], "cause": cause}


# --- precedents as unit tests ----------------------------------------------------------------
PASS_SHARE = 0.85
REPAIR_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["rules"],
    "properties": {"rules": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "text", "executable", "when", "then", "open_question"],
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "executable": {"type": "boolean"},
                       "when": rules.WHEN_SCHEMA, "then": rules.THEN_SCHEMA, "open_question": {"type": ["string", "null"]}}}}},
}


def _failing(pb: dict) -> list[dict]:
    out = []
    for r in pb["rules"]:
        bt = r["backtest"]
        n = bt["support"] + bt["conflicts"]
        if r.get("executable") and (n == 0 or bt["support"] / n < PASS_SHARE):
            out.append(r)
    return out


def repair(con, pb: dict, info: dict, usage: llm.Usage | None = None, max_rounds: int = 3) -> int:
    """Run every rule against the precedents it cites and the rest of history; hand the failures back to the model
    as counterexamples. At most three rounds. What still disagrees afterwards is reported, not absorbed."""
    all_cases = {c["id"]: c for c in cases(con, pb["trained_before"])}
    for round_no in range(1, max_rounds + 1):
        bad = _failing(pb)
        if not bad:
            return round_no - 1
        ask = [{"id": r["id"], "text": r["text"], "executable": r["executable"], "when": r["when"], "then": r["then"],
                "replayed": {"agreed": r["backtest"]["support"], "disagreed": r["backtest"]["conflicts"]},
                "counterexamples": [{"case": all_cases.get(x["item_id"]), "rule_would_have": x["rule_would"]} for x in r["backtest"]["conflict_examples"]],
                "cases_you_cited": [all_cases[i] for i in r.get("precedent_ids", [])[:4] if i in all_cases]} for r in bad]
        prompt = ("These rules failed when replayed over the client's own history. Each is shown with how often it agreed, the "
                  "cases where it disagreed with what the trail shows, and cases you cited for it. A rule that never fired has "
                  "conditions that match nothing (wrong regex, wrong candidate method, wrong sign). Fix each rule, make it a "
                  "judgement rule (executable false) if the rule language cannot express it, and keep the id. Rule text must stand alone.\n\n"
                  f"Chart of accounts: {json.dumps(info['chart'])}\n\n{json.dumps(ask, indent=1, default=str)}")
        reply = llm.call(INDUCE_SYSTEM, [{"role": "user", "content": prompt}], schema=REPAIR_SCHEMA, max_tokens=16000, usage=usage)
        fixed = {r["id"]: r for r in json.loads(reply.text)["rules"]}
        for r in pb["rules"]:
            if r["id"] in fixed:
                f = fixed[r["id"]]
                r |= {"text": f["text"], "executable": bool(f["executable"]), "when": f["when"], "then": f["then"],
                      "open_question": f["open_question"], "repaired_in_round": round_no}
                r.pop("bands", None)
        backtest(con, pb)
    return max_rounds


def findings(con, pb: dict) -> list[dict]:
    """Past items that disagree with a rule the rest of history supports: probable mistakes or undocumented exceptions."""
    observed = {o["item"]["id"]: o for o in history.observe(con, pb["trained_before"])}
    users = {u["id"]: u for u in db.q(con, "SELECT * FROM user")}
    out = []
    for r in pb["rules"]:
        bt = r["backtest"]
        n = bt["support"] + bt["conflicts"]
        odd = list(bt.get("conflict_ids", [])) if n and bt["support"] / n >= PASS_SHARE else []
        odd += [i for b in (r.get("bands") or {}).values() for i in b.get("beyond", [])]
        for item_id in dict.fromkeys(odd):
            o = observed.get(item_id)
            if not o:
                continue
            who = [f"{users[u]['name']} ({users[u]['role']})" for u in o["handled_by"] if u in users]
            booked = ", ".join(f"{a['amount']:,.2f} to {a['account']}" for a in o["adjustments"]) or o["outcome"]["action"]
            out.append({"finding_id": f"{pb.get('client', '')}F-{len(out) + 1:03d}", "rule_id": r["id"], "rule_text": r["text"],
                        "item_id": item_id, "date": o["item"]["date"], "amount": o["item"]["amount"],
                        "description": o["item"].get("description") or o["item"].get("memo"), "posted_by": who,
                        "what_was_done": booked, "agreeing_cases": bt["support"],
                        "summary": f"{' and '.join(who) or 'Someone'} handled this on {o['item']['date']} as {booked}; "
                                   f"{bt['support']} comparable items were handled the way the rule describes.",
                        "evidence_ids": o["evidence_ids"]})
    return out
