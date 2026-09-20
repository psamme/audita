"""Client C: a real bank's cash desk (BenchRec), run through the same idea as the simulated clients.

    uv run python -m benchrec.real

1. Deterministic matcher, thresholds already tuned on TRAIN (runs/benchrec_results.json).
2. Playbook of conventions induced from the analysts' own resolutions in TRAIN (benchrec/induce.py).
3. One pass over EVAL with everything frozen. Only `grade()` below reads solution.csv.

Writes runs/real_results.json.
"""

import csv
import json
import random
import subprocess
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from . import data
from .induce import FLOOR, analyst_groups, apply, induce, is_right, linked
from .matcher import Blocks, Thresholds, match
from .scoring import score

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "runs" / "real_results.json"
MATCHER_RESULTS = ROOT / "runs" / "benchrec_results.json"
N_EXAMPLES = 36
SEED = 7


def operators(path=data.TRAIN):
    """matchId -> (rule, operator), and the operator profile. The rule sits on one row of each group."""
    rule_of, by_of = {}, {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            by_of[r["matchId"]] = r["matchedBy"]
            if r["matchRule"]:
                rule_of[r["matchId"]] = r["matchRule"]
    return rule_of, by_of


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def operator_profile(ledger, bank, rule_of, by_of):
    groups = defaultdict(lambda: ([], []))
    for a in ledger:
        groups[a.match_id][0].append(a)
    for b in bank:
        groups[b.match_id][1].append(b)
    manual_ops = {by_of[g] for g, r in rule_of.items() if r == "MANUAL"}
    prof = defaultdict(lambda: defaultdict(list))
    for g, (As, Bs) in groups.items():
        p = prof[by_of[g]]
        p["groups"].append(1)
        p["manual"].append(rule_of.get(g) == "MANUAL")
        p["one_to_one"].append(len(As) == 1 and len(Bs) == 1)
        p["no_ledger_side"].append(not As)
        if len(As) == 1 and len(Bs) == 1:
            p["amount_exact"].append(As[0].cents == Bs[0].cents)
            p["same_day"].append(As[0].day == Bs[0].day)
            if As[0].cents != Bs[0].cents:
                p["diff_cents"].append(abs(As[0].cents - Bs[0].cents))
    out = []
    for op, p in sorted(prof.items(), key=lambda kv: -len(kv[1]["groups"])):
        share = lambda k: (sum(p[k]) / len(p[k])) if p[k] else None  # noqa: E731
        out.append({
            "operator": op,
            "kind": "analyst" if op in manual_ops else "matching engine (inferred: never closes anything manually)",
            "groups": len(p["groups"]),
            "share_manual": share("manual"),
            "share_one_to_one": share("one_to_one"),
            "share_groups_with_no_ledger_side": share("no_ledger_side"),
            "one_to_one_groups": len(p["amount_exact"]),
            "one_to_one_with_a_difference": len(p["diff_cents"]),
            "one_to_one_amount_exact": share("amount_exact"),
            "one_to_one_same_value_date": share("same_day"),
            "median_accepted_difference_cents": _q(p["diff_cents"], 0.5),
            "p90_accepted_difference_cents": _q(p["diff_cents"], 0.9),
        })
    analysts = {g for g, op in by_of.items() if op in manual_ops}
    return out, analysts


def grade(matches, cleared, truth):
    """The only place the answer key is used."""
    base = {m.b_id: m.allocations for m in matches}
    s0 = score(base, truth)
    preds = dict(base)
    per = defaultdict(lambda: {"bank_lines": 0, "right": 0})
    no_ledger = {"bank_lines": 0, "right": 0}
    for r in cleared:
        ok = is_right(r, truth)
        bucket = no_ledger if r.verdict == "no_ledger" else per[r.convention]
        bucket["bank_lines"] += len(r.b_ids)
        bucket["right"] += len(r.b_ids) * ok
        if r.verdict == "no_ledger":
            per[r.convention]["bank_lines"] += len(r.b_ids)
            per[r.convention]["right"] += len(r.b_ids) * ok
        else:
            for b in r.b_ids:
                preds[b] = r.allocations
    s1 = score(preds, truth)
    for v in per.values():
        v["precision"] = v["right"] / v["bank_lines"] if v["bank_lines"] else None
    return s0, s1, dict(per), no_ledger


def _line(rec):
    return {"id": rec.id, "side": "ledger" if rec.side == "A" else "bank", "amount": rec.cents / 100,
            "value_date": date.fromordinal(rec.day).isoformat(), "dc": rec.dc,
            "references": rec.refs.strip()[:120], "attributes": " ".join(rec.attrs.split())[:160]}


def examples(cleared, asked, rest, ledger, bank, playbook, truth):
    a_by, b_by = {a.id: a for a in ledger}, {b.id: b for b in bank}
    conv = {c.id: c for c in playbook}
    rng = random.Random(SEED)
    picks = []
    by_conv = defaultdict(list)
    for r in cleared:
        by_conv[("cleared", r.convention)].append(r)
    for r in asked:
        by_conv[("escalated", r.convention)].append(r)
    for r in rest:
        by_conv[("escalated", r.reason)].append(r)
    per = max(3, N_EXAMPLES // max(1, len(by_conv)))
    for key in sorted(by_conv):
        rs = by_conv[key]
        wrong = [r for r in rs if r.verdict != "escalated" and not is_right(r, truth)]
        chosen = rng.sample(rs, min(per, len(rs)))
        if wrong and not any(r in wrong for r in chosen):
            chosen[-1] = rng.choice(wrong)  # always show a miss where there is one
        picks += chosen
    out = []
    a_amounts = defaultdict(list)
    for a in ledger:
        a_amounts[a.cents].append(a)
    for r in picks[: N_EXAMPLES + 12]:
        c = conv.get(r.convention)
        cands = [a_by[i] for i in r.a_ids]
        if not cands and r.verdict == "escalated":
            b0 = b_by[r.b_ids[0]]
            cands = sorted(a_amounts.get(b0.cents, []), key=lambda a: abs(a.day - b0.day))[:3]
        if r.verdict == "escalated":
            right = None
            would = _suggestion_right(r, truth) if r.convention else None
        else:
            right, would = is_right(r, truth), None
        out.append({
            "verdict": r.verdict,
            "convention": r.convention or None,
            "convention_title": c.title if c else None,
            "reason": r.reason,
            "measured": f"{r.param} {c.unit}" if c else None,
            "band": {"applies_to": c.applies_to, "asks_to": c.asks_to, "unit": c.unit} if c else None,
            "precedents": ([c.applies_precedent] + c.precedents[:2]) if c else [],
            "bank_lines": [_line(b_by[i]) for i in r.b_ids],
            "ledger_lines": [_line(a) for a in cands],
            "reference_found_in_bank_text": [linked(a, b_by[r.b_ids[0]]) for a in cands],
            "right_per_scorer": right,
            "suggestion_would_have_been_right": would,
            "bank_line_has_a_ledger_match_in_key": bool(truth.get(r.b_ids[0])),
        })
    return out


def _suggestion_right(r, truth):
    """An escalated suggestion, graded as if it had been applied. An offset suggests 'no ledger side'."""
    if r.convention == "bank_side_offset":
        return all(not truth.get(b) for b in r.b_ids)
    return bool(r.allocations) and all(truth.get(b) and r.allocations <= truth[b] for b in r.b_ids)


def commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def main():
    t0 = time.time()
    th = Thresholds(**json.loads(MATCHER_RESULTS.read_text())["thresholds"])
    train_a, train_b = data.load_records(data.TRAIN)
    rule_of, by_of = operators()
    profile, analysts = operator_profile(train_a, train_b, rule_of, by_of)
    train_matches = match(Blocks(train_a, train_b), th)
    playbook = induce(train_a, train_b, train_matches, analysts)
    print("INDUCED ON TRAIN")
    for c in playbook:
        bt = c.backtest.get("at_applies_to") or c.backtest.get("at_widest") or {}
        print(f"  {c.id:<20} support {c.support:>5}  applies<= {c.applies_to}  asks<= {c.asks_to}  "
              f"executes {c.executes}  backtest {bt.get('agree')}/{bt.get('bank_lines')}")

    # frozen from here on
    eval_a, eval_b = data.load_records(data.EVAL)
    eval_matches = match(Blocks(eval_a, eval_b), th)
    cleared, asked, rest = apply(eval_a, eval_b, eval_matches, playbook)

    truth = data.load_solution()
    s0, s1, per, no_ledger = grade(eval_matches, cleared, truth)
    base = score(data.load_baseline(), truth)
    n = s0["n_bank_lines"]
    n_residue = n - s0["matched"]
    lines_cleared = sum(len(r.b_ids) for r in cleared)
    lines_matched_by_conv = sum(len(r.b_ids) for r in cleared if r.verdict != "no_ledger")
    right_conv = sum(v["right"] for k, v in per.items() if k != "bank_side_offset")
    asked_lines = sum(len(r.b_ids) for r in asked)
    reasons = Counter(r.reason for r in rest)
    sugg_right = sum(len(r.b_ids) for r in asked if _suggestion_right(r, truth))

    def tier(s):
        return {"matched": s["matched"], "correct_pair": s["correct_pair_level"],
                "precision_pair": s["precision_pair_level"], "match_rate_pair": s["match_rate_pair_level"],
                "precision_strict": s["precision"], "match_rate_strict": s["match_rate"],
                "left_for_review": s["left_for_review"], "wrong_pair": s["matched"] - s["correct_pair_level"]}

    days = [b.day for b in eval_b]
    tdays = [b.day for b in train_b]
    results = {
        "client": "Client C: a real bank's cash desk",
        "dataset": "BenchRec cash v1.0 (Kaggle, CC BY 4.0): real, anonymised bank-to-ledger lines labelled by the bank's analysts",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_commit": commit(),
        "protocol": [
            "Matcher thresholds tuned on the train split only.",
            "Conventions induced from the analysts' resolutions in the train split only; bands chosen by back-test on train.",
            "The eval split is the dataset authors' own held-out file. Its value dates overlap the end of the train period, so it is held out by line, not by calendar.",
            "One pass over the eval split with everything frozen. The answer key (solution.csv) is opened only by the grader.",
            f"A convention executes only if it agrees with the analysts on at least {FLOOR:.0%} of the train lines it touches.",
            "Correct means pair level: every ledger allocation we attach belongs to that bank line's labelled match. Strict (exact set) is reported beside it.",
        ],
        "train": {"bank_lines": len(train_b), "ledger_lines": len(train_a),
                  "from": date.fromordinal(min(tdays)).isoformat(), "to": date.fromordinal(max(tdays)).isoformat(),
                  "analyst_closed_groups": len(analysts)},
        "eval": {"bank_lines": n, "matchable": s0["n_matchable"], "ledger_lines": len(eval_a),
                 "from": date.fromordinal(min(days)).isoformat(), "to": date.fromordinal(max(days)).isoformat()},
        "tiers": {
            "shipped_baseline": tier(base),
            "matcher": tier(s0),
            "matcher_plus_conventions": tier(s1),
        },
        "residue": {
            "bank_lines_left_by_matcher": n_residue,
            "cleared_by_conventions": lines_cleared,
            "matched_to_ledger_by_conventions": lines_matched_by_conv,
            "right_pair": right_conv,
            "precision_pair": right_conv / lines_matched_by_conv if lines_matched_by_conv else None,
            "declared_no_ledger_side": no_ledger,
            "share_of_residue_cleared": lines_cleared / n_residue if n_residue else None,
            "escalated_with_a_suggestion": asked_lines,
            "suggestions_that_were_right": sugg_right,
            "escalated_other": dict(reasons),
            "still_for_a_person": asked_lines + len(rest),
            "model_calls": 0,
            "model_cost_usd": 0.0,
        },
        "by_convention_on_eval": per,
        "playbook": [c.as_dict() for c in playbook],
        "operators": profile,
        "account_contrast": {
            "possible": False,
            "finding": "The dataset holds one account (ACC#00001) in one currency (USD), so it cannot show two clients "
                       "or two accounts disagreeing. What it does show is who resolves what: the engine closes "
                       "exact amounts on the same value date (all but a handful of its matches), and the people close everything else under "
                       "conventions the engine does not have.",
        },
        "examples": examples(cleared, asked, rest, eval_a, eval_b, playbook, truth),
        "runtime_seconds": round(time.time() - t0, 1),
        "notes": [
            "No model was called anywhere on this page. Every number is from deterministic code and costs $0 to run.",
            "The scoring script used by the dataset's authors was not available; the grader is our own reading of solution.csv, "
            "and the shipped baseline is graded with the same function.",
            "A bank-side offset is graded right when the key lists no ledger match for either line. It is reported apart "
            "from precision and match rate, which only count lines matched to the ledger.",
            "Operator names are the dataset's own anonymised labels. Which one is the engine is our inference.",
        ],
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=1))

    print("\nEVAL (frozen)")
    for k, t in results["tiers"].items():
        print(f"  {k:<26} matched {t['matched']:>6}  precision {t['precision_pair']:.4%}  match rate {t['match_rate_pair']:.4%}"
              f"  strict {t['precision_strict']:.4%}/{t['match_rate_strict']:.4%}  wrong {t['wrong_pair']}")
    print(f"  residue {n_residue}: conventions cleared {lines_cleared} ({right_conv}/{lines_matched_by_conv} right, "
          f"no-ledger {no_ledger}), asked {asked_lines} (suggestion right {sugg_right}), other {dict(reasons)}")
    for k, v in per.items():
        print(f"    {k:<20} {v}")
    print(f"  wrote {RESULTS} in {results['runtime_seconds']}s")


if __name__ == "__main__":
    main()
