"""The headline experiment on the hidden month. Prints aggregates only; never prints item-level detail.

    uv run python experiments.py            # both clients, all conditions
    uv run python experiments.py --client A --skip zero_shot

1. zero_shot   frontier model with tools, no playbook, no history                      (full month)
2. playbook    playbook induced from months 1-3, only back-test-trusted rules active   (full month)
3. corrected   after human input: the controller answers the playbook's open questions, then a reviewer works
               the escalation queue for April 1-15. Scored on April 16-30 only, which the humans never touched.
All three are also scored on April 16-30 so the comparison is like for like.
"""
import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from grade import grade
from shadow import db, llm, pipeline, playbook
from sim import reviewer

PERIOD, SPLIT_TO, SPLIT_FROM = "2026-04", "2026-04-15", "2026-04-16"


def score(run_id: str, key: Path, second_half: bool) -> dict:
    res = grade(db.RUNS / run_id, key, SPLIT_FROM if second_half else None, None)
    if not second_half:
        (db.RUNS / run_id / "metrics.json").write_text(json.dumps(res["metrics"], indent=1))
        (db.RUNS / run_id / "grades.json").write_text(json.dumps(res["items"]))
    m = res["metrics"]
    m.pop("by_category", None)     # category names would describe the hidden traps
    return m


def one_client(client: str, skip: set[str], label: str, workers: int) -> dict:
    key = db.ROOT / "keys" / f"{client}_{PERIOD}.json"
    out, human_cost = {"second_half": {}, "full_month": {}}, llm.Usage()
    shutil.rmtree(playbook.pb_dir(client, "main"), ignore_errors=True)
    (db.DATA / client / "corrections.jsonl").unlink(missing_ok=True)
    u = llm.Usage()
    pb = playbook.induce(client, PERIOD, "main", usage=u)
    out["induction"] = {"rules": len(pb["rules"]), "approved": sum(r["status"] == "approved" for r in pb["rules"]),
                        "open_questions": sum(bool(r.get("open_question")) for r in pb["rules"]), "cost_usd": u.as_dict()["cost_usd"]}

    if "zero_shot" not in skip:
        rid = f"{client}_{PERIOD}_zero_shot"
        pipeline.run(client, PERIOD, "zero_shot", run_id=rid, workers=workers, label=label)
        out["full_month"]["zero_shot"], out["second_half"]["zero_shot"] = score(rid, key, False), score(rid, key, True)

    rid = f"{client}_{PERIOD}_playbook"
    pipeline.run(client, PERIOD, "playbook", "main", version=1, run_id=rid, workers=workers, label=label)
    out["full_month"]["playbook"], out["second_half"]["playbook"] = score(rid, key, False), score(rid, key, True)

    answers = reviewer.band_interview(client, "main", usage=human_cost) + reviewer.interview(client, "main", usage=human_cost)
    rid_half = f"{client}_{PERIOD}_signed_off_first_half"
    pipeline.run(client, PERIOD, "corrected", "main", run_id=rid_half, workers=workers, date_to=SPLIT_TO, label=label)
    fixes = reviewer.review_queue(client, "main", rid_half, key, SPLIT_TO, usage=human_cost)
    rid = f"{client}_{PERIOD}_corrected"
    pipeline.run(client, PERIOD, "corrected", "main", run_id=rid, workers=workers, date_from=SPLIT_FROM, label=label)
    out["second_half"]["corrected"] = score(rid, key, True)
    res = grade(db.RUNS / rid, key, SPLIT_FROM, None)
    (db.RUNS / rid / "metrics.json").write_text(json.dumps(res["metrics"] | {"by_category": None}, indent=1))
    (db.RUNS / rid / "grades.json").write_text(json.dumps(res["items"]))
    out["human_input"] = {"questions_answered": len(answers), "queue_corrections": len(fixes),
                          "corrections_that_changed_the_playbook": sum(bool(f["diff"]) for f in fixes),
                          "playbook_versions": playbook.versions(client, "main")[-1],
                          "learning_cost_usd": human_cost.as_dict()["cost_usd"]}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", choices=["A", "B"])
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--label", default="interim", help="'interim' for the agent-built validation set, 'blind' for the teammate's set")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    clients = [a.client] if a.client else ["A", "B"]
    with ThreadPoolExecutor(2) as ex:
        results = dict(zip(clients, ex.map(lambda c: one_client(c, set(a.skip), a.label, a.workers), clients)))
    path = db.RUNS / "results.json"
    merged = json.loads(path.read_text()) if path.exists() else {"period": PERIOD, "clients": {}, "full_month": {}, "detail": {}}
    for c, r in results.items():
        merged["clients"].setdefault(c, {}).update({k: v | {"scope": "second_half"} for k, v in r["second_half"].items()})
        merged["full_month"].setdefault(c, {}).update(r["full_month"])
        merged["detail"][c] = {"induction": r["induction"], "human_input": r["human_input"]}
    merged |= {"test_set": a.label, "backend": llm.backend(), "model": llm.MODEL, "effort": llm.EFFORT,
               "split": {"corrections_from": f"{PERIOD}-01..{SPLIT_TO}", "scored_on": f"{SPLIT_FROM}..end"}}
    path.write_text(json.dumps(merged, indent=1))
    print(json.dumps(merged, indent=1))
