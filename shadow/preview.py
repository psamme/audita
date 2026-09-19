"""Read-only policy previews over a fixed period. No models and no answer keys."""
import copy
import hashlib
import json
import secrets
import time

from grade import same
from shadow import authority, correct, db, pipeline, playbook, state

PREVIEWS = {}
TTL_SECONDS = 900
TABLES = ("client", "user", "bank_line", "ledger_entry", "invoice", "document", "reconcile_link", "journal_entry", "approval")


class StalePreview(ValueError):
    pass


def snapshot(con, pb):
    data = {table: db.q(con, f"SELECT * FROM {table} ORDER BY id") for table in TABLES}
    data["playbook"] = pb
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def summarize(items):
    bank = [it for it in items if it["item_kind"] == "bank"]
    exceptions = [it for it in bank if it["tier"] != "matcher"]
    auto = lambda it: it["resolution"]["action"] in {"match", "match_adjust", "book"}
    return {"bank_items": len(bank), "bank_exceptions": len(exceptions),
            "automatic_exceptions": sum(auto(it) for it in exceptions),
            "needs_review": sum(it["resolution"]["action"] == "escalate" for it in bank),
            "automatic_bank_items": sum(auto(it) for it in bank)}


@state.serialized
def build(client, track, period, rule_id, condition, role, value=None, review=None, limit=None, not_amount=False):
    from datetime import date
    date.fromisoformat(period + "-01")
    old = playbook.load(client, track)
    if not old:
        raise ValueError("No playbook on this track")
    con = db.connect(client, readonly=True)
    try:
        con.execute("BEGIN")
        authority.require_senior(con, role)
        new = copy.deepcopy(old)
        change = playbook.apply_band_answer(new, rule_id, condition, value, review, limit, not_amount)
        if change is None:
            raise ValueError("No such rule or band")
        if change["held"]:
            return {"held": change["held"], "preview_id": None, "diff": None}
        args = dict(client=client, period=period, condition="corrected", track=track, use_llm=False,
                    persist=False, con_override=con)
        before = pipeline.run(**args, playbook_override=old)
        after = pipeline.run(**args, playbook_override=new)
        claimed_before = {lid: it["item_id"] for it in before["items"] for lid in it["resolution"].get("ledger_ids", [])}
        claimed_after = {lid: it["item_id"] for it in after["items"] for lid in it["resolution"].get("ledger_ids", [])}
        prior = {it["item_id"]: it for it in before["items"]}
        later = {it["item_id"]: it for it in after["items"]}
        changes = []
        for iid in sorted(prior.keys() | later.keys()):
            a, b = prior.get(iid), later.get(iid)
            if a and b and same(a["resolution"], b["resolution"]) and a["resolution"].get("escalate_to") == b["resolution"].get("escalate_to"):
                continue
            it = b or a
            changes.append({"item_id": iid, "item_kind": it["item_kind"], "record": it["record"],
                            "before": a["resolution"] if a else None, "after": b["resolution"] if b else None,
                            "before_claimed_by": claimed_before.get(iid), "after_claimed_by": claimed_after.get(iid)})
        token = secrets.token_urlsafe(24)
        for key in list(PREVIEWS):
            if PREVIEWS[key]["expires"] < time.time():
                del PREVIEWS[key]
        if len(PREVIEWS) >= 100:
            del PREVIEWS[next(iter(PREVIEWS))]
        PREVIEWS[token] = {"expires": time.time() + TTL_SECONDS, "fingerprint": snapshot(con, old),
                           "period": period, "resolved_ids": [x["item_id"] for x in changes if x["before"] and x["after"] and x["before"]["action"] == "escalate" and x["after"]["action"] != "escalate"], "args": dict(client=client, track=track, rule_id=rule_id, condition=condition,
                                        value=value, review=review, limit=limit, not_amount=not_amount, role=role)}
        return {"preview_id": token, "client": client, "track": track, "period": period, "version": old["version"],
                "expires_in_seconds": TTL_SECONDS, "before": summarize(before["items"]), "after": summarize(after["items"]),
                "changed_items": changes, "diff": playbook.diff(old, new), "llm_calls": 0,
                "scope_note": "All bank items and open ledger items in this period. Unresolved cases remain for review; no model is run.",
                "accuracy_note": "This measures changed decisions, not correctness. No answer key is used."}
    finally:
        con.rollback()
        con.close()


@state.serialized
def commit(preview_id, role):
    entry = PREVIEWS.get(preview_id)
    if not entry or entry["expires"] < time.time():
        raise StalePreview("Preview expired or was already applied. Preview again.")
    args = entry["args"]
    if role != args["role"]:
        raise PermissionError("Apply the preview as the role that reviewed it")
    con = db.connect(args["client"], readonly=True)
    try:
        con.execute("BEGIN")
        authority.require_senior(con, role)
        if snapshot(con, playbook.load(args["client"], args["track"])) != entry["fingerprint"]:
            raise StalePreview("The policy or supporting data changed. Preview again.")
        out = correct.answer_band(**args)
        del PREVIEWS[preview_id]
        if out.get("diff"):
            run_id = f"preview_{args['client']}_{args['track']}_v{out['new_version']}"
            out["reconciliation"] = pipeline.run(args["client"], entry["period"], "corrected", args["track"],
                                                  use_llm=False, run_id=run_id, con_override=con,
                                                  label="Policy preview applied. Deterministic rehearsal, not an accuracy evaluation.")
            out["reran"] = [json.loads(line) for line in (db.RUNS / run_id / "resolutions.jsonl").read_text().splitlines()
                            if json.loads(line)["item_id"] in entry["resolved_ids"]]
        return out
    finally:
        con.rollback()
        con.close()
