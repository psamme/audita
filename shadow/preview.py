"""Read-only policy previews over a fixed period. No models and no answer keys."""
import copy
import hashlib
import json
import secrets
import time

from grade import same
from shadow import authority, correct, db, pipeline, playbook, rules, state

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


def prepare_policy(con, old, rule_id, condition, limit, role, effective_from=None):
    """Explicit sign-off of a displayed learned rule and its numeric boundary; no model rewrite."""
    new = copy.deepcopy(old)
    rule = next((r for r in new['rules'] if r['id'] == rule_id), None)
    if not rule or rule.get('status') == 'retired':
        raise ValueError('No active learned rule')
    if not rule.get('executable') and not rule.get('executable_if_approved'):
        raise ValueError('This rule needs a written clarification before it can execute')
    change = playbook.apply_band_answer(new, rule_id, condition, None, limit=limit)
    if not change or change['held']:
        raise ValueError('This rule has no approvable numeric boundary')
    op = {'op': 'modify', 'rule_id': rule_id, 'text': rule['text'], 'when': rule['when'],
          'then': rule['then'], 'executable': True}
    new = correct._apply_ops(copy.deepcopy(old), [op], old['client'], 'explicit policy sign-off')
    if effective_from:
        from datetime import date
        date.fromisoformat(effective_from)
        if effective_from < old['trained_before'] + '-01':
            raise ValueError('A new policy must start after the training window')
        next(r for r in new['rules'] if r['id'] == rule_id)['valid_from'] = effective_from
    cause = {'type': 'interview', 'rule_id': rule_id, 'by_role': role,
             'note': 'Approved the displayed rule and stated its boundary.' + (f' New policy effective {effective_from}.' if effective_from else ''),
             'patch': {'kind': 'ops', 'ops': [op], 'origin': 'explicit policy sign-off'}}
    correct.validate_patch(con, new, cause)
    selected = next(r for r in new['rules'] if r['id'] == rule_id)
    if selected['status'] != 'approved' or not selected.get('human_confirmed'):
        raise ValueError(selected.get('open_question') or 'Historical replay did not support this approval')
    return new, cause


@state.serialized
def build(client, track, period, rule_id, condition, role, value=None, review=None, limit=None, not_amount=False, approve_rule=False, effective_from=None):
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
        approval_cause = None
        if approve_rule:
            new, approval_cause = prepare_policy(con, old, rule_id, condition, limit, role, effective_from)
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
                           "approval_cause": approval_cause, "approved_playbook": new if approve_rule else None,
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
        if entry.get('approval_cause'):
            cause = copy.deepcopy(entry['approval_cause'])
            event = correct._log(args['client'], {'type': 'interview', 'rule_id': args['rule_id'],
                                  'answer': cause['note'], 'by_role': role}, args['track'])
            cause['correction_id'] = event['correction_id']
            old = playbook.load(args['client'], args['track'])
            saved, diff = correct._finish(con, args['client'], args['track'], old,
                                         copy.deepcopy(entry['approved_playbook']), cause)
            out = {'correction_id': event['correction_id'], 'new_version': saved['version'], 'diff': diff}
        else:
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
