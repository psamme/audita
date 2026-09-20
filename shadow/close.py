"""Month-end close status for one client and period. No model call, nothing written, $0.

Everything here is read off what already exists: the client's books (opened read-only), the standing resolution of
every item (the newest one across the track's runs, the same rule the undo's blast radius uses), the playbook in
force, the re-open log and, when the auditor has run, its file. It never opens the simulator's truth or the keys.

    uv run python -m shadow.close A 2026-04
"""
import json
from datetime import date

from shadow import correct, db, playbook as pbmod

CLEARED = {"match", "match_adjust", "book"}
REASONS = {"in_band": "inside a band nobody has settled", "no_rule": "no rule covers it", "conflicting_precedents": "precedents conflict",
           "fraud_shaped": "fraud shaped", "thin_precedent": "too few precedents", None: "investigator was not sure"}


def standing(client: str, period: str, track: str = "main") -> dict[str, dict]:
    """The resolution currently standing for each item: newest run wins, re-runs after an answer included.
    Zero-shot runs are left out (they are a comparison, not the books) and so are an undo's re-checks."""
    metas = sorted((json.loads(p.read_text()) for p in db.RUNS.glob("*/run.json")), key=lambda m: (m["created_at"], m["run_id"]))
    out: dict[str, dict] = {}
    for m in metas:
        if (m["client"] != client or m["period"] != period or (m.get("track") or "main") != track
                or m["condition"] == "zero_shot" or m["run_id"].startswith("blast_")):
            continue
        for line in (db.RUNS / m["run_id"] / "resolutions.jsonl").read_text().splitlines():
            it = json.loads(line)
            out[it["item_id"]] = it | {"run_id": m["run_id"], "playbook_version": m.get("playbook_version"), "resolved_at": m["created_at"]}
    return out


def _reopened(client: str, track: str, items: dict[str, dict]) -> set[str]:
    """Items an undo re-opened and that nothing has resolved again since (the standing resolution is still the one it re-opened)."""
    path = db.DATA / client / ("reopened.jsonl" if track == "main" else f"reopened_{track}.jsonl")
    if not path.exists():
        return set()
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return {r["item_id"] for r in rows if r["item_id"] in items and items[r["item_id"]]["run_id"] == r["run_id"]}


def _money(x: float) -> float:
    return round(x + 0.0, 2)


def _row(key, label, status, count=None, amount=None, detail="", href=None):
    return {"key": key, "label": label, "status": status, "count": count, "amount": None if amount is None else _money(amount),
            "detail": detail, "href": href}


def _audit(client: str) -> dict | None:
    path = db.RUNS / f"audit_{client}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def status(client: str, period: str = "2026-04", track: str = "main") -> dict:
    con = db.connect(client, readonly=True)
    info = db.q(con, "SELECT id, name, chart FROM client")[0]
    bank = db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date, id", period)
    if not bank:
        raise ValueError(f"no bank lines for {period}")
    ledger = {e["id"]: e for e in db.q(con, "SELECT * FROM ledger_entry")}
    items = standing(client, period, track)
    log = _log(client, track)
    undone = {e.get("retracted") for e in log if e.get("type") == "retraction"}
    for e in log:     # a queue correction is a person resolving that item; it stands unless it was retracted
        it = items.get(e.get("item_id"))
        if e.get("type") == "correction" and it and e["correction_id"] not in undone and e["human"]["action"] != "escalate" and e.get("run_id") == it["run_id"]:
            items[e["item_id"]] = it | {"tier": "person", "resolution": it["resolution"] | e["human"] | {"reason": None, "rule_id": None}}
    reopened = _reopened(client, track, items)
    as_of = max(b["date"] for b in bank)
    abs_sum = lambda its: sum(abs(it["record"]["amount"]) for it in its)

    bank_items = [items[b["id"]] for b in bank if b["id"] in items]
    missing = [b for b in bank if b["id"] not in items]
    is_open = lambda it: it["resolution"]["action"] == "escalate" or it["item_id"] in reopened
    cleared = [it for it in bank_items if it["resolution"]["action"] in CLEARED and not is_open(it)]
    by_tier = lambda t: [it for it in cleared if it["tier"] == t]
    held = [it for it in items.values() if it["tier"] == "guardrail" and is_open(it)]
    asked = [it for it in items.values() if is_open(it) and it["tier"] != "guardrail"]
    carried = [it for it in items.values() if it["resolution"]["action"] == "carry_forward" and not is_open(it)]

    rows = []
    rows.append(_row("imported", "Every bank line in the period has a standing resolution", "PASS" if not missing else "FAIL",
                     len(bank) - len(missing), sum(b["amount"] for b in bank),
                     f"{len(bank)} lines, net movement shown" + (f"; {len(missing)} have never been run" if missing else ""), None))
    m, r, inv = by_tier("matcher"), by_tier("rule"), by_tier("investigator")
    rows.append(_row("matcher", "Cleared by the matcher", "PASS", len(m), abs_sum(m), "unique exact matches, no model call", None))
    rows.append(_row("rules", "Cleared by playbook rules", "PASS", len(r), abs_sum(r),
                     f"{len({it['resolution']['rule_id'] for it in r})} signed rules fired, no model call", "playbook.html?client=" + client))
    cost = sum(it["usage"]["cost_usd"] for it in items.values())
    rows.append(_row("investigator", "Resolved by the investigator", "PASS", len(inv), abs_sum(inv),
                     f"model cost for every standing item ${cost:,.2f}", None))
    person = by_tier("person")
    rows.append(_row("person", "Resolved by a person in the review queue", "PASS", len(person), abs_sum(person),
                     "each one was also offered to the playbook as a correction", "playbook.html?client=" + client))

    # tie-out of what is cleared: bank amount, less the ledger entries it was matched to, plus the adjustments booked, must be zero
    off = []
    for it in cleared:
        res = it["resolution"]
        if any(i not in ledger for i in res["ledger_ids"]):
            off.append((it["item_id"], None))
            continue
        d = db.cents(it["record"]["amount"]) - sum(db.cents(ledger[i]["amount"]) for i in res["ledger_ids"]) + sum(db.cents(a["amount"]) for a in res["adjustments"])
        if d:
            off.append((it["item_id"], d / 100))
    used = [i for it in items.values() if not is_open(it) for i in it["resolution"]["ledger_ids"]]
    twice = sorted({i for i in used if used.count(i) > 1})
    rows.append(_row("tieout", "Cleared items tie to the ledger, to the cent", "PASS" if not off and not twice else "FAIL", len(cleared),
                     sum(d or 0 for _, d in off),
                     "bank less ledger plus adjustments is zero on every one" if not off and not twice else
                     f"{len(off)} do not tie ({', '.join(i for i, _ in off[:3])}); {len(twice)} ledger entries claimed twice", None))

    # each rule that books a small difference without review carries its own signed limit; check every item it cleared against it
    pb = pbmod.load(client, track)
    caps = {}
    for rule in (pb or {}).get("rules", []):
        w, then = rule.get("when", {}), rule.get("then", {})
        acct = then.get("account") or then.get("remainder_account")
        cap = next((v for v in (w.get("diff_abs_max"), w.get("diff_max"), (w.get("doc") or {}).get("net_diff_abs_max")) if v is not None), None)
        if then.get("action") == "match_adjust" and acct and cap is not None:
            caps[rule["id"]] = (acct, cap)
    booked, over, n_lim = {}, [], 0
    for it in cleared:
        acct, cap = caps.get(it["resolution"].get("rule_id"), (None, None))
        if it["tier"] != "rule" or acct is None:
            continue
        amt = sum(abs(a["amount"]) for a in it["resolution"]["adjustments"] if a["account"] == acct)
        if amt:
            n_lim += 1
            booked[acct] = booked.get(acct, 0) + amt
            if amt > cap + 0.005:
                over.append(it["item_id"])
    if caps:
        chart = info["chart"]
        lim = {}
        for acct, cap in caps.values():
            lim[acct] = max(lim.get(acct, 0), cap)
        parts = [f"{acct} {chart.get(acct, '')}: ${booked.get(acct, 0):,.2f} booked, largest signed limit ${cap:,.2f} an item" for acct, cap in sorted(lim.items())]
        rows.append(_row("writeoffs", "Differences booked without review are inside the signed limit", "PASS" if not over else "FAIL",
                         n_lim, sum(booked.values()), "; ".join(parts) + (f"; over the limit: {', '.join(over[:3])}" if over else ""),
                         "playbook.html?client=" + client))

    rows.append(_row("carried", "Timing items carried forward to next period", "PASS", len(carried), abs_sum(carried),
                     "ledger entries the bank has not shown yet; they open next month's reconciliation", None))

    age = lambda it: (date.fromisoformat(as_of) - date.fromisoformat(it["record"]["date"])).days
    queue_href = lambda its: "queue.html?run=" + max(its, key=lambda it: it["resolved_at"])["run_id"] if its else "queue.html"
    if asked:
        why: dict = {}
        for it in asked:
            k = "re-opened by an undo" if it["item_id"] in reopened else REASONS.get(it["resolution"].get("reason"), it["resolution"].get("reason"))
            why[k] = why.get(k, 0) + 1
        detail = ", ".join(f"{n} {k}" for k, n in sorted(why.items(), key=lambda kv: -kv[1])) + f"; oldest is {max(age(it) for it in asked)} days old at {as_of}"
    rows.append(_row("open", "Open with a person", "OPEN" if asked else "PASS", len(asked), abs_sum(asked),
                     detail if asked else "nothing is waiting on a person", queue_href(asked)))
    flags = sorted({f["flag"].replace("_", " ") for it in held for f in it.get("control_flags") or []})
    rows.append(_row("held", "Held by guardrails", "HELD" if held else "PASS", len(held), abs_sum(held),
                     ("not matched however exactly it ties: " + ", ".join(flags)) if held else "no control flag raised", queue_href(held)))

    blocked = [it for it in bank_items if is_open(it)]
    rows.append(_row("unreconciled", "Bank movement not yet explained", "PASS" if not blocked and not missing else "OPEN", len(blocked) + len(missing),
                     sum(it["record"]["amount"] for it in blocked) + sum(b["amount"] for b in missing),
                     "net of the open and held bank lines; one operating account", queue_href(blocked)))

    if pb:
        proposed = [x for x in pb["rules"] if x["status"] == "proposed"]
        floor = [x for x in proposed if x.get("below_floor")]
        old = [it for it in items.values() if it.get("playbook_version") not in (None, pb["version"])]
        n_in = sum(1 for x in log if x.get("type") != "retraction")
        rows.append(_row("playbook", f"Playbook version {pb['version']} in force", "INFO", len(pb["rules"]), None,
                         f"{sum(x['status'] == 'approved' for x in pb['rules'])} rules signed and running, {len(proposed)} unsigned and not running"
                         + (f" ({len(floor)} held back by their own history)" if floor else "") + f"; {n_in} human inputs on file"
                         + (f"; {len(old)} standing items were resolved under an earlier version" if old else ""), "playbook.html?client=" + client))

    aud = _audit(client)
    if aud is None:
        rows.append(_row("audit", "Independent auditor", "INFO", None, None, "has not run for this client yet", "audit.html"))
    else:
        finds = aud.get("findings") or []
        sev = lambda f: str(f.get("severity", "")).lower()
        answer = [f for f in finds if sev(f) in ("medium", "high", "critical")]
        rp = aud.get("reperformance") or {}
        redo = (f"re-performed {rp['sampled']} sampled items without the preparer's reasoning, agreed on {rp['agree']}; "
                if isinstance(rp.get("sampled"), int) and isinstance(rp.get("agree"), int) else "")
        rows.append(_row("audit", "Independent auditor", "OPEN" if answer else "PASS", len(finds), None,
                         redo + (f"{len(answer)} finding{'s' if len(answer) != 1 else ''} to answer ({', '.join(sorted({sev(f) for f in answer}))})"
                                 if answer else "no finding needs an answer")
                         + (f"; covers run {aud['run_id']}" if aud.get("run_id") else ""), "audit.html?client=" + client))

    checks = [x for x in rows if x["status"] != "INFO"]
    passing = sum(x["status"] == "PASS" for x in checks)
    blockers = {it["item_id"]: it for it in asked + held}
    worth = abs_sum(blockers.values())
    failed = [x["label"] for x in checks if x["status"] == "FAIL"]
    if not items:
        headline = f"No reconciliation run is on file for {period} on the {track} track, so nothing can be said about this close yet."
    elif not blockers and not failed and passing == len(checks):
        headline = f"{passing} of {len(checks)} checks pass. The period is ready to close."
    else:
        headline = (f"{passing} of {len(checks)} checks pass. Close is blocked by {len(blockers)} item{'s' if len(blockers) != 1 else ''} worth ${worth:,.2f}."
                    + (f" {len(failed)} check{'s' if len(failed) != 1 else ''} failed." if failed else ""))
    dates = sorted(it["record"]["date"] for it in items.values())
    return {"client": client, "name": info["name"], "period": period, "track": track, "as_of": as_of, "headline": headline,
            "checks_pass": passing, "checks_total": len(checks), "blocked_items": len(blockers), "blocked_usd": _money(worth),
            "covered_from": dates[0] if dates else None, "covered_to": dates[-1] if dates else None,
            "runs": sorted({it["run_id"] for it in items.values()}), "rows": rows,
            "guarantees": ["The agent opens the client's books read-only. Nothing on this page was posted by it.",
                           "A rule change never restates history silently. An answer re-runs only the open queue. An undo re-checks every "
                           "resolution that leaned on it and re-opens exactly the ones that no longer hold, and they show up here as open."]}


def _log(client: str, track: str) -> list[dict]:
    path = correct.log_path(client, track)
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


if __name__ == "__main__":
    import sys
    s = status(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "2026-04")
    print(s["headline"])
    for x in s["rows"]:
        print(f"  {x['status']:5} {x['label']:70} {x['count'] if x['count'] is not None else '':>5} {x['amount'] if x['amount'] is not None else '':>14}  {x['detail']}")
