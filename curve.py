"""Questions to trust: how many human answers until the $0 tiers clear X% of exceptions with zero wrong matches.

Runs on the March development holdout only (playbook induced from January and February), so it never touches the
hidden month. Answers are given in the order a real onboarding would produce them: yes/no band questions (widest
band first), the playbook's open questions, then corrections from the review queue of March 1-15. After every
answer the matcher and the compiled rules (no model calls) re-run March 16-31 and the result is logged.

    uv run python curve.py [--client A]
"""
import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from grade import grade
from shadow import db, llm, pipeline, playbook
from sim import reviewer

PERIOD, HALF_TO, HALF_FROM, TRACK = "2026-03", "2026-03-15", "2026-03-16", "curve"


def measure(client: str, key: Path, k: int, kind: str, what: str, per_item_cost: float) -> dict:
    rid = f"curve_{client}_{k:02d}"
    s = pipeline.run(client, PERIOD, "corrected", TRACK, use_llm=False, run_id=rid, date_from=HALF_FROM, label="questions-to-trust curve")
    m = grade(db.RUNS / rid, key, HALF_FROM, None)["metrics"]
    exceptions = m["n_exceptions"]
    left = s["tiers"]["investigator"] + s["tiers"]["guardrail"]
    in_band = s["escalation_reasons"]["in_band"]
    shutil.rmtree(db.RUNS / rid)
    auto_ok = round(m["accuracy_exceptions"] * exceptions) - 0   # exceptions the $0 tiers resolved correctly
    return {"k": k, "answer_kind": kind, "answer": what, "playbook_version": s["playbook_version"],
            "auto_resolve_rate": round(auto_ok / exceptions, 4) if exceptions else None,
            "wrong_matches": round((m["wrong_match_rate"] or 0) * m["n_items"]), "wrong_auto": round((m["wrong_auto_rate"] or 0) * m["n_items"]),
            "left_for_model_or_human": left, "in_band_escalations": in_band, "tiers": s["tiers"],
            "est_llm_cost_usd": round(left * per_item_cost, 2)}


def one(client: str, target: float) -> dict:
    key = db.RUNS / "dev" / f"key_{client}_{PERIOD}.json"
    shutil.rmtree(playbook.pb_dir(client, TRACK), ignore_errors=True)
    usage = llm.Usage()
    playbook.induce(client, PERIOD, TRACK, usage=usage)
    ref = db.RUNS / f"dev_{client}_playbook" / "run.json"     # measured cost per investigated item on this client
    per_item = 0.09
    if ref.exists():
        r = json.loads(ref.read_text())
        per_item = r["cost_usd"] / max(1, r["tiers"]["investigator"] + r["tiers"]["guardrail"])
    points = [measure(client, key, 0, "induction", "playbook as induced, nothing asked yet", per_item)]
    log = lambda kind, q, a: points.append(measure(client, key, len(points), kind, f"{q} -> {a}"[:400], per_item))
    reviewer.band_interview(client, TRACK, limit=8, usage=usage, on_answer=log)
    reviewer.interview(client, TRACK, limit=10, usage=usage, on_answer=log)
    rid = f"curve_{client}_queue"
    pipeline.run(client, PERIOD, "corrected", TRACK, use_llm=False, run_id=rid, date_to=HALF_TO)
    reviewer.review_queue(client, TRACK, rid, key, HALF_TO, limit=12, usage=usage, on_answer=log)
    shutil.rmtree(db.RUNS / rid, ignore_errors=True)
    clean = [p for p in points if p["wrong_matches"] == 0 and (p["auto_resolve_rate"] or 0) >= target]
    return {"client": client, "target_auto_resolve_rate": target, "questions_to_trust": clean[0]["k"] if clean else None,
            "scored_on": f"{HALF_FROM}..end of {PERIOD} (development holdout)", "corrections_from": f"{PERIOD}-01..{HALF_TO}",
            "cost_note": "model cost is estimated: items left after the $0 tiers times the measured cost per investigated item",
            "per_item_cost_usd": round(per_item, 4), "learning_cost_usd": usage.as_dict()["cost_usd"], "points": points}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", choices=["A", "B"])
    ap.add_argument("--target", type=float, default=0.9)
    a = ap.parse_args()
    clients = [a.client] if a.client else ["A", "B"]
    with ThreadPoolExecutor(2) as ex:
        out = dict(zip(clients, ex.map(lambda c: one(c, a.target), clients)))
    path = db.RUNS / "questions_to_trust.json"
    merged = (json.loads(path.read_text()) if path.exists() else {}) | out
    path.write_text(json.dumps(merged, indent=1))
    for c, r in out.items():
        print(c, "questions to trust:", r["questions_to_trust"])
        for p in r["points"]:
            print(f"  k={p['k']:2d} {p['answer_kind']:14s} auto={p['auto_resolve_rate']} wrong={p['wrong_auto']} left={p['left_for_model_or_human']} in_band={p['in_band_escalations']} est=${p['est_llm_cost_usd']}")
