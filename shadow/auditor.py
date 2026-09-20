"""The auditor: a second agent that checks the preparer's work without seeing how the preparer reasoned.

    uv run python -m shadow.auditor --client A --run runs/A_2026-04_corrected

It reads the client's books (the same read-only database the preparer sees) and a run's resolutions, and nothing
else: no grades, no metrics, no answer material of any kind. From each resolution it takes only the disposition
(action, ledger ids, adjustments, who it was routed to, the rule cited); the rationale and the trace are dropped
before anything is tested, so agreement is never the auditor repeating the preparer.

Three parts, in the order an audit file is read:
  1. control tests over the ERP trail and the run, deterministic, no model calls
  2. re-performance of a seeded, risk-weighted sample: arithmetic and uniqueness in code for every item,
     and a capped number of items re-performed from scratch by a model that is never told the preparer's answer
  3. consistency: the same transaction means the same thing everywhere

Writes runs/audit_<client>.json.
"""
import argparse
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from shadow import db, llm, playbook as pbmod

AUTO = ("match", "match_adjust", "book")
ACCT = re.compile(r"ACCT\s*\*?(\d{3,})", re.I)
STOP = {"ach", "inc", "llc", "llp", "ltd", "corp", "wire", "debit", "credit", "out", "the", "pmt", "payment", "and", "group"}
SEVERITY = {"high": 0, "medium": 1, "low": 2, "note": 3}
MODEL_CAP = 12


# --- helpers -------------------------------------------------------------------------------
def toks(*parts) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", " ".join(p or "" for p in parts).lower()) if t not in STOP}


def days(a: str, b: str) -> int:
    return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days


def close_date(period: str, close_days: int) -> str:
    y, m = map(int, period.split("-"))
    return (date(y + (m == 12), m % 12 + 1, 1) + timedelta(days=close_days + 2)).isoformat()


def disposition(item: dict) -> dict:
    """All the auditor keeps of the preparer's work. No rationale, no trace, no confidence."""
    r = item["resolution"]
    return {"item_id": item["item_id"], "item_kind": item["item_kind"], "record": item["record"], "tier": item["tier"],
            "action": r["action"], "ledger_ids": list(r.get("ledger_ids") or []),
            "adjustments": [{"account": str(a["account"]), "amount": round(float(a["amount"]), 2)} for a in r.get("adjustments") or []],
            "escalate_to": r.get("escalate_to"), "rule_id": r.get("rule_id"), "precedent_ids": list(r.get("precedent_ids") or []),
            "evidence_ids": list(r.get("evidence_ids") or []), "control_flags": item.get("control_flags") or []}


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    meta = json.loads((run_dir / "run.json").read_text())
    items = [disposition(json.loads(l)) for l in (run_dir / "resolutions.jsonl").read_text().splitlines() if l.strip()]
    return meta, items


def finding(code, severity, title, detail, evidence, where="trail", route_to=None) -> dict:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "evidence_ids": [e for e in evidence if e][:12],
            "where": where, "route_to": route_to}


def control(code, name, what, status, tested, findings, note="") -> dict:
    return {"code": code, "name": name, "what": what, "status": status, "tested": tested, "exceptions": len(findings),
            "findings": findings, "note": note}


# --- 1. control tests ----------------------------------------------------------------------
def writeoff_limits(pb: dict | None) -> dict[str, dict]:
    """Per signed rule, the largest difference it may book to its write-off account without review, where a person stated it.
    A limit belongs to its rule: the same account also takes bank fees, which no limit governs."""
    out: dict[str, dict] = {}
    for r in (pb or {}).get("rules", []):
        if r.get("status") != "approved" or r["then"].get("action") != "match_adjust":
            continue
        acct = r["then"].get("remainder_account") or r["then"].get("account")
        for cond, b in (r.get("bands") or {}).items():
            if acct and b and b.get("source") == "stated" and b.get("side") == "upper" and "diff" in cond and b.get("lo") is not None:
                out[r["id"]] = {"limit": float(b["lo"]), "account": str(acct)}
    return out


def control_tests(con, period: str, pb: dict | None, items: list[dict]) -> list[dict]:
    info = db.q(con, "SELECT * FROM client")[0]
    users = {u["id"]: u for u in db.q(con, "SELECT * FROM user")}
    senior = [u for u in users.values() if u["senior"]]
    head = (senior[0]["role"].lower().replace(" ", "_").replace("-", "_")) if senior else None
    bank = db.q(con, "SELECT * FROM bank_line WHERE period <= ? ORDER BY date, id", period)
    ledger = db.q(con, "SELECT * FROM ledger_entry WHERE period <= ? ORDER BY date, id", period)
    jes = db.q(con, "SELECT * FROM journal_entry WHERE period <= ? ORDER BY id", period)
    approvals = db.q(con, "SELECT * FROM approval WHERE period <= ? ORDER BY id", period)
    links = db.q(con, "SELECT * FROM reconcile_link WHERE period <= ? AND undone_at IS NULL", period)
    appr_by_subject: dict[str, list[dict]] = {}
    for a in approvals:
        appr_by_subject.setdefault(a["subject_id"], []).append(a)
    ok_appr = lambda sid: any(a["status"] == "approved" for a in appr_by_subject.get(sid, []))
    run_by_id = {i["item_id"]: i for i in items}
    held = lambda bid: bid in run_by_id and run_by_id[bid]["action"] == "escalate"
    out = []

    # C1 the same payment leaving the bank twice
    fs, outgoing = [], [b for b in bank if b["amount"] < 0 and b["counterparty"]]
    seen = set()
    for i, a in enumerate(outgoing):
        twins = [b for b in outgoing[i + 1:] if b["counterparty"] == a["counterparty"] and b["amount"] == a["amount"] and days(b["date"], a["date"]) <= 10]
        group = [a] + twins
        if not twins or any(g["id"] in seen for g in group):
            continue
        issued = [e for e in ledger if e["amount"] == a["amount"] and toks(e["counterparty"]) & toks(a["counterparty"]) and abs(days(e["date"], a["date"])) <= 15]
        if issued and len(issued) < len(group):
            seen.update(g["id"] for g in group)
            in_run = [g for g in group if g["id"] in run_by_id]
            cleared = [g["id"] for g in in_run if not held(g["id"])]
            sev = "high" if len(cleared) > len(issued) else "note" if in_run else "medium"
            fs.append(finding("C1", sev, f"{a['counterparty']}: {len(group)} payments of {abs(a['amount']):,.2f}, {len(issued)} issued",
                              f"The bank shows {len(group)} debits of the same amount to {a['counterparty']} within ten days; the ledger issued {len(issued)}. "
                              + (f"In this run the preparer cleared {len(cleared)} and held {len(in_run) - len(cleared)} for a person." if in_run else "Before the window this run covers."),
                              [g["id"] for g in group] + [e["id"] for e in issued], "run" if in_run else "trail", head if sev == "high" else None))
    out.append(control("C1", "Duplicate payments", "Same payee and amount leaving the bank twice within ten days while the ledger issued fewer payments.",
                       "exceptions" if fs else "pass", len(outgoing), fs))

    # C2 one vendor under two names
    names: dict[str, set[str]] = {}
    for src in (ledger, bank, db.q(con, "SELECT party AS counterparty, id FROM invoice")):
        for r in src:
            if r.get("counterparty"):
                norm = " ".join(sorted(toks(r["counterparty"])))
                if norm:
                    names.setdefault(norm, set()).add(r["counterparty"])
    keys, fs = sorted(names), []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a != b and SequenceMatcher(None, a, b).ratio() >= 0.88 and a.split()[0][:4] == b.split()[0][:4]:
                fs.append(finding("C2", "low", f"Possible duplicate vendor: {sorted(names[a])[0]} / {sorted(names[b])[0]}",
                                  "Two counterparty names differ by a few characters. One vendor under two names splits its payment history and hides duplicates.", []))
    out.append(control("C2", "Duplicate vendors", "Counterparty names across the ledger, bank and invoices that are nearly identical.",
                       "exceptions" if fs else "pass", len(keys), fs))

    # C3 round-number payments with no invoice behind them
    fs, big = [], [b for b in bank if b["amount"] <= -1000 and db.cents(b["amount"]) % 100000 == 0]
    inv_backed = {(e["amount"], e["ref"]) for e in ledger if e["invoice_id"]}
    for b in big:
        if (b["amount"], b["ref"]) in inv_backed:
            continue
        in_run = b["id"] in run_by_id
        sev = "note" if (in_run and held(b["id"])) or ok_appr(b["id"]) else "medium" if in_run else "low"
        fs.append(finding("C3", sev, f"Round payment of {abs(b['amount']):,.2f} to {b['counterparty'] or 'unnamed payee'}",
                          f"{b['id']} on {b['date']}: a whole-thousand payment with no invoice-backed ledger entry on the same reference. "
                          + ("Held for a person in this run." if in_run and held(b["id"]) else "Approved on file." if ok_appr(b["id"]) else "No approval on file."),
                          [b["id"]], "run" if in_run else "trail", head if sev == "medium" else None))
    out.append(control("C3", "Round-number payments", "Outgoing payments of a whole thousand or more with no invoice behind them.",
                       "exceptions" if any(f["severity"] != "note" for f in fs) else "pass", len([b for b in bank if b["amount"] < 0]), fs))

    # C4 entries posted after the period closed
    fs, late = [], []
    for e in ledger + jes:
        if e.get("posted_at") and e["posted_at"][:10] > close_date(e["date"][:7], info["close_days"]):
            late.append(e)
    consumers = {}
    for i in items:
        for lid in i["ledger_ids"]:
            consumers.setdefault(lid, []).append(i["item_id"])
    for e in late:
        used_by = consumers.get(e["id"], []) + ([e["id"]] if e["id"] in run_by_id and run_by_id[e["id"]]["action"] in AUTO else [])
        linked = [l["bank_id"] for l in links if l["ledger_id"] == e["id"]]
        if ok_appr(e["id"]):
            continue
        if used_by:
            fs.append(finding("C4", "high", f"{e['id']} posted after close and cleared in this run",
                              f"Dated {e['date']}, posted {e['posted_at'][:10]}, after {e['date'][:7]} closed. No approval on file, yet this run matched it ({', '.join(used_by)}).",
                              [e["id"]] + used_by, "run", head))
        elif linked:
            fs.append(finding("C4", "medium", f"{e['id']} posted after close, reconciled with no approval",
                              f"Dated {e['date']}, posted {e['posted_at'][:10]}. Reconciled to {', '.join(linked)} in the trail with no approval recorded.", [e["id"]] + linked))
        else:
            fs.append(finding("C4", "note", f"{e['id']} posted after close, not cleared",
                              f"Dated {e['date']}, posted {e['posted_at'][:10]}. Nothing has been matched to it; "
                              + ("the preparer routed it to a person." if e["id"] in run_by_id and held(e["id"]) else "still open."), [e["id"]],
                              "run" if e["id"] in run_by_id else "trail"))
    out.append(control("C4", "Posted after close", "Ledger and journal entries dated in a month but posted after that month's books closed.",
                       "exceptions" if any(f["severity"] != "note" for f in fs) else "pass", len(ledger) + len(jes), fs))

    # C5 self-approved requests, and approvals by someone without the seniority
    if approvals:
        fs = []
        for a in approvals:
            if a["requested_by"] == a["approver"]:
                fs.append(finding("C5", "high", f"{a['id']}: requested and approved by the same person", f"{a['requested_by']} on {a['date']} for {a['subject_id']}.", [a["id"], a["subject_id"]], route_to=head))
            elif a["status"] == "approved" and not users.get(a["approver"], {}).get("senior"):
                fs.append(finding("C5", "medium", f"{a['id']}: approved by {a['approver']}, who is not a senior role", f"Subject {a['subject_id']}.", [a["id"], a["subject_id"]]))
        linked_bank = {l["bank_id"] for l in links}
        for a in approvals:
            if a["status"] in ("pending", "rejected") and a["subject"] == "bank_line" and a["subject_id"] in linked_bank and not ok_appr(a["subject_id"]):
                fs.append(finding("C5", "medium", f"{a['subject_id']} reconciled while its approval is {a['status']}",
                                  f"{a['id']} asked {a['approver']} on {a['date']}; status is still {a['status']}, but a reconcile link exists.", [a["id"], a["subject_id"]]))
        out.append(control("C5", "Self-approval and open approvals", "Requester and approver are different people, the approver is senior, and nothing is reconciled on a pending or rejected approval.",
                           "exceptions" if fs else "pass", len(approvals), fs))
    else:
        solo = sorted({j["posted_by"] for j in jes})
        out.append(control("C5", "Self-approval and open approvals", "Requester and approver are different people.", "not_testable", 0,
                           [finding("C5", "low", "No approval records exist for this client",
                                    f"The ERP keeps no approvals log, so segregation of duties cannot be evidenced from the books. All {len(jes)} adjustment entries were posted by {', '.join(solo)}. "
                                    "The only sign-off on record is the playbook's.", [])],
                           "The data cannot support this test: the client has no approval workflow. Reported as a design gap, not skipped silently."))

    # C6 payee bank details changed with no approval
    fs, known = [], {}
    cleared_trail = {l["bank_id"] for l in links}
    for b in [x for x in bank if x["amount"] < 0 and x["counterparty"]]:
        m = ACCT.search(b["description"] or "")
        if not m:
            continue
        k = known.setdefault(b["counterparty"], set())
        if k and m.group(1) not in k:
            in_run = b["id"] in run_by_id
            if in_run and not held(b["id"]) and not ok_appr(b["id"]):
                fs.append(finding("C6", "high", f"{b['id']}: paid to a new account and cleared", f"{b['counterparty']} was always paid to {', '.join(sorted(k))}; this one went to {m.group(1)} and the run cleared it with no approval.", [b["id"]], "run", head))
            elif not in_run and b["id"] in cleared_trail and not ok_appr(b["id"]):
                fs.append(finding("C6", "medium", f"{b['id']}: paid to a new account, reconciled with no approval", f"{b['counterparty']}: account ending {m.group(1)}, earlier payments went to {', '.join(sorted(k))}.", [b["id"]]))
            else:
                fs.append(finding("C6", "note", f"{b['id']}: paid to a new account, control held",
                                  f"{b['counterparty']}: account ending {m.group(1)}. " + ("The preparer stopped and routed it to a person." if in_run else "Approved or left open in the trail."), [b["id"]], "run" if in_run else "trail"))
            if b["id"] not in cleared_trail:
                continue
        k.add(m.group(1))
    out.append(control("C6", "Vendor bank-detail changes", "A payment to an account the payee was never paid at before needs an approval before it is cleared.",
                       "exceptions" if any(f["severity"] != "note" for f in fs) else "pass", sum(1 for x in bank if x["amount"] < 0 and ACCT.search(x["description"] or "")), fs))

    # C7 write-offs above the limit a person signed
    limits, fs, n = writeoff_limits(pb), [], 0
    if limits:
        for i in items:
            lim = limits.get(i["rule_id"])
            if i["action"] != "match_adjust" or not lim:
                continue
            n += 1
            off = round(sum(abs(a["amount"]) for a in i["adjustments"] if a["account"] == lim["account"]), 2)
            if off > lim["limit"] + 0.005:
                fs.append(finding("C7", "high", f"{i['item_id']}: {off:,.2f} written off to {lim['account']}, limit {lim['limit']:,.2f}",
                                  f"{i['rule_id']} is signed up to {lim['limit']:,.2f}. The run booked more than that without a person.", [i["item_id"]], "run", head))
        out.append(control("C7", "Write-offs above the signed limit", "No rule books a difference larger than the limit a person stated for it. Limits: "
                           + "; ".join(f"{k} up to {v['limit']:,.2f} to {v['account']}" for k, v in sorted(limits.items())), "exceptions" if fs else "pass", n, fs))
    else:
        out.append(control("C7", "Write-offs above the signed limit", "No rule books a difference larger than the limit a person stated for it.", "not_testable", 0, [],
                           "No signed rule on this playbook states a write-off limit in money, so there is nothing to test against."))

    # C8 money moved on a rule nobody signed
    rules_by_id = {r["id"]: r for r in (pb or {}).get("rules", [])}
    fs, n = [], 0
    for i in items:
        if i["tier"] == "rule" and i["action"] in AUTO:
            n += 1
            r = rules_by_id.get(i["rule_id"])
            if not r or r.get("status") != "approved" or not r.get("executable", True):
                fs.append(finding("C8", "high", f"{i['item_id']} resolved on {i['rule_id']}, which is {r.get('status') if r else 'not in the playbook'}",
                                  "A rule that no person signed decided an item without review.", [i["item_id"]], "run", head))
    out.append(control("C8", "Unsigned rules", "Every item a playbook rule resolved on its own cites a rule that is approved and executable in the playbook version the run used.",
                       "exceptions" if fs else "pass", n, fs))
    return out


# --- 2. re-performance ---------------------------------------------------------------------
def draw_sample(items: list[dict], seed: int, materiality: float, n_random: int) -> dict:
    """Risk-weighted: every item a model resolved on its own, every other self-resolved item at or above materiality,
    then a seeded random draw from what the free tiers cleared, and a few escalations to test for over-caution."""
    rng = random.Random(seed)
    amt = lambda i: abs(i["record"]["amount"])
    model_auto = [i for i in items if i["tier"] == "investigator" and i["action"] in AUTO]
    material = [i for i in items if i["action"] in AUTO and i["tier"] != "investigator" and amt(i) >= materiality]
    taken = {i["item_id"] for i in model_auto + material}
    rest = sorted((i for i in items if i["action"] in AUTO and i["item_id"] not in taken), key=lambda i: i["item_id"])
    rule_pool, other_pool = [i for i in rest if i["tier"] == "rule"], [i for i in rest if i["tier"] != "rule"]
    picks = rng.sample(rule_pool, min(len(rule_pool), max(1, n_random * 2 // 3)))
    picks += rng.sample(other_pool, min(len(other_pool), n_random - len(picks)))
    esc = sorted((i for i in items if i["action"] == "escalate"), key=lambda i: i["item_id"])
    esc_picks = rng.sample(esc, min(len(esc), 4))
    strata = [("model_resolved", model_auto), ("material", material), ("random", picks), ("escalated", esc_picks)]
    sample, why = [], {}
    for name, group in strata:
        for i in group:
            if i["item_id"] not in why:
                why[i["item_id"]] = name
                sample.append(i)
    return {"items": sample, "stratum": why,
            "design": {"seed": seed, "materiality": materiality, "population": len(items),
                       "population_self_resolved": sum(i["action"] in AUTO for i in items),
                       "strata": {name: len([1 for v in why.values() if v == name]) for name, _ in strata},
                       "rule": "All items the model resolved on its own; all other self-resolved items at or above materiality; "
                               f"a seeded random draw of {n_random} from the rest, two thirds from playbook rules; four escalations."}}


def open_ledger(con, period: str) -> list[dict]:
    used = {r["ledger_id"] for r in db.q(con, "SELECT ledger_id FROM reconcile_link WHERE period < ? AND undone_at IS NULL", period)}
    return [e for e in db.q(con, "SELECT * FROM ledger_entry WHERE period <= ? ORDER BY date, id", period) if e["id"] not in used]


def recompute(con, period: str, item: dict, ledger_by_id: dict, open_ids: set, chart: dict) -> list[dict]:
    """Deterministic re-performance. Each check is pass / fail / n/a with the numbers that decide it."""
    rec, checks = item["record"], []
    add = lambda name, ok, detail: checks.append({"check": name, "result": "n/a" if ok is None else "pass" if ok else "fail", "detail": detail})
    if item["action"] not in AUTO:
        add("routed to a real senior role", item["escalate_to"] is not None, f"escalated to {item['escalate_to']}")
        if item["item_kind"] == "bank":
            twins = [e for e in ledger_by_id.values() if e["id"] in open_ids and db.cents(e["amount"]) == db.cents(rec["amount"]) and abs(days(rec["date"], e["date"])) <= 21]
            add("no obvious match was passed over", None if not twins else len(twins) != 1 or bool(item["control_flags"]),
                f"{len(twins)} open entries carry this exact amount" + (" (a control flag explains the stop)" if item["control_flags"] and twins else ""))
        return checks
    les = [ledger_by_id.get(x) for x in item["ledger_ids"]]
    add("ledger entries exist and were open", all(les) and all(x in open_ids for x in item["ledger_ids"]) if item["ledger_ids"] else None,
        ", ".join(item["ledger_ids"]) or "none claimed")
    if all(les):
        base = rec["amount"] if item["item_kind"] == "bank" else 0.0
        residual = round(sum(e["amount"] for e in les) - base - sum(a["amount"] for a in item["adjustments"]), 2)
        if item["item_kind"] == "bank":
            add("ties to the cent", abs(residual) < 0.005, f"ledger {sum(e['amount'] for e in les):,.2f} less bank {base:,.2f} less adjustments {sum(a['amount'] for a in item['adjustments']):,.2f} = {residual:,.2f}")
        for e in les:
            gap = days(rec["date"], e["date"])
            if item["item_kind"] == "bank":
                add(f"{e['id']} dated plausibly", -5 <= gap <= 95, f"bank {rec['date']}, ledger {e['date']} ({gap} days)")
                b, l = toks(rec.get("counterparty")), toks(e.get("counterparty"))
                add(f"{e['id']} same counterparty", None if not b or not l else bool(b & l) or (rec.get("ref") and rec.get("ref") == e.get("ref")),
                    f"bank '{rec.get('counterparty') or ''}' vs ledger '{e.get('counterparty') or ''}'")
    add("adjustment accounts exist in the chart", all(a["account"] in chart for a in item["adjustments"]) if item["adjustments"] else None,
        ", ".join(f"{a['account']} {chart.get(a['account'], 'UNKNOWN')}" for a in item["adjustments"]) or "no adjustments")
    if item["action"] == "match" and item["item_kind"] == "bank" and len(item["ledger_ids"]) == 1:
        rivals = [e["id"] for e in ledger_by_id.values() if e["id"] in open_ids and e["id"] not in item["ledger_ids"] and db.cents(e["amount"]) == db.cents(rec["amount"])
                  and -3 <= days(rec["date"], e["date"]) <= 21 and (not toks(rec.get("counterparty")) or not toks(e.get("counterparty")) or toks(rec.get("counterparty")) & toks(e.get("counterparty")))
                  and not _named(rec, ledger_by_id[item["ledger_ids"][0]], e)]
        add("the match was the only candidate", not rivals, "no rival entry" if not rivals else f"equally good: {', '.join(rivals[:4])}")
    docs = [d for d in (db.q(con, "SELECT * FROM document WHERE id=?", x) for x in item["evidence_ids"] if "-DOC-" in x) if d]
    for d in (x[0] for x in docs):
        if d["type"] == "processor_report" and item["action"] == "match_adjust":
            expected = round(sum(float(d["meta"].get(k) or 0) for k in ("fees", "refunds", "chargebacks")), 2)
            booked = round(sum(a["amount"] for a in item["adjustments"]), 2)
            net_gap = round(float(d["meta"].get("net", 0)) - rec["amount"], 2)
            add(f"adjustments agree with {d['id']}", abs(booked - expected - net_gap) < 0.011,
                f"report deductions {expected:,.2f}, report net less bank {net_gap:,.2f}, booked {booked:,.2f}")
    return checks


def _named(rec: dict, chosen: dict, rival: dict) -> bool:
    """The bank line names the chosen entry (by reference or invoice number) and does not name the rival."""
    text = f"{rec.get('description') or ''} {rec.get('ref') or ''}".lower()
    hit = lambda e: any(k and k.lower() in text for k in (e.get("ref"), e.get("invoice_id")))
    return hit(chosen) and not hit(rival)


AUDIT_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["match", "match_adjust", "book", "carry_forward", "escalate", "cannot_conclude"]},
    "ledger_ids": {"type": "array", "items": {"type": "string"}},
    "adjustments": {"type": "array", "items": {"type": "object", "properties": {"account": {"type": "string"}, "amount": {"type": "number"}}, "required": ["account", "amount"]}},
    "basis": {"type": "string"}}, "required": ["action", "ledger_ids", "adjustments", "basis"]}

AUDIT_SYSTEM = """You are the independent auditor re-performing one bank reconciliation item for {name}. {blurb}
Someone else already resolved this item. You are not told what they did, and you must not guess at it. Work only from the case below.

Decide what the books support:
- match: the bank line equals the listed ledger entries exactly.
- match_adjust: it ties to ledger entries once a difference is booked to named accounts. adjustments sum to (sum of ledger amounts) minus (bank amount).
- book: nothing in the ledger corresponds and the client's signed policy says where such an item goes. adjustments sum to minus the bank amount.
- carry_forward: a ledger entry with no bank line yet, young enough that the signed policy says to wait.
- escalate: the signed policy or a control requires a person (a limit exceeded, bank details changed, a duplicate, an entry posted after close, no policy covers it).
- cannot_conclude: the case does not contain enough to decide either way.

The client's signed policy is given as plain rules. A rule that is not listed is not signed: do not invent one. Amounts are signed (money in is positive).
Use only ledger ids that appear in the case. basis: two or three plain sentences naming the records and numbers you relied on."""


def case_for(con, period: str, item: dict, ledger_open: list[dict], pb: dict | None, chart: dict) -> dict:
    rec = item["record"]
    t = toks(rec.get("counterparty"), rec.get("description") if item["item_kind"] == "bank" else rec.get("memo"))
    score = lambda e: (2 if rec.get("ref") and e.get("ref") == rec.get("ref") else 0) + (2 if db.cents(e["amount"]) == db.cents(rec["amount"]) else 0) \
        + (1 if toks(e.get("counterparty"), e.get("memo")) & t else 0) + (1 if abs(abs(e["amount"]) - abs(rec["amount"])) <= 0.12 * max(1.0, abs(rec["amount"])) else 0)
    cands = []
    if item["item_kind"] == "bank":
        pool = [e for e in ledger_open if -5 <= days(rec["date"], e["date"]) <= 95 and (e["amount"] > 0) == (rec["amount"] > 0) and score(e) > 0]
        cands = sorted(pool, key=lambda e: (-score(e), abs(days(rec["date"], e["date"]))))[:12]
    refs = {rec.get("ref") or ""} | {c.get("ref") or "" for c in cands[:6]} | {c.get("invoice_id") or "" for c in cands[:6]}
    refs = {r.lower() for r in refs if r}
    lo, hi = (date.fromisoformat(rec["date"]) - timedelta(days=20)).isoformat(), (date.fromisoformat(rec["date"]) + timedelta(days=7)).isoformat()
    docs = []   # documents that name a reference come first; ones that only name the counterparty fill what is left
    for d in db.q(con, "SELECT * FROM document WHERE type != 'internal_email' AND date BETWEEN ? AND ? ORDER BY date DESC", lo, hi):
        text = f"{d['subject']} {d['body']} {json.dumps(d['meta'])}".lower()
        by_ref = any(r in text for r in refs)
        if by_ref or (toks(rec.get("counterparty")) and toks(rec.get("counterparty")) <= toks(text)):
            docs.append((0 if by_ref else 1, {"id": d["id"], "type": d["type"], "date": d["date"], "sender": d["sender"], "subject": d["subject"],
                                              "body": (d["body"] or "")[:1200], "meta": d["meta"]}))
    docs = [d for _, d in sorted(docs, key=lambda x: x[0])]
    prior = []
    if item["item_kind"] == "bank" and rec["amount"] < 0 and rec.get("counterparty"):
        prior = [{"id": b["id"], "date": b["date"], "amount": b["amount"], "description": b["description"]} for b in
                 db.q(con, "SELECT * FROM bank_line WHERE counterparty=? AND date < ? ORDER BY date DESC LIMIT 5", rec["counterparty"], rec["date"])]
        prior += [{"id": b["id"], "date": b["date"], "amount": b["amount"], "description": b["description"], "note": "same period"} for b in
                  db.q(con, "SELECT * FROM bank_line WHERE counterparty=? AND period=? AND id != ? AND amount=?", rec["counterparty"], period, rec["id"], rec["amount"])]
    policy = [f"{r['id']}: {r['text']}" for r in (pb or {}).get("rules", []) if r.get("status") == "approved"]
    info = db.q(con, "SELECT * FROM client")[0]
    case = {"item_kind": item["item_kind"], "item": rec, "period": period, "period_close": close_date(period, info["close_days"]),
            "candidate_ledger_entries": cands, "documents": docs[:5], "other_payments_to_this_payee": prior, "chart_of_accounts": chart, "signed_policy": policy}
    if item["item_kind"] == "ledger":
        case["age_days_at_period_end"] = days(close_date(period, -2), rec["date"]) - 1
    return case


def same_adjustments(a: list[dict], b: list[dict]) -> bool:
    tot = lambda xs: {k: round(sum(x["amount"] for x in xs if str(x["account"]) == k), 2) for k in {str(x["account"]) for x in xs}}
    ta, tb = tot(a), tot(b)
    return set(ta) == set(tb) and all(abs(ta[k] - tb[k]) < 0.011 for k in ta)


def compare(prep: dict, aud: dict) -> tuple[str, str]:
    if aud["action"] == "cannot_conclude":
        return "cannot_conclude", "The auditor could not decide from the case file."
    p_auto, a_auto = prep["action"] in AUTO, aud["action"] in AUTO
    if prep["action"] == "escalate" and aud["action"] == "escalate":
        return "agree", "Both stopped and asked a person."
    if prep["action"] == "carry_forward" or aud["action"] == "carry_forward":
        return ("agree", "Both carried it forward.") if prep["action"] == aud["action"] else ("disagree", f"Preparer: {prep['action']}. Auditor: {aud['action']}.")
    if p_auto and a_auto:
        if sorted(prep["ledger_ids"]) != sorted(aud["ledger_ids"]):
            return "disagree", f"Different ledger entries: preparer {prep['ledger_ids'] or 'none'}, auditor {aud['ledger_ids'] or 'none'}."
        if not same_adjustments(prep["adjustments"], aud["adjustments"]):
            return "disagree", "Same entries, but the difference was booked to different accounts or amounts."
        return "agree", "Same entries, same adjustments, to the cent."
    if p_auto and not a_auto:
        return "disagree", "The preparer resolved it without a person; the auditor would have asked one."
    return "cautious", "The preparer asked a person; the auditor would have resolved it. Costs review time, not money."


def model_reperform(con_factory, period: str, item: dict, ledger_open, pb, chart, info) -> dict:
    usage = llm.Usage()
    case = case_for(con_factory(), period, item, ledger_open, pb, chart)
    try:
        reply = llm.call(AUDIT_SYSTEM.format(name=info["name"], blurb=info["blurb"]), [{"role": "user", "content": json.dumps(case, default=str)}],
                         schema=AUDIT_SCHEMA, max_tokens=6000, usage=usage)
        out = json.loads(reply.text)
        known = {c["id"] for c in case["candidate_ledger_entries"]}
        if any(x not in known for x in out["ledger_ids"]):
            out = out | {"action": "cannot_conclude", "basis": "Named a ledger entry that was not in the case. Discarded. " + out["basis"]}
    except Exception as e:                                            # a failed call is recorded, never counted as agreement
        out = {"action": "cannot_conclude", "ledger_ids": [], "adjustments": [], "basis": f"Model re-performance failed: {type(e).__name__}"}
    out["adjustments"] = [{"account": str(a["account"]), "amount": round(float(a["amount"]), 2)} for a in out["adjustments"]]
    return {"case_size": {"candidates": len(case["candidate_ledger_entries"]), "documents": len(case["documents"]), "policy_rules": len(case["signed_policy"])},
            "auditor": out, "usage": usage.as_dict()}


# --- 3. consistency ------------------------------------------------------------------------
def consistency(con, period: str, meta: dict, items: list[dict], pb: dict | None, chart: dict) -> list[dict]:
    out = []
    chk = lambda code, name, what, bad, tested: out.append({"code": code, "name": name, "what": what, "status": "pass" if not bad else "exceptions", "tested": tested,
                                                            "exceptions": len(bad), "details": bad[:10]})
    dates = [i["record"]["date"] for i in items]
    lo, hi = (min(dates), max(dates)) if dates else ("", "")
    bank = db.q(con, "SELECT id FROM bank_line WHERE period=? AND date BETWEEN ? AND ?", period, lo, hi)
    ids = [i["item_id"] for i in items]
    missing = [b["id"] for b in bank if b["id"] not in set(ids)]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    chk("K1", "One disposition per bank line", f"Every bank line from {lo} to {hi} has exactly one disposition in the run: none missing, none twice.",
        [f"missing {x}" for x in missing] + [f"twice {x}" for x in dup], len(bank))
    use: dict[str, list[str]] = {}
    for i in items:
        for lid in i["ledger_ids"]:
            use.setdefault(lid, []).append(i["item_id"])
    prior = {r["ledger_id"] for r in db.q(con, "SELECT ledger_id FROM reconcile_link WHERE period < ? AND undone_at IS NULL", period)}
    carried = {i["item_id"] for i in items if i["item_kind"] == "ledger" and i["action"] == "carry_forward"}
    bad = [f"{l} claimed by {', '.join(v)}" for l, v in use.items() if len(v) > 1] + [f"{l} was already reconciled in an earlier period" for l in use if l in prior] \
        + [f"{l} is both matched and carried forward" for l in use if l in carried]
    chk("K2", "No ledger entry consumed twice", "A ledger entry clears once: not by two bank lines, not again after an earlier period, not matched and carried forward at once.", bad, len(use))
    led = {e["id"]: e for e in db.q(con, "SELECT * FROM ledger_entry")}
    bad, n, booked = [], 0, 0.0
    for i in items:
        if i["action"] in AUTO and i["item_kind"] == "bank" and all(x in led for x in i["ledger_ids"]):
            n += 1
            adj = sum(a["amount"] for a in i["adjustments"])
            booked += abs(adj)
            r = round(sum(led[x]["amount"] for x in i["ledger_ids"]) - i["record"]["amount"] - adj, 2)
            if abs(r) >= 0.005:
                bad.append(f"{i['item_id']} is out by {r:,.2f}")
    chk("K3", "Adjustments equal the differences", f"For every self-resolved bank line, ledger less bank less adjustments is zero. {booked:,.2f} of adjustments tested.", bad, n)
    bad = [f"{i['item_id']} books to {a['account']}, not in the chart" for i in items for a in i["adjustments"] if a["account"] not in chart]
    chk("K4", "Accounts exist", "Every adjustment goes to an account in the client's chart.", bad, sum(len(i["adjustments"]) for i in items))
    rules = {r["id"]: r for r in (pb or {}).get("rules", [])}
    ver = meta.get("playbook_version")
    bad = []
    cited = [i for i in items if i["rule_id"]]
    for i in cited:
        r = rules.get(i["rule_id"])
        if not r:
            bad.append(f"{i['item_id']} cites {i['rule_id']}, absent from playbook v{ver}")
        elif r.get("version_added") and ver and r["version_added"] > ver:
            bad.append(f"{i['item_id']} cites {i['rule_id']}, added in v{r['version_added']}, after the run's v{ver}")
    chk("K5", "Cited rules existed", f"Every rule a resolution cites is in playbook v{ver}, the version the run says it used.", bad, len(cited))
    known = set()
    for t in ("bank_line", "ledger_entry", "document", "invoice", "reconcile_link", "journal_entry", "approval"):
        known |= {r["id"] for r in db.q(con, f"SELECT id FROM {t}")}
    refs = [(i["item_id"], x) for i in items for x in i["precedent_ids"] + i["evidence_ids"]]
    bad = [f"{a} cites {x}, which is not in the books" for a, x in refs if x not in known]
    chk("K6", "Evidence is real", "Every precedent and evidence id a resolution cites opens a real record.", bad, len(refs))
    late = [(a, x) for a, x in refs if x in led and led[x]["period"] > period]
    chk("K7", "Nothing from the future", "No resolution relies on a ledger entry from a later period.", [f"{a} cites {x}" for a, x in late], len(refs))
    roles = {u["role"].lower().replace(" ", "_").replace("-", "_") for u in db.q(con, "SELECT * FROM user WHERE senior=1")}
    esc = [i for i in items if i["action"] == "escalate"]
    bad = [f"{i['item_id']} is routed to {i['escalate_to'] or 'nobody'}" + (f" by {i['rule_id']}" if i["rule_id"] else "") + f"; this client has {', '.join(sorted(roles))}"
           for i in esc if i["escalate_to"] not in roles]
    chk("K8", "Escalations reach a real person", "Every escalation is routed to a senior role that exists at this client.", bad, len(esc))
    return out


# --- assemble ------------------------------------------------------------------------------
def audit_file(con, item: dict, stratum: str, checks: list[dict], model: dict | None, verdict: dict, pb: dict | None, meta: dict, chart: dict) -> dict:
    rules = {r["id"]: r for r in (pb or {}).get("rules", [])}
    r = rules.get(item["rule_id"]) if item["rule_id"] else None
    get = lambda t, x: (db.q(con, f"SELECT * FROM {t} WHERE id=?", x) or [None])[0]
    docs = [get("document", x) for x in item["evidence_ids"] if "-DOC-" in x]
    appr = db.q(con, "SELECT * FROM approval WHERE subject_id=?", item["item_id"])
    signed = None
    if r:
        signed = {"status": r.get("status"), "origin": r.get("origin"), "human_confirmed": r.get("human_confirmed"), "version_added": r.get("version_added"),
                  "agrees_with_history": (r.get("backtest") or {}).get("support"), "conflicts_with_history": (r.get("backtest") or {}).get("conflicts")}
    return {"item_id": item["item_id"], "item_kind": item["item_kind"], "stratum": stratum, "record": item["record"],
            "preparer": {"tier": item["tier"], "action": item["action"], "ledger_ids": item["ledger_ids"], "adjustments": item["adjustments"],
                         "escalate_to": item["escalate_to"], "control_flags": item["control_flags"]},
            "ledger_lines": [x for x in (get("ledger_entry", i) for i in item["ledger_ids"]) if x],
            "rule": {"id": r["id"], "text": r["text"], "playbook_version": meta.get("playbook_version"), "track": meta.get("track")} | signed if r else None,
            "precedent_ids": item["precedent_ids"], "documents": [{"id": d["id"], "type": d["type"], "date": d["date"], "subject": d["subject"]} for d in docs if d],
            "approvals": [{"id": a["id"], "approver": a["approver"], "status": a["status"], "date": a["date"]} for a in appr],
            "approved_by": ("playbook sign-off on " + r["id"]) if r and r.get("status") == "approved" and item["action"] in AUTO else
                           (item["escalate_to"] and f"waiting on {item['escalate_to']}") or ("deterministic match, no judgement involved" if item["tier"] == "matcher" else "no human approval on record"),
            "auditor": {"checks": checks, "model": model, "verdict": verdict["verdict"], "why": verdict["why"]}}


def run_audit(client: str, run_dir: Path, seed: int = 7, materiality: float | None = None, n_random: int = 12, use_llm: bool = True,
              model_cap: int = MODEL_CAP, workers: int = 6, out_path: Path | None = None, reuse: bool = False) -> dict:
    con = db.connect(client, readonly=True)
    info = db.q(con, "SELECT * FROM client")[0]
    chart = {str(k): v for k, v in info["chart"].items()}
    meta, items = load_run(run_dir)
    period = meta["period"]
    pb = pbmod.load(client, meta.get("track") or "main", meta.get("playbook_version")) if meta.get("playbook_version") else None
    if materiality is None:        # 1% of the money that moved through the bank in the run, floored to something a person would call material
        total = sum(abs(i["record"]["amount"]) for i in items if i["item_kind"] == "bank")
        materiality = float(max(500, round(total * 0.01, -2)))

    controls = control_tests(con, period, pb, items)
    cons = consistency(con, period, meta, items, pb, chart)
    smp = draw_sample(items, seed, materiality, n_random)
    ledger_open = open_ledger(con, period)
    ledger_by_id = {e["id"]: e for e in db.q(con, "SELECT * FROM ledger_entry")}
    open_ids = {e["id"] for e in ledger_open}

    order = {"model_resolved": 0, "material": 1, "random": 2, "escalated": 3}
    for_model = sorted(smp["items"], key=lambda i: (order[smp["stratum"][i["item_id"]]], -abs(i["record"]["amount"])))[:model_cap] if use_llm else []
    model_out: dict[str, dict] = {}
    prev = out_path or db.RUNS / f"audit_{client}.json"
    if reuse and prev.exists():       # same run, same sample: keep the model's earlier verdicts and their cost
        old = json.loads(prev.read_text())
        if old.get("run_id") == meta["run_id"]:
            model_out = {f["item_id"]: f["auditor"]["model"] for f in old["files"] if f["auditor"]["model"] and f["item_id"] in {i["item_id"] for i in for_model}}
    for_model = [i for i in for_model if i["item_id"] not in model_out]
    if for_model:
        with ThreadPoolExecutor(workers) as ex:
            for it, res in zip(for_model, ex.map(lambda i: model_reperform(lambda: db.connect(client, readonly=True), period, i, ledger_open, pb, chart, info), for_model)):
                model_out[it["item_id"]] = res

    files, findings, tally = [], [], {"agree": 0, "disagree": 0, "cautious": 0, "cannot_conclude": 0}
    head = next((u["role"].lower().replace(" ", "_").replace("-", "_") for u in db.q(con, "SELECT * FROM user WHERE senior=1")), None)
    for it in smp["items"]:
        checks = recompute(con, period, it, ledger_by_id, open_ids, chart)
        failed = [c for c in checks if c["result"] == "fail"]
        m = model_out.get(it["item_id"])
        if failed:
            verdict = {"verdict": "disagree", "why": "Failed re-computation: " + "; ".join(f"{c['check']} ({c['detail']})" for c in failed)}
        elif m:
            v, why = compare(it, m["auditor"])
            verdict = {"verdict": v, "why": why}
        else:
            verdict = {"verdict": "agree", "why": "Re-computed in code: every check passed. Not sent to the model."}
        tally[verdict["verdict"]] += 1
        if verdict["verdict"] == "disagree":
            big = abs(it["record"]["amount"]) >= materiality
            findings.append(finding("R1", "high" if big and it["action"] in AUTO else "medium", f"{it['item_id']}: auditor disagrees with the preparer",
                                    verdict["why"] + (f" Auditor's basis: {m['auditor']['basis']}" if m else ""), [it["item_id"]] + it["ledger_ids"], "run", head))
        files.append(audit_file(con, it, smp["stratum"][it["item_id"]], checks, m, verdict, pb, meta, chart))

    for c in controls:
        findings += [f for f in c["findings"] if f["severity"] != "note"]
    for k in cons:
        if k["status"] != "pass":
            findings.append(finding(k["code"], "medium" if k["code"] == "K8" else "high", f"Consistency: {k['name']}", "; ".join(k["details"]),
                                    [d.split(" ")[0] for d in k["details"]], "run", head))
    findings.sort(key=lambda f: (SEVERITY[f["severity"]], f["where"] != "run"))
    concluded = tally["agree"] + tally["disagree"] + tally["cautious"]
    cost = round(sum(m["usage"]["cost_usd"] for m in model_out.values()), 4)
    tiers = meta.get("tiers", {})
    report = {
        "client": client, "client_name": info["name"], "run_id": meta["run_id"], "period": period, "playbook_version": meta.get("playbook_version"),
        "created_at": datetime.now().isoformat(timespec="seconds"), "auditor_model": llm.MODEL if model_out else None,
        "independence": "The auditor reads the books and each item's disposition only. Rationale, trace, confidence, grades and metrics are never loaded.",
        "team": {"preparer": {"items": meta["n_items"], "matcher": tiers.get("matcher", 0), "rules": tiers.get("rule", 0),
                              "model": tiers.get("investigator", 0) + tiers.get("guardrail", 0), "self_resolved": sum(i["action"] in AUTO for i in items),
                              "carried_forward": sum(i["action"] == "carry_forward" for i in items)},
                 "approver": {"escalated": sum(i["action"] == "escalate" for i in items), "signed_rules": sum(r.get("status") == "approved" for r in (pb or {}).get("rules", [])),
                              "roles": sorted({i["escalate_to"] for i in items if i["escalate_to"]})},
                 "auditor": {"controls": len(controls), "sampled": len(smp["items"]), "model_reperformed": len(model_out), "findings": len(findings),
                             "routed_back": sum(1 for f in findings if f["route_to"])}},
        "controls": controls, "consistency": cons, "sample": smp["design"],
        "reperformance": tally | {"sampled": len(smp["items"]), "concluded": concluded, "agreement_rate": round(tally["agree"] / concluded, 4) if concluded else None,
                                  "model_reperformed": len(model_out), "code_only": len(smp["items"]) - len(model_out)},
        "findings": findings, "findings_by_severity": {s: sum(f["severity"] == s for f in findings) for s in ("high", "medium", "low")},
        "cost_usd": cost, "llm_calls": sum(m["usage"]["llm_calls"] for m in model_out.values()), "files": files}
    out_path = out_path or db.RUNS / f"audit_{client}.json"
    out_path.write_text(json.dumps(report, indent=1, default=str))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--run", required=True, help="run directory, e.g. runs/A_2026-04_corrected")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--materiality", type=float)
    ap.add_argument("--random", type=int, default=12, dest="n_random")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--cap", type=int, default=MODEL_CAP)
    ap.add_argument("--reuse", action="store_true", help="keep model re-performances already in the report for this run")
    a = ap.parse_args()
    run_dir = Path(a.run) if Path(a.run).is_absolute() else db.ROOT / a.run
    rep = run_audit(a.client, run_dir, a.seed, a.materiality, a.n_random, not a.no_llm, a.cap, reuse=a.reuse)
    print(json.dumps({k: rep[k] for k in ("client", "run_id", "sample", "reperformance", "findings_by_severity", "cost_usd", "llm_calls")}, indent=1))
    for c in rep["controls"] + rep["consistency"]:
        print(f"  {c['code']} {c['status']:<12} tested {c['tested']:<5} exceptions {c['exceptions']:<3} {c['name']}")
    for f in rep["findings"][:12]:
        print(f"  [{f['severity']}] {f['title']}")
