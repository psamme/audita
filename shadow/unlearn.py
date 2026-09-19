"""Nothing is learned without a receipt, so anything can be unlearned.

The playbook is a derived view: the induced version plus an ordered list of stored patches, each caused by one
correction, interview answer or band answer. Retracting an input drops its patch (or its precedent), replays the
rest in order with no model call, recomputes the bands, and then checks the blast radius: every past resolution
that leaned on what was retracted is re-run through the new playbook, and only those whose outcome changes re-open.
"""
import json

from grade import same
from shadow import correct, db, pipeline, playbook as pbmod


def _patches(client: str, track: str) -> tuple[dict, list[dict]]:
    vs = pbmod.versions(client, track)
    base_v = max(v for v in vs if pbmod.load(client, track, v)["cause"].get("type") == "induction")
    causes = [pbmod.load(client, track, v)["cause"] for v in vs if v > base_v]
    retracted = {c["retracted"] for c in causes if c.get("type") == "retraction"}      # stays dropped in every later replay
    return pbmod.load(client, track, base_v), [c for c in causes if c.get("patch") and c.get("correction_id") not in retracted]


def _replay(client: str, base: dict, patches: list[dict], dropped: str | None) -> tuple[dict, list[str]]:
    pb = json.loads(json.dumps(base))
    notes = []
    for cause in patches:
        if dropped and cause.get("correction_id") == dropped:
            continue
        patch = cause["patch"]
        if patch["kind"] == "band":
            if pbmod.apply_band_answer(pb, patch["rule_id"], patch["condition"], patch["value"], patch["review"],
                                      patch.get("limit"), patch.get("not_amount", False)) is None:
                notes.append(f"{cause.get('correction_id')}: band answer no longer has a rule to apply to; skipped")
            continue
        ids = {r["id"] for r in pb["rules"]}
        clean = [op for op in patch["ops"] if op["op"] == "add" or op.get("rule_id") in ids]
        for op in patch["ops"]:
            if op not in clean:     # the rule this patch edited is gone; ask instead of guessing
                notes.append(f"{cause.get('correction_id')}: could not re-apply '{op['op']}' to {op.get('rule_id')}")
        pb = correct._apply_ops(pb, clean, client, patch["origin"])
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


def retract(client: str, track: str, correction_id: str | None = None, precedent_id: str | None = None, note: str = "") -> dict:
    """Undo one input. Only rules that input touched may change; every other rule stays byte-identical."""
    assert correction_id or precedent_id
    con = db.connect(client, readonly=True)
    current = pbmod.load(client, track)
    base, patches = _patches(client, track)
    if correction_id and not any(c.get("correction_id") == correction_id for c in patches):
        raise ValueError(f"{correction_id} is not an input of the current playbook (held answers and earlier retractions cannot be retracted)")
    replayed, notes = _replay(client, base, patches, correction_id)
    if precedent_id:
        replayed["excluded_precedents"] = sorted(set(current.get("excluded_precedents") or []) | {precedent_id})
        pbmod.backtest(con, replayed)
        touched = {r["id"] for r in current["rules"] if precedent_id in (r.get("precedent_ids") or [])
                   or any(precedent_id in (b.get("lo_precedent"), b.get("hi_precedent")) for b in (r.get("bands") or {}).values())}
    else:
        touched = _touched_by(client, track, correction_id)
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
    """Re-run, at the $0 tiers, every past resolution that cited a changed rule or the retracted precedent.

    Looks at every run on the track, including the partial re-runs made right after an answer or a correction
    ("<run>__after_<id>"), and keeps the newest resolution of each item: that is the one currently standing.
    An item re-opens when the playbook, without the retracted input, no longer resolves it the same way."""
    standing: dict[tuple, dict] = {}
    metas = sorted((json.loads(p.read_text()) for p in db.RUNS.glob("*/run.json")), key=lambda m: (m["created_at"], m["run_id"]))   # "<run>__after_<id>" sorts after its base run within the same second
    for meta in metas:
        if meta["client"] != client or meta.get("track") != track or meta["run_id"].startswith("blast_"):
            continue
        for line in (db.RUNS / meta["run_id"] / "resolutions.jsonl").read_text().splitlines():
            it = json.loads(line)
            standing[(meta["period"], it["item_id"])] = it | {"run_id": meta["run_id"]}
    touched = {k: it for k, it in standing.items() if it["resolution"]["action"] != "escalate" and
               (it["resolution"].get("rule_id") in rule_ids or precedent_id in (it["resolution"].get("precedent_ids") or []))}
    checked, reopened = 0, []
    for period in sorted({p for p, _ in touched}):
        ids = {i for p, i in touched if p == period}
        rid = f"blast_{client}_{track}_{period}_v{version}"
        pipeline.run(client, period, "corrected", track, version=version, use_llm=False, only=ids, run_id=rid,
                     label="blast radius check after a retraction")
        after = {json.loads(l)["item_id"]: json.loads(l) for l in (db.RUNS / rid / "resolutions.jsonl").read_text().splitlines()}
        for item_id in sorted(ids):
            before, now = touched[(period, item_id)], after.get(item_id)
            checked += 1
            if not now or not same(now["resolution"], before["resolution"]):
                reopened.append({"run_id": before["run_id"], "item_id": item_id, "reason": "rule_retracted", "record": before["record"],
                                 "before": {k: before["resolution"].get(k) for k in ("action", "ledger_ids", "adjustments", "rule_id")},
                                 "after": {k: (now or {}).get("resolution", {}).get(k) for k in ("action", "reason", "rule_id", "rationale")}})
    path = db.DATA / client / ("reopened.jsonl" if track == "main" else f"reopened_{track}.jsonl")
    with path.open("a") as f:
        for r in reopened:
            f.write(json.dumps(r, default=str) + "\n")
    return {"resolutions_checked": checked, "reopened": reopened}
