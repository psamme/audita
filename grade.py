"""Standalone grader. Takes a run's resolutions and an answer key, prints and writes the metrics.

    uv run python grade.py runs/<run_id> --key keys/A_2026-04.json [--from 2026-04-16] [--to 2026-04-30]

Agent code never reads a key. Accuracy is over key items; key items the run did not resolve count as incorrect
(never as a wrong match). Cost, model calls and escalations are taken from ALL of the run's resolutions, including
items the key does not cover (reported as `unkeyed`), because a human would still have to review those.
Amounts are compared to the cent (tolerance 0.011 to absorb float rounding).
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

MATCHY = {"match", "match_adjust"}


def by_account(adjustments) -> dict:
    out = defaultdict(float)
    for a in adjustments or []:
        out[a["account"]] += a["amount"]
    return {k: round(v, 2) for k, v in out.items() if abs(v) >= 0.005}


def same(pred: dict, key: dict) -> bool:
    if pred["action"] != key["action"]:
        return False
    if key["action"] in MATCHY and sorted(pred.get("ledger_ids") or []) != sorted(key.get("ledger_ids") or []):
        return False
    if key["action"] == "match" and by_account(pred.get("adjustments")):
        return False        # the right entries plus an adjustment nobody asked for is not a clean match
    if key["action"] in {"match_adjust", "book"}:
        p, k = by_account(pred.get("adjustments")), by_account(key.get("adjustments"))
        if p.keys() != k.keys() or any(abs(p[a] - k[a]) > 0.011 for a in k):
            return False
    return True


def grade_item(pred: dict | None, key: dict) -> dict:
    accepted = [key] + list(key.get("alternatives") or [])
    if pred is None:
        return {"correct": False, "wrong_match": False, "wrong_auto": False, "missing": True, "key_action": key["action"],
                "should_escalate": any(k["action"] == "escalate" for k in accepted)}
    correct = any(same(pred, k) for k in accepted)
    ledger_ok = any(sorted(pred.get("ledger_ids") or []) == sorted(k.get("ledger_ids") or [])
                    for k in accepted if k["action"] in MATCHY)
    should_escalate = any(k["action"] == "escalate" for k in accepted)
    return {"correct": correct, "should_escalate": should_escalate,
            "wrong_match": pred["action"] in MATCHY and not correct and not ledger_ok,
            "wrong_auto": pred["action"] != "escalate" and not correct,
            "missing": False, "key_action": key["action"],
            "routed": pred["action"] == "escalate" and key["action"] == "escalate"
                      and pred.get("escalate_to") == key.get("escalate_to")}


def ratio(a, b):
    return round(a / b, 4) if b else None


def summarize(rows: list[dict], unkeyed: list[dict] = ()) -> dict:
    n = len(rows)
    esc_pred = [r for r in rows if r["pred_action"] == "escalate"]
    esc_key = [r for r in rows if r["should_escalate"]]
    matched = [r for r in rows if r["pred_action"] in MATCHY]
    auto = [r for r in rows if r["pred_action"] not in (None, "escalate")]
    exc = [r for r in rows if not r["easy"]]
    out = {
        "n_items": n,
        "accuracy": ratio(sum(r["correct"] for r in rows), n),
        "n_exceptions": len(exc),
        "accuracy_exceptions": ratio(sum(r["correct"] for r in exc), len(exc)),
        "escalated": len(esc_pred),
        "wrong_match_of_matched": ratio(sum(r["wrong_match"] for r in matched), len(matched)),
        "matches_made": len(matched),
        "escalation_precision": ratio(sum(r["should_escalate"] for r in esc_pred), len(esc_pred)),
        "escalation_recall": ratio(sum(r["pred_action"] == "escalate" for r in esc_key), len(esc_key)),
        "routing_accuracy": ratio(sum(r.get("routed", False) for r in esc_pred), sum(r["correct"] for r in esc_pred)),
        "wrong_match_rate": ratio(sum(r["wrong_match"] for r in rows), n),
        "wrong_auto_rate": ratio(sum(r["wrong_auto"] for r in rows), n),
        "wrong_auto_of_auto": ratio(sum(r["wrong_auto"] for r in auto), len(auto)),
        "missing": sum(r["missing"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in list(rows) + list(unkeyed)), 4),
        "llm_calls": sum(r["llm_calls"] for r in list(rows) + list(unkeyed)),
        "unkeyed": len(unkeyed),
        "unkeyed_escalated": sum(r["pred_action"] == "escalate" for r in unkeyed),
        "review_load": len(esc_pred) + sum(r["pred_action"] == "escalate" for r in unkeyed),
    }
    for tier, name in (("matcher", "share_matcher"), ("rule", "share_rule"), ("guardrail", "share_guardrail"),
                       ("investigator", "share_investigator")):
        out[name] = ratio(sum(r["tier"] == tier for r in rows), n)
    return out


def grade(run_dir: Path, key_path: Path, date_from: str | None = None, date_to: str | None = None) -> dict:
    key = json.loads(key_path.read_text())
    items = {}
    for line in (run_dir / "resolutions.jsonl").read_text().splitlines():
        it = json.loads(line)
        items[it["item_id"]] = it
    rows, per_item = [], {}
    for item_id, k in key.items():
        it = items.get(item_id)
        when = k.get("date") or (it or {}).get("record", {}).get("date")
        if when and ((date_from and when < date_from) or (date_to and when > date_to)):
            continue
        if it is None and (date_from or date_to) and not when:
            continue
        g = grade_item(it["resolution"] if it else None, k)
        per_item[item_id] = g
        usage = (it or {}).get("usage") or {}
        rows.append(g | {"item_id": item_id, "pred_action": it["resolution"]["action"] if it else None,
                         "tier": (it or {}).get("tier"), "easy": k.get("easy", False),
                         "source": k.get("source", "standard"), "category": k.get("category", ""),
                         "cost_usd": usage.get("cost_usd", 0.0), "llm_calls": usage.get("llm_calls", 0)})
    unkeyed = []
    for item_id, it in items.items():
        when = it["record"]["date"]
        if item_id in key or (date_from and when < date_from) or (date_to and when > date_to):
            continue
        unkeyed.append({"pred_action": it["resolution"]["action"], "cost_usd": it["usage"].get("cost_usd", 0.0), "llm_calls": it["usage"].get("llm_calls", 0)})
    out = summarize(rows, unkeyed)
    out["scope"] = "full_month" if not (date_from or date_to) else f"{date_from or ''}..{date_to or ''}"
    out["by_source"] = {s: {"n": len(rs), "accuracy": ratio(sum(r["correct"] for r in rs), len(rs))}
                        for s in sorted({r["source"] for r in rows}) for rs in [[r for r in rows if r["source"] == s]]}
    cats = defaultdict(list)
    for r in rows:
        cats[r["category"]].append(r)
    out["by_category"] = {c: {"n": len(rs), "correct": sum(r["correct"] for r in rs),
                              "wrong_auto": sum(r["wrong_auto"] for r in rs)} for c, rs in sorted(cats.items())}
    return {"metrics": out, "items": per_item}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--quiet", action="store_true", help="hide per-category rows (use on the hidden test)")
    a = ap.parse_args()
    result = grade(a.run_dir, a.key, a.date_from, a.date_to)
    suffix = "" if not (a.date_from or a.date_to) else f"_{a.date_from or ''}_{a.date_to or ''}"
    (a.run_dir / f"metrics{suffix}.json").write_text(json.dumps(result["metrics"], indent=1))
    (a.run_dir / f"grades{suffix}.json").write_text(json.dumps(result["items"]))
    shown = dict(result["metrics"])
    if a.quiet:
        shown.pop("by_category")
    print(json.dumps(shown, indent=1))
