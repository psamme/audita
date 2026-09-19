"""Nothing is learned without a receipt, so anything can be unlearned.

The playbook is a derived view: the induced version plus an ordered list of stored patches, each caused by one
correction, interview answer or band answer. Retracting an input drops its patch (or its precedent), replays the
rest in order with no model call, recomputes the bands, and then checks the blast radius: every past resolution
that leaned on what was retracted is re-run through the new playbook, and only those whose outcome changes re-open.
"""
import json

from grade import same
from shadow import state, authority, correct, db, pipeline, playbook as pbmod


def _patches(client: str, track: str) -> tuple[dict, list[dict]]:
    vs = pbmod.versions(client, track)
    base_v = max((v for v in vs if pbmod.load(client, track, v)["cause"].get("type") == "induction"), default=0)
    causes = [pbmod.load(client, track, v)["cause"] for v in vs if v > base_v]
    retracted = {c["retracted"] for c in causes if c.get("type") == "retraction"}      # stays dropped in every later replay
    base = pbmod.load(client, track, base_v) if base_v else {"version": 0, "rules": [], "trained_before": pbmod.load(client, track)["trained_before"]}
    return base, [c for c in causes if c.get("patch") and c.get("correction_id") not in retracted]


def _replay(client: str, base: dict, patches: list[dict], dropped: str | None) -> tuple[dict, list[str]]:
    pb = json.loads(json.dumps(base))
    notes = []
    for cause in patches:
        if dropped and cause.get("correction_id") == dropped:
            continue
        patch = cause["patch"]
        if patch["kind"] == "band":
            try:
                result = pbmod.apply_band_answer(pb, patch["rule_id"], patch["condition"], patch.get("value"), patch.get("review"),
                                                patch.get("limit"), patch.get("not_amount", False))
            except ValueError as exc:
                result = None
                notes.append(f"{cause.get('correction_id')}: dependent band answer needs review: {exc}")
            if result is None or result.get("held"):
                notes.append(f"{cause.get('correction_id')}: band answer not re-applied; review required")
            continue
        ids = {r["id"] for r in pb["rules"]}
        clean = [op for op in patch["ops"] if op["op"] == "add" or op.get("rule_id") in ids]
        for op in patch["ops"]:
            if op not in clean:     # the rule this patch edited is gone; ask instead of guessing
                notes.append(f"{cause.get('correction_id')}: could not re-apply '{op['op']}' to {op.get('rule_id')}")
        pb = correct._apply_ops(pb, clean, client, patch["origin"])
        touched = {op.get("assigned_id") or op.get("rule_id") for op in clean}
        for rule in pb["rules"]:
            if rule["id"] not in touched or rule.get("status") == "retired":
                continue
            safety = patch.get("safety", {}).get(rule["id"])
            if safety is not None:
                for key in authority.SAFETY_FIELDS:
                    if safety.get(key) is None:
                        rule.pop(key, None)
                    else:
                        rule[key] = safety[key]
            else:
                # Legacy patches did not store authority or effective dates. Never
                # promote an unverified old patch while undoing a different input.
                rule.update(status="proposed", human_confirmed=False, awaiting_senior=True,
                            open_question="This replayed legacy correction needs senior confirmation.")
                notes.append(f"{cause.get('correction_id')}: legacy approval not replayed")
    return pb, notes


def _touched_by(client: str, track: str, correction_id: str) -> set[str]:
    """Rules the retracted input changed when it was applied (from the diff of the version it produced)."""
    vs = pbmod.versions(client, track)
    for v in vs:
        pb = pbmod.load(client, track, v)
        if pb["cause"].get("correction_id") == correction_id and pb["cause"].get("type") != "retraction" and v - 1 in vs:
            d = pbmod.diff(pbmod.load(client, track, v - 1), pb)
            return {r["id"] for r in d["added"] + d["removed"]} | {c["after"]["id"] for c in d["changed"]}
    return set()


@state.serialized
def retract(client: str, track: str, correction_id: str | None = None, precedent_id: str | None = None, note: str = "") -> dict:
    """Undo one input and replay dependent changes without restoring withdrawn evidence."""
    if bool(correction_id) == bool(precedent_id):
        raise ValueError("Choose exactly one correction or precedent")
    con = db.connect(client, readonly=True)
    current = pbmod.load(client, track)
    target = correction_id or precedent_id
    previous = [pbmod.load(client, track, v)["cause"] for v in pbmod.versions(client, track)]
    if any(c.get("type") == "retraction" and c.get("retracted") == target for c in previous):
        return {"retracted": target, "new_version": current["version"], "already_retracted": True,
                "diff": pbmod.diff(current, current), "rules_changed": [], "replay_notes": [],
                "resolutions_checked": 0, "reopened": []}
    base, patches = _patches(client, track)
    if correction_id and not any(c.get("correction_id") == correction_id for c in patches):
        raise ValueError(f"{correction_id} is not an input of the current playbook (held answers and earlier retractions cannot be retracted)")
    exclusions = set(current.get("excluded_precedents") or []) | ({precedent_id} if precedent_id else set())
    if exclusions:
        base["excluded_precedents"] = sorted(exclusions)
        pbmod.backtest(con, base)
    replayed, notes = _replay(client, base, patches, correction_id)
    if precedent_id:
        touched = {r["id"] for r in current["rules"] + replayed["rules"]}
    else:
        touched = _touched_by(client, track, correction_id)
        # A dependent patch may stop applying when its originating rule is removed.
        # Include the resulting semantic differences, not just direct citations.
        delta = pbmod.diff(current, replayed)
        touched |= {r["id"] for r in delta["added"] + delta["removed"]}
        touched |= {x["after"]["id"] for x in delta["changed"]}
    by_id = {r["id"]: r for r in replayed["rules"]}
    rules_out = []
    for r in current["rules"]:
        if r["id"] not in touched:
            rules_out.append(r)                      # untouched: carried over exactly as it is
        elif r["id"] in by_id:
            rules_out.append(by_id[r["id"]])         # touched: as it would have been without that input
    pb = {k: v for k, v in current.items() if k not in ("version", "created_at", "cause", "rules")} | {"rules": rules_out}
    if precedent_id:
        pb["excluded_precedents"] = replayed["excluded_precedents"]
        pb["findings"] = pbmod.findings(con, replayed)
    entry = correct._log(client, {"type": "retraction", "retracted": correction_id or precedent_id, "note": note}, track)
    cause = {"type": "retraction", "correction_id": entry["correction_id"], "retracted": correction_id or precedent_id,
             "note": note or f"Retracted {correction_id or precedent_id}", "replay_notes": notes}
    saved = pbmod.save(client, track, pb, cause)
    d = pbmod.diff(current, saved)
    changed = {r["id"] for r in d["added"] + d["removed"]} | {c["after"]["id"] for c in d["changed"]}
    radius = blast_radius(client, track, changed, precedent_id, saved["version"])
    return {"retracted": correction_id or precedent_id, "new_version": saved["version"], "diff": d, "rules_changed": sorted(changed),
            "replay_notes": notes} | radius


def blast_radius(client: str, track: str, rule_ids: set[str], precedent_id: str | None, version: int) -> dict:
    """Re-run complete periods at $0 to check direct and indirect dependencies."""
    checked, reopened = 0, []
    for run_json in sorted(db.RUNS.glob("*/run.json")):
        meta = json.loads(run_json.read_text())
        if meta["client"] != client or meta.get("track") != track or "__" in meta["run_id"] or meta["run_id"].startswith("blast_"):
            continue
        items = [json.loads(l) for l in (run_json.parent / "resolutions.jsonl").read_text().splitlines()]
        touched = {it["item_id"]: it for it in items if it["resolution"]["action"] != "escalate" and (rule_ids or precedent_id)}
        if not touched:
            continue
        rid = f"blast_{meta['run_id']}"
        pipeline.run(client, meta["period"], "corrected", track, version=version, use_llm=False, run_id=rid,
                     label="blast radius check after a retraction")
        after = {json.loads(l)["item_id"]: json.loads(l) for l in (db.RUNS / rid / "resolutions.jsonl").read_text().splitlines()}
        for item_id, before in touched.items():
            checked += 1
            now = after.get(item_id)
            if not now or not same(now["resolution"], before["resolution"]):
                reopened.append({"run_id": meta["run_id"], "item_id": item_id, "reason": "rule_retracted", "record": before["record"],
                                 "before": {k: before["resolution"].get(k) for k in ("action", "ledger_ids", "adjustments", "rule_id")},
                                 "after": {k: (now or {}).get("resolution", {}).get(k) for k in ("action", "reason", "rule_id", "rationale")}})
    path = db.DATA / client / ("reopened.jsonl" if track == "main" else f"reopened_{track}.jsonl")
    with path.open("a") as f:
        for r in reopened:
            f.write(json.dumps(r, default=str) + "\n")
    return {"resolutions_checked": checked, "reopened": reopened}
