"""When the company has statements and a ledger but no record of who cleared what.

Most exports arrive that way, and the playbook is induced from what the humans did, so without a
trail there is nothing to learn from. Three steps close the gap.

1. Derive the routine links. The deterministic matcher already clears exact, mutually unique
   one-to-one matches, and those are precisely the items `history.observe` would call routine and
   `playbook.cases` would then skip. So deriving them teaches nothing on its own, and that is the
   point: it is triage. It makes the open-ledger view correct for later periods and shrinks what
   is left to a queue a person can actually work through.
2. Label the residue. What the matcher would not touch is the exception set, which is where the
   policy lives. Each label writes the same rows an ERP would have left behind.
3. Stop when each recurring shape has enough examples to support a rule.

Labels are written straight to the database. They deliberately do not go through `correct.correct`,
which would spend a model call and cut a new playbook version for every single one.
"""
import hashlib
import re
from datetime import date, timedelta

from shadow import db, guardrails, matcher
from shadow.onboard import ids

AUTO_PREFIX = "LNKA"          # derived links are greppable and removable as a group
_SHAPE = re.compile(r"[A-Z]*\d[\w-]*")


def _jitter(seed: str) -> int:
    """Deterministic 0 to 2 days, so a rerun produces the same trail and tests are stable."""
    return int(hashlib.sha1(seed.encode()).hexdigest(), 16) % 3


def _next_business_day(iso: str, add: int) -> str:
    d = date.fromisoformat(iso) + timedelta(days=1 + add)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def periods(con, before: str | None = None) -> list[str]:
    sql = "SELECT DISTINCT period FROM bank_line"
    args: tuple = ()
    if before:
        sql += " WHERE period < ?"
        args = (before,)
    return [r["period"] for r in db.q(con, sql + " ORDER BY 1", *args)]


def derive_links(client: str, reconciler: str, before: str | None = None, job=None) -> dict:
    """Write a reconcile_link for every unique one-to-one match the matcher makes."""
    con = db.connect(client, readonly=True)
    try:
        ps = periods(con, before)
        plan = []
        for period in ps:
            if job:
                job.phase(f"matching {period}")
            flagged = guardrails.scan(con, period)
            matches, _ = matcher.run(con, period, skip=set(flagged))
            for m in matches:
                if len(m["ledger_ids"]) != 1:
                    continue          # many-to-one is not routine; leave it for a human
                bank = db.q(con, "SELECT date, period FROM bank_line WHERE id=?", m["bank_id"])
                led = db.q(con, "SELECT amount FROM ledger_entry WHERE id=?", m["ledger_ids"][0])
                if not bank or not led:
                    continue
                plan.append((m["bank_id"], m["ledger_ids"][0], bank[0]["period"],
                             bank[0]["date"], led[0]["amount"]))
    finally:
        con.close()

    alloc = ids.Allocator(client)
    written = skipped = 0
    con = db.connect(client, readonly=False)
    try:
        existing = {r["bank_id"] for r in db.q(con, "SELECT bank_id FROM reconcile_link")}
        for bank_id, ledger_id, period, bdate, amount in plan:
            if bank_id in existing:
                skipped += 1
                continue
            rid, _ = alloc.allocate("reconcile_link", {"bank_ref": bank_id, "ledger_ref": ledger_id},
                                    prefix=AUTO_PREFIX)
            when = _next_business_day(bdate, _jitter(bank_id))
            con.execute("INSERT OR REPLACE INTO reconcile_link VALUES (?,?,?,?,?,?,?,?)",
                        (rid, period, bank_id, ledger_id, amount, reconciler, when, None))
            written += 1
        con.commit()
    finally:
        con.close()
    alloc.commit()
    return {"periods": ps, "derived": written, "already_linked": skipped}


def clear_derived(client: str) -> dict:
    """Remove every derived link, leaving anything the company actually imported."""
    con = db.connect(client, readonly=False)
    try:
        cur = con.execute("DELETE FROM reconcile_link WHERE id LIKE ?", (f"{client}-{AUTO_PREFIX}-%",))
        con.commit()
        return {"deleted": cur.rowcount}
    finally:
        con.close()


def shape(text: str) -> str:
    """The same normalisation induction clusters on, so the coverage counter predicts rule support."""
    return _SHAPE.sub("#", text or "")[:80]


def residue(client: str, before: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Bank lines with no link yet, newest first, each with the candidates a person would weigh."""
    con = db.connect(client, readonly=True)
    try:
        linked = {r["bank_id"] for r in db.q(con, "SELECT bank_id FROM reconcile_link WHERE undone_at IS NULL")}
        booked = {r["bank_id"] for r in db.q(con, "SELECT bank_id FROM journal_entry WHERE bank_id IS NOT NULL")}
        ps = periods(con, before)
        items, shapes = [], {}
        for period in ps:
            flagged = guardrails.scan(con, period)
            open_ledger = matcher.open_ledger(con, period)
            for b in db.q(con, "SELECT * FROM bank_line WHERE period=? ORDER BY date DESC, id", period):
                s = shape(b["description"])
                done = b["id"] in linked or b["id"] in booked
                bucket = shapes.setdefault(s, {"shape": s, "total": 0, "labelled": 0})
                bucket["total"] += 1
                bucket["labelled"] += done
                if done:
                    continue
                items.append({"item": b, "period": period, "shape": s,
                              "control_flags": flagged.get(b["id"], []),
                              "candidates": _candidates(b, open_ledger)})
        items.sort(key=lambda it: (it["item"]["date"], it["item"]["id"]), reverse=True)
        total = len(items)
        window = items[offset:offset + limit]
    finally:
        con.close()
    ready = [s for s in shapes.values() if s["labelled"] >= 3]
    return {"total": total, "returned": len(window), "offset": offset, "items": window,
            "shapes": sorted(shapes.values(), key=lambda s: -s["total"])[:20],
            "kinds_ready": len(ready), "kinds": len(shapes)}


def _candidates(bank: dict, open_ledger: list[dict], k: int = 6) -> list[dict]:
    """Plausible ledger entries for this line, closest amount first, so labelling is clicking."""
    same_sign = [e for e in open_ledger
                 if (e["amount"] > 0) == (bank["amount"] > 0)
                 and -8 <= matcher.days_between(bank["date"], e["date"]) <= 25]
    want = matcher.tokens(bank["description"], bank["counterparty"])
    scored = sorted(same_sign, key=lambda e: (
        -len(want & matcher.tokens(e["memo"], e["counterparty"], e["ref"])),
        abs(round(e["amount"] - bank["amount"], 2))))
    return [dict(e) | {"difference": round(e["amount"] - bank["amount"], 2)} for e in scored[:k]]


ACTIONS = ("match", "match_adjust", "book", "escalate", "carry_forward")


def label(client: str, item_id: str, action: str, ledger_ids: list[str] | None = None,
          adjustments: list[dict] | None = None, handled_by: str = "",
          days_to_handle: int = 1, senior_signed_off: bool = False,
          escalate_to: str | None = None, note: str = "") -> dict:
    """Record one past decision as the rows an ERP would have left behind.

    A link says it was cleared; an adjusting entry says where the difference went; an approval row
    says a senior was involved. Those three, joined to the roster, are what induction reads.
    """
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {list(ACTIONS)}")
    con = db.connect(client, readonly=True)
    try:
        rows = db.q(con, "SELECT * FROM bank_line WHERE id=?", item_id)
        if not rows:
            raise ValueError("no such bank line")
        bank = rows[0]
        people = {u["id"]: u for u in db.q(con, "SELECT * FROM user")}
        seniors = [u for u in people.values() if u["senior"]]
        entries = [db.q(con, "SELECT * FROM ledger_entry WHERE id=?", i) for i in (ledger_ids or [])]
    finally:
        con.close()
    if handled_by and handled_by not in people:
        raise ValueError(f"{handled_by} is not on the roster")
    entries = [e[0] for e in entries if e]
    if action in ("match", "match_adjust") and not entries:
        raise ValueError("that action needs at least one ledger entry")

    who = handled_by or (seniors[0]["id"] if senior_signed_off and seniors else "")
    when = _next_business_day(bank["date"], max(0, int(days_to_handle) - 1))
    alloc = ids.Allocator(client)
    written = {"links": 0, "journal_entries": 0, "approvals": 0}

    con = db.connect(client, readonly=False)
    try:
        if action in ("match", "match_adjust"):
            for e in entries:
                rid, _ = alloc.allocate("reconcile_link",
                                        {"bank_ref": item_id, "ledger_ref": e["id"]})
                con.execute("INSERT OR REPLACE INTO reconcile_link VALUES (?,?,?,?,?,?,?,?)",
                            (rid, bank["period"], item_id, e["id"], e["amount"], who, when, None))
                written["links"] += 1
        if action == "match_adjust":
            total = round(sum(e["amount"] for e in entries), 2)
            diff = round(total - bank["amount"], 2)
            given = round(sum(a["amount"] for a in (adjustments or [])), 2)
            if abs(diff - given) > 0.011:
                raise ValueError(f"adjustments sum to {given} but the difference is {diff}")
        if action == "book":
            given = round(sum(a["amount"] for a in (adjustments or [])), 2)
            if abs(given + bank["amount"]) > 0.011:
                raise ValueError(f"adjustments must sum to {round(-bank['amount'], 2)}")
        for a in (adjustments or []):
            rid, _ = alloc.allocate("journal_entry",
                                    {"bank_ref": item_id, "account": a["account"],
                                     "amount": a["amount"]})
            con.execute("INSERT OR REPLACE INTO journal_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (rid, bank["period"], bank["date"], when, who, a["account"],
                         round(float(a["amount"]), 2), a.get("memo") or note, item_id, None))
            written["journal_entries"] += 1
        if senior_signed_off or action == "escalate":
            # escalate_to may be a person's id or a senior role; either resolves to a user id,
            # because seniority is read off the user row the trail points at.
            approver = ""
            if escalate_to:
                approver = next((u["id"] for u in seniors
                                 if escalate_to in (u["id"], u["role"])), "")
            if not approver and seniors:
                approver = seniors[0]["id"]
            if approver:
                rid, _ = alloc.allocate("approval", {"bank_ref": item_id, "date": when})
                con.execute("INSERT OR REPLACE INTO approval VALUES (?,?,?,?,?,?,?,?,?)",
                            (rid, bank["period"], when, "bank_line", item_id, handled_by or "",
                             approver, "approved", note))
                written["approvals"] += 1
        con.commit()
    finally:
        con.close()
    alloc.commit()
    left = residue(client, limit=1)
    return {"item_id": item_id, "written": written, "remaining": left["total"],
            "kinds_ready": left["kinds_ready"], "kinds": left["kinds"]}


def unlabel(client: str, item_id: str) -> dict:
    """Undo one label: remove the rows it wrote for that item."""
    con = db.connect(client, readonly=False)
    try:
        a = con.execute("DELETE FROM reconcile_link WHERE bank_id=? AND id NOT LIKE ?",
                        (item_id, f"%-{AUTO_PREFIX}-%")).rowcount
        b = con.execute("DELETE FROM journal_entry WHERE bank_id=?", (item_id,)).rowcount
        c = con.execute("DELETE FROM approval WHERE subject_id=?", (item_id,)).rowcount
        con.commit()
    finally:
        con.close()
    return {"item_id": item_id, "removed": {"links": a, "journal_entries": b, "approvals": c}}
