"""The playbook: a client's exception-handling conventions, induced from how its humans resolved past exceptions.

Stored as versioned JSON (data/<client>/playbook/<track>/vN.json) so every change is a diff. Each rule is readable
text plus machine conditions, cites the precedents behind it, and carries a measured back-test: the rule is
replayed over the history it was induced from and scored against what the humans actually did.
"""
import json
import re
from collections import defaultdict
from datetime import datetime

from shadow import db, guardrails, llm, matcher, rules

APPROVE_MIN_SUPPORT = 3


def pb_dir(client: str, track: str):
    return db.DATA / client / "playbook" / track


def versions(client: str, track: str = "main") -> list[int]:
    return sorted(int(p.stem[1:]) for p in pb_dir(client, track).glob("v*.json"))


def load(client: str, track: str = "main", version: int | None = None) -> dict | None:
    vs = versions(client, track)
    if not vs:
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
    core = lambda r: {k: r.get(k) for k in ("text", "when", "then", "executable", "status")}
    return {"from": a["version"], "to": b["version"], "cause": b.get("cause"),
            "added": [rb[i] for i in rb if i not in ra], "removed": [ra[i] for i in ra if i not in rb],
            "changed": [{"before": ra[i], "after": rb[i]} for i in rb if i in ra and core(ra[i]) != core(rb[i])]}


def render(pb: dict, chart: dict | None = None) -> str:
    lines = []
    for r in pb["rules"]:
        bt = r.get("backtest") or {}
        tag = "auto" if r.get("executable") else "judgement"
        lines.append(f"{r['id']} [{r['status']}, {tag}, {bt.get('support', 0)} precedents, {bt.get('conflicts', 0)} conflicts] {r['text']}")
    return "\n".join(lines)


# --- history -> cases -> clusters ----------------------------------------------------------
def cases(con, through: str) -> list[dict]:
    """One case per exception the humans resolved, in periods up to and including `through`."""
    ledger = {e["id"]: e for e in db.q(con, "SELECT * FROM ledger_entry")}
    bank = {b["id"]: b for b in db.q(con, "SELECT * FROM bank_line")}
    out = []
    for r in db.q(con, "SELECT * FROM resolution WHERE period <= ? AND (by='human' OR action='carry_forward') ORDER BY id", through):
        item = bank.get(r["item_id"]) or ledger.get(r["item_id"])
        les = [ledger[i] for i in r["ledger_ids"]]
        total = round(sum(e["amount"] for e in les), 2)
        c = {"precedent_id": r["id"], "period": r["period"], "item_kind": r["item_kind"], "date": item["date"],
             "text": item.get("description") or item.get("memo"), "counterparty": item["counterparty"],
             "ref": item["ref"], "amount": item["amount"], "action": r["action"], "adjustments": r["adjustments"],
             "escalate_to": r["escalate_to"], "note": r["note"]}
        if les:
            c |= {"ledger": [{"amount": e["amount"], "account": e["account"], "memo": e["memo"], "ref": e["ref"],
                              "days_before_bank": matcher.days_between(item["date"], e["date"])} for e in les],
                  "diff": round(total - item["amount"], 2),
                  "diff_pct": round(abs(total - item["amount"]) / abs(total) * 100, 3) if total else None}
        if r["item_kind"] == "ledger":
            c |= {"account": item["account"], "posted_at": item["posted_at"],
                  "age_days_at_period_end": matcher.days_between(matcher.period_end(r["period"]), item["date"])}
        out.append(c)
    return out


def clusters(cs: list[dict], examples: int = 5) -> list[dict]:
    groups = defaultdict(list)
    for c in cs:
        shape = re.sub(r"[A-Z]*\d[\w-]*", "#", c["text"] or "")
        sig = (c["item_kind"], shape, c["action"], tuple(sorted(a["account"] for a in c["adjustments"])),
               c["escalate_to"], len(c.get("ledger", [])) > 1)
        groups[sig].append(c)
    out = []
    for sig, g in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        amounts = [abs(c["amount"]) for c in g]
        diffs = [c["diff"] for c in g if c.get("diff") is not None]
        pcts = [c["diff_pct"] for c in g if c.get("diff_pct") is not None]
        g_sorted = sorted(g, key=lambda c: abs(c.get("diff") or c["amount"]))
        picks = [g_sorted[0], g_sorted[-1]] + g_sorted[1:-1][:: max(1, len(g) // examples)][: examples - 2] if len(g) > 2 else g
        out.append({"count": len(g), "item_kind": sig[0], "text_shape": sig[1], "action": sig[2],
                    "accounts": list(sig[3]), "escalate_to": sig[4],
                    "abs_amount_range": [min(amounts), max(amounts)],
                    "diff_range": [min(diffs), max(diffs)] if diffs else None,
                    "diff_pct_range": [min(pcts), max(pcts)] if pcts else None,
                    "examples": picks})
    return out


# --- induction -----------------------------------------------------------------------------
RULE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["rules"],
    "properties": {"rules": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["text", "executable", "when", "then", "precedent_ids"],
        "properties": {
            "text": {"type": "string"}, "executable": {"type": "boolean"},
            "when": {"type": "object"}, "then": {"type": "object"},
            "precedent_ids": {"type": "array", "items": {"type": "string"}}}}}},
}

INDUCE_SYSTEM = """You are onboarding a new client onto a bank reconciliation service. Nobody configured anything for this \
client. Your only source is how the client's own finance staff resolved past exceptions. From that history, write the \
client's playbook: the conventions this team actually follows, specific enough that a controller could read each rule, \
recognise it as theirs, and sign it off.

A deterministic matcher already clears exact one-to-one matches before the playbook is consulted, so write rules for \
what is left: differences, items with no ledger counterpart, items the humans sent to someone, entries left open at \
month end.

Each rule has:
- text: one plain sentence in the client's terms, with the threshold, the account number and who it goes to. \
No hedging, no em dashes.
- when / then: machine conditions in the rule language documented below. Thresholds must be consistent with every \
example you were shown: never set a no-review limit above an amount the humans escalated, and do not extrapolate a \
limit far beyond the largest amount they handled without review.
- executable: true when `when` fully captures the convention, so code can apply it with no judgement. Use false when \
the convention depends on something the rule language cannot check (a pattern across several items, reading an email, \
a contract term, whether evidence exists). A non-executable rule still needs a `when` that scopes which items it \
covers, because a matching non-executable rule sends the item to a human-like investigator with your text attached.
- precedent_ids: the precedent_id values from the examples that show this convention.

Rules are tried in order and the first match wins. Put narrow rules before broad ones, and put a judgement rule before \
any mechanical rule that would otherwise swallow its cases. Where the humans drew a line (small ones written off, \
larger ones sent to someone), write both sides of the line as separate rules so nothing falls through silently. Do not \
invent conventions the history does not show. If a kind of item appears once or twice only, still write the rule; the \
back-test will decide whether it is trusted.

Rule language:
""" + rules.__doc__.split("when (bank items", 1)[1].join(["when (bank items", ""])


def induce(client: str, through: str, track: str = "main", usage: llm.Usage | None = None) -> dict:
    con = db.connect(client, readonly=True)
    info = db.q(con, "SELECT * FROM client")[0]
    cl = clusters(cases(con, through))
    prompt = (f"Client: {info['name']}. {info['blurb']}\nChart of accounts: {json.dumps(info['chart'])}\n"
              f"History covers periods through {through}. Below are the {len(cl)} kinds of exception the humans "
              f"resolved, each with its count, amount and difference ranges, and sample cases with the human's note.\n\n"
              + json.dumps(cl, indent=1))
    reply = llm.call(INDUCE_SYSTEM, [{"role": "user", "content": prompt}], schema=RULE_SCHEMA, max_tokens=32000, usage=usage)
    raw = json.loads(reply.text)["rules"]
    known = {r["id"] for r in db.q(con, "SELECT id FROM resolution WHERE period <= ?", through)}
    pb_rules = []
    for n, r in enumerate(raw, 1):
        pb_rules.append({"id": f"{client}-R-{n:03d}", "text": r["text"], "executable": bool(r["executable"]),
                         "when": r["when"], "then": r["then"], "origin": "induced", "version_added": 1,
                         "precedent_ids": [p for p in r["precedent_ids"] if p in known], "status": "approved"})
    pb = {"trained_through": through, "rules": pb_rules}
    backtest(con, pb)
    return save(client, track, pb, {"type": "induction", "through": through, "clusters": len(cl)})


# --- back-test -----------------------------------------------------------------------------
def backtest(con, pb: dict) -> dict:
    """Replay every history period: guardrails, matcher, then the rules. Score each rule against what the humans did.

    Rules that contradict history are demoted to judgement rules; rules without enough support stay proposed.
    """
    from grade import same  # comparison logic only; no key is read here
    through = pb["trained_through"]
    periods = [r["period"] for r in db.q(con, "SELECT DISTINCT period FROM resolution WHERE period <= ? ORDER BY 1", through)]
    for r in pb["rules"]:
        r["status"] = "approved"
        r["backtest"] = {"support": 0, "conflicts": 0, "conflict_examples": [], "supported_by": []}
    uncovered = []
    for period in periods:
        human = {r["item_id"]: r for r in db.q(con, "SELECT * FROM resolution WHERE period=?", period)}
        flagged = guardrails.scan(con, period)
        matches, used = matcher.run(con, period, skip=set(flagged))
        ctx = rules.Ctx(con, period, matcher.open_ledger(con, period), set(used))
        done = {m["bank_id"] for m in matches}
        bank = [b for b in db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date, id", period) if b["id"] not in done]
        truth_used = {i for h in human.values() if h["item_kind"] == "bank" for i in h["ledger_ids"]}
        ledger_items = [e for e in db.q(con, "SELECT * FROM ledger_entry WHERE period=? ORDER BY date, id", period)
                        if e["id"] not in truth_used and e["id"] in human]
        for kind, items in (("bank", bank), ("ledger", ledger_items)):
            for item in items:
                h = human.get(item["id"])
                if not h:
                    continue
                rule, out = rules.apply(pb, item, kind, ctx)
                if not rule:
                    if h["by"] == "human":
                        uncovered.append(h["id"])
                    continue
                bt = rule["backtest"]
                if out.get("defer"):
                    bt["support"] += 1
                    bt["supported_by"].append(h["id"])
                    continue
                if same(out["resolution"], h):
                    bt["support"] += 1
                    bt["supported_by"].append(h["id"])
                    ctx.used.update(out["resolution"]["ledger_ids"])
                else:
                    bt["conflicts"] += 1
                    if len(bt["conflict_examples"]) < 3:
                        bt["conflict_examples"].append({"precedent_id": h["id"], "human": h["action"], "rule": out["resolution"]["action"]})
                    ctx.used.update(h["ledger_ids"])
    for r in pb["rules"]:
        bt = r["backtest"]
        r["precedent_ids"] = (bt["supported_by"][:12] or r["precedent_ids"][:12])
        bt["supported_by"] = len(bt["supported_by"])
        if r["executable"] and bt["conflicts"]:
            r["executable"] = False
            r["demoted"] = "contradicted by history in back-test; kept as guidance for the investigator"
        if bt["support"] < APPROVE_MIN_SUPPORT:
            r["status"] = "proposed"
    pb["backtest"] = {"periods": periods, "uncovered_human_cases": len(uncovered), "uncovered_examples": uncovered[:10]}
    return pb
