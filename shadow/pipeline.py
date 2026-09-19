"""One reconciliation run: guardrails, matcher, playbook rules, investigator. Writes runs/<run_id>/.

    uv run python -m shadow.pipeline A 2026-04 --condition playbook
    uv run python -m shadow.pipeline A 2026-03 --condition playbook --track dev --no-llm   # tiers 0-1 only, free

Conditions: zero_shot (no playbook, no history), playbook (latest induced playbook on the track), corrected
(same code path; the track's latest version includes what human corrections added), cold_start (empty playbook).
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from shadow import db, guardrails, investigator, matcher, playbook as pbmod, rules, stale

ZERO = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.0, "llm_calls": 0}


def blank(action="escalate", **kw) -> dict:
    return {"action": action, "ledger_ids": [], "adjustments": [], "escalate_to": None, "rationale": "", "rule_id": None,
            "precedent_ids": [], "evidence_ids": [], "confidence": 1.0, "reason": None, "questions": [], "proposed": None} | kw


def fmt(cond: str, x) -> str:
    return "anything" if x is None else f"{x:,.2f}%" if "pct" in cond else f"${x:,.2f}"


def run(client: str, period: str, condition: str, track: str = "main", version: int | None = None, use_llm: bool = True,
        workers: int = 8, only: set[str] | None = None, run_id: str | None = None, date_from: str | None = None,
        date_to: str | None = None, label: str = "", db_file=None) -> dict:
    con = db.connect(client, readonly=True, path=db_file)
    info = db.q(con, "SELECT * FROM client")[0]
    pb = None
    if condition == "cold_start":
        pb = pbmod.load(client, track, version) or {"version": 0, "rules": []}
    elif condition != "zero_shot":
        pb = pbmod.load(client, track, version)
        assert pb, f"no playbook on track {track}; run shadow.induce first"
    roles = sorted({u["role"].lower().replace(" ", "_") for u in db.q(con, "SELECT * FROM user WHERE senior=1")})

    flags = guardrails.scan(con, period)
    matches, used = matcher.run(con, period, skip=set(flags))
    ledger_open = matcher.open_ledger(con, period)
    by_id = {e["id"]: e for e in ledger_open}
    items: dict[str, dict] = {}
    in_scope = lambda r: (not only or r["id"] in only) and not (date_from and r["date"] < date_from) and not (date_to and r["date"] > date_to)

    bank = db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date, id", period)
    matched = {m["bank_id"]: m for m in matches}
    for b in bank:
        if b["id"] in matched and in_scope(b):
            m = matched[b["id"]]
            items[b["id"]] = {"item_id": b["id"], "item_kind": "bank", "record": b, "tier": "matcher", "usage": dict(ZERO),
                              "resolution": blank("match", ledger_ids=m["ledger_ids"], evidence_ids=m["ledger_ids"], rationale=m["how"]),
                              "trace": [{"step": 1, "kind": "matcher", "label": "deterministic match", "input": {}, "output": m["how"], "ids": m["ledger_ids"]}]}

    ctx = rules.Ctx(con, period, ledger_open, used)
    queue = []   # (item, kind, flags, deferring rule)

    def rule_tier(batch):
        planned = rules.plan(pb, batch, ctx) if pb else {}
        for kind, item in batch:
            fl = flags.get(item["id"], [])
            rule, out = (None, None) if fl else planned.get(item["id"], (None, None))
            if not rule or out.get("defer"):
                queue.append((item, kind, fl, rule))
                continue
            if out.get("in_band"):
                b = out["in_band"]
                known = f"up to {fmt(b['condition'], b['lo'])}" if b["lo"] else "never"
                far = f"from {fmt(b['condition'], b['hi'])} it was handled differently" if b["hi"] is not None else "nothing larger has ever come up"
                why = (f"{rule['text']} This one is at {fmt(b['condition'], b['value'])}. The client's history shows that handling {known}; {far}. "
                       "Between the two nobody knows, so it is not guessed.")
                items[item["id"]] = {
                    "item_id": item["id"], "item_kind": kind, "record": item, "tier": "rule", "usage": dict(ZERO), "band": b,
                    "resolution": blank(escalate_to=out.get("escalate_to") or (roles[0] if roles else None), reason="in_band", rule_id=rule["id"],
                                        precedent_ids=[x for x in (b["lo_precedent"], b["hi_precedent"]) if x], evidence_ids=out["evidence_ids"],
                                        rationale=why, proposed=out["would"],
                                        questions=[f"Should one at {fmt(b['condition'], b['value'])} be handled the usual way, with no review?"]),
                    "trace": [{"step": 1, "kind": "matcher", "label": "no unique exact match", "input": {}, "output": "left for the playbook", "ids": []},
                              {"step": 2, "kind": "rule", "label": f"{rule['id']} in band", "input": b, "output": why,
                               "ids": [x for x in (b["lo_precedent"], b["hi_precedent"]) if x]}]}
                continue
            res = out["resolution"]
            ctx.used.update(res["ledger_ids"])
            items[item["id"]] = {
                "item_id": item["id"], "item_kind": kind, "record": item, "tier": "rule", "usage": dict(ZERO),
                "resolution": blank(**res, rule_id=rule["id"], precedent_ids=rule.get("precedent_ids", [])[:6],
                                    evidence_ids=out["evidence_ids"], confidence=rule.get("confidence", 1.0), rationale=rule["text"]),
                "trace": [{"step": 1, "kind": "matcher", "label": "no unique exact match", "input": {}, "output": "left for the playbook", "ids": []},
                          {"step": 2, "kind": "rule", "label": rule["id"], "input": rule["when"], "output": rule["text"], "ids": out["evidence_ids"]}]}

    rule_tier([("bank", b) for b in bank if b["id"] not in matched and in_scope(b)])

    def work(job):
        item, kind, fl, rule = job
        if not use_llm:
            return item, kind, fl, {"resolution": blank(escalate_to=roles[0] if roles else None, confidence=0.0, reason="no_rule",
                                                        rationale="Not cleared by the matcher or a playbook rule. Left for review (model tier disabled)."),
                                    "trace": [], "usage": dict(ZERO)}
        try:
            return item, kind, fl, investigator.investigate(db.connect(client, readonly=True, path=db_file), info, period, item, kind, set(ctx.used), pb, fl, rule)
        except Exception as e:  # a failed investigation must never drop an item
            return item, kind, fl, {"resolution": blank(escalate_to=roles[0] if roles else None, confidence=0.0, reason="no_rule",
                                                        rationale=f"Investigation failed ({type(e).__name__}: {e}). Left for review."),
                                    "trace": [], "usage": dict(ZERO)}

    def drain():
        jobs, queue[:] = list(queue), []
        with ThreadPoolExecutor(workers) as ex:
            for item, kind, fl, out in ex.map(work, jobs):
                res = out["resolution"]
                clash = [i for i in res["ledger_ids"] if i in ctx.used]
                if clash:   # two investigations claimed the same entry; the later one goes to a human
                    res = res | {"action": "escalate", "ledger_ids": [], "adjustments": [], "escalate_to": roles[0] if roles else None,
                                 "rationale": f"Ledger entry {clash[0]} was claimed by another item in this run. " + res["rationale"]}
                ctx.used.update(res["ledger_ids"])
                tier = "guardrail" if fl and res["action"] == "escalate" else "investigator"
                items[item["id"]] = {"item_id": item["id"], "item_kind": kind, "record": item, "tier": tier,
                                     "control_flags": fl, "resolution": res, "trace": out["trace"], "usage": out["usage"]}

    drain()
    # ledger entries of this period that nothing cleared
    rule_tier([("ledger", e) for e in db.q(con, "SELECT * FROM ledger_entry WHERE period=? ORDER BY date, id", period)
               if e["id"] in by_id and e["id"] not in ctx.used and in_scope(e)])
    drain()

    run_id = run_id or f"{client}_{period}_{condition}_{datetime.now():%m%d-%H%M%S}"
    out_dir = db.RUNS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(items.values(), key=lambda it: (it["record"]["date"], it["item_id"]))
    for it in ordered:
        it["evidence_fingerprint"] = stale.fingerprint(con, it["resolution"])
    (out_dir / "resolutions.jsonl").write_text("\n".join(json.dumps(it, default=str) for it in ordered))
    tiers = {t: sum(it["tier"] == t for it in ordered) for t in ("matcher", "guardrail", "rule", "investigator")}
    summary = {"run_id": run_id, "client": client, "condition": condition, "period": period, "track": track, "label": label,
               "playbook_version": pb["version"] if pb else None, "created_at": datetime.now().isoformat(timespec="seconds"),
               "n_items": len(ordered), "tiers": tiers, "llm": use_llm,
               "cost_usd": round(sum(it["usage"]["cost_usd"] for it in ordered), 4),
               "llm_calls": sum(it["usage"]["llm_calls"] for it in ordered),
               "escalated": sum(it["resolution"]["action"] == "escalate" for it in ordered),
               "escalation_reasons": {r: sum(it["resolution"]["action"] == "escalate" and it["resolution"].get("reason") == r for it in ordered)
                                      for r in ("in_band", "no_rule", "conflicting_precedents", "fraud_shaped", "thin_precedent")}}
    (out_dir / "run.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("client")
    ap.add_argument("period")
    ap.add_argument("--condition", default="playbook", choices=["zero_shot", "playbook", "corrected", "cold_start"])
    ap.add_argument("--track", default="main")
    ap.add_argument("--version", type=int)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--run-id")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    print(json.dumps(run(a.client, a.period, a.condition, a.track, a.version, not a.no_llm, a.workers, run_id=a.run_id,
                         date_from=a.date_from, date_to=a.date_to, label=a.label), indent=1))
