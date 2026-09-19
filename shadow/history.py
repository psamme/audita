"""Read what the ERP retained about past reconciliation work and reconstruct what happened to each item.

There is no table of decisions or reasons. There are reconcile links, adjustment journal entries, who posted them
and when, an approvals log where a workflow exists, and an inbox. `observe` turns those into one record per item:
what was linked, what was booked where, by whom, how long it took, and whether anyone senior touched it.
"""
from shadow import db
from shadow.matcher import days_between, period_end


def role_key(role: str) -> str:
    return role.lower().replace(" ", "_").replace("-", "_")


def observe(con, before: str) -> list[dict]:
    """Observed handling of every bank line and ledger entry in periods strictly before `before`."""
    users = {u["id"]: u for u in db.q(con, "SELECT * FROM user")}
    ledger = {e["id"]: e for e in db.q(con, "SELECT * FROM ledger_entry WHERE period < ?", before)}
    links, jes, approvals = {}, {}, {}
    for l in db.q(con, "SELECT * FROM reconcile_link WHERE period < ? AND undone_at IS NULL ORDER BY id", before):
        links.setdefault(l["bank_id"], []).append(l)
    all_je = db.q(con, "SELECT * FROM journal_entry WHERE period < ? ORDER BY id", before)
    reversed_ids = {j["reverses_id"] for j in all_je if j["reverses_id"]}
    for j in all_je:
        if j["id"] not in reversed_ids and not j["reverses_id"]:
            jes.setdefault(j["bank_id"], []).append(j)
    for a in db.q(con, "SELECT * FROM approval WHERE period < ? ORDER BY id", before):
        approvals.setdefault(a["subject_id"], []).append(a)

    out = []
    linked_in = {}  # ledger id -> period of the bank line that cleared it
    for b in db.q(con, "SELECT * FROM bank_line WHERE period < ? ORDER BY date, id", before):
        ls, js, aps = links.get(b["id"], []), jes.get(b["id"], []), approvals.get(b["id"], [])
        for l in ls:
            linked_in[l["ledger_id"]] = b["period"]
        touches = [(l["reconciled_by"], l["reconciled_at"]) for l in ls] + [(j["posted_by"], j["posted_at"]) for j in js]
        seniors = [users[u] for u, _ in touches if users.get(u, {}).get("senior")] + \
                  [users[a["approver"]] for a in aps if a["approver"] in users]
        les = [ledger[l["ledger_id"]] for l in ls if l["ledger_id"] in ledger]
        total = round(sum(e["amount"] for e in les), 2)
        o = {"item_kind": "bank", "item": b, "period": b["period"], "ledger": les,
             "adjustments": [{"account": j["account"], "amount": j["amount"], "memo": j["memo"], "by": j["posted_by"],
                              "entry_id": j["id"]} for j in js],
             "handled_by": sorted({u for u, _ in touches}), "lag_days": max((days_between(t, b["date"]) for _, t in touches), default=None),
             "approvals": [{"approver": a["approver"], "status": a["status"], "comment": a["comment"]} for a in aps],
             "open": not touches, "reviewed_by": role_key(seniors[0]["role"]) if seniors else None,
             "diff": round(total - b["amount"], 2) if les else None,
             "evidence_ids": [l["id"] for l in ls] + [j["id"] for j in js]}
        o["outcome"] = _outcome(o)
        o["routine"] = (o["outcome"] and o["outcome"]["action"] == "match" and len(les) == 1 and not o["reviewed_by"])
        out.append(o)
    for e in ledger.values():
        aps = approvals.get(e["id"], [])
        cleared = linked_in.get(e["id"])
        if cleared == e["period"] or (not aps and cleared is None and not e["posted_at"] > period_end(e["date"][:7])):
            continue
        seniors = [users[a["approver"]] for a in aps if a["approver"] in users]
        o = {"item_kind": "ledger", "item": e, "period": e["period"], "ledger": [], "adjustments": [],
             "handled_by": [], "lag_days": None,
             "approvals": [{"approver": a["approver"], "status": a["status"], "comment": a["comment"]} for a in aps],
             "open": cleared is None, "reviewed_by": role_key(seniors[0]["role"]) if seniors else None, "diff": None,
             "cleared_in": cleared, "age_days_at_period_end": days_between(period_end(e["period"]), e["date"]),
             "evidence_ids": [a["id"] for a in aps]}
        o["outcome"] = ({"action": "escalate", "ledger_ids": [], "adjustments": [], "escalate_to": o["reviewed_by"]}
                        if o["reviewed_by"] else
                        {"action": "carry_forward", "ledger_ids": [], "adjustments": [], "escalate_to": None} if cleared else None)
        o["routine"] = False
        out.append(o)
    return out


def _outcome(o: dict) -> dict | None:
    """What the trail implies was done, in the agent's own action vocabulary. None when the trail is silent."""
    if o["reviewed_by"]:
        return {"action": "escalate", "ledger_ids": [], "adjustments": [], "escalate_to": o["reviewed_by"]}
    if o["open"]:
        return None
    ids = sorted(e["id"] for e in o["ledger"])
    adj = [{"account": a["account"], "amount": a["amount"]} for a in o["adjustments"]]
    action = "match_adjust" if ids and adj else "match" if ids else "book"
    return {"action": action, "ledger_ids": ids, "adjustments": adj, "escalate_to": None}


def internal_mail(con, before: str) -> list[dict]:
    return [d for d in db.q(con, "SELECT * FROM document WHERE type='internal_email' ORDER BY date")
            if d["meta"].get("period", "") < before]
