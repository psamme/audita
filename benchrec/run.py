"""BenchRec evaluation of the deterministic tier-0 matcher.

    uv run python -m benchrec.run

Profiles the data, tunes thresholds on TRAIN only, evaluates once on EVAL against
solution.csv, scores the shipped MatcherByChatGPT baseline the same way, and writes
runs/benchrec_results.json.
"""

import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date
from itertools import product
from pathlib import Path

from . import data
from .matcher import Blocks, Thresholds, match
from .scoring import score

TARGET_PRECISION = 0.998
RESULTS = Path(__file__).resolve().parent.parent / "runs" / "benchrec_results.json"

# fixed before any evaluation: strict uniqueness everywhere, no tuning involved
CONSERVATIVE = Thresholds()


def pct(x):
    return f"{100 * x:.2f}%"


def profile(name, ledger, bank, labelled):
    print(f"\n== {name}: {len(ledger):,} ledger (A) rows, {len(bank):,} bank (B) rows")
    print(f"   currencies {sorted({r.currency for r in ledger + bank})}, "
          f"accounts {sorted({r.account for r in ledger + bank})}")
    days = [r.day for r in bank]
    print(f"   bank value dates {date.fromordinal(min(days))} .. {date.fromordinal(max(days))}")
    for side, rows in (("A", ledger), ("B", bank)):
        signs = Counter((r.dc, "neg" if r.cents < 0 else "pos") for r in rows)
        print(f"   {side} sign convention: " + ", ".join(f"{dc}/{s}={n:,}" for (dc, s), n in sorted(signs.items())))
    allocs = Counter(a.allocation for a in ledger)
    print(f"   distinct A_allocation {len(allocs):,} for {len(ledger):,} A rows "
          f"({sum(1 for n in allocs.values() if n > 1):,} allocations shared by >1 A row)")
    a_per_amount = Counter(a.cents for a in ledger)
    n_cand = Counter(min(a_per_amount.get(b.cents, 0), 3) for b in bank)
    print("   same-amount ledger candidates per bank line: "
          + ", ".join(f"{k if k < 3 else '3+'}: {pct(n_cand[k] / len(bank))}" for k in range(4)))
    if not labelled:
        return {}

    groups = defaultdict(lambda: ([], []))
    for a in ledger:
        groups[a.match_id][0].append(a)
    for b in bank:
        groups[b.match_id][1].append(b)
    shape = Counter()
    for As, Bs in groups.values():
        kind = ("1 A : 1 B" if (len(As), len(Bs)) == (1, 1) else
                "B only (no ledger side)" if not As else
                "A only (no bank side)" if not Bs else
                "1 A : many B" if len(As) == 1 else
                "many A : 1 B" if len(Bs) == 1 else "many A : many B")
        shape[kind] += len(Bs)
    print("   bank lines by labelled match-group shape:")
    for kind, n in shape.most_common():
        if n:
            print(f"     {kind:<26} {n:>7,}  {pct(n / len(bank))}")
    unmatched = sum(1 for b in bank if not b.target)
    print(f"   unmatched bank lines = blank targetAllocation: {unmatched:,} ({pct(unmatched / len(bank))}); "
          "their matchId groups contain bank rows only")
    multi = sum(1 for b in bank if len(b.target) > 1)
    print(f"   bank lines whose target is a LIST of several allocations: {multi:,} ({pct(multi / len(bank))})")

    pairs = [(As[0], Bs[0]) for As, Bs in groups.values() if len(As) == len(Bs) == 1]
    same_amt = sum(a.cents == b.cents for a, b in pairs)
    same_day = sum(a.day == b.day for a, b in pairs)
    print(f"   in 1:1 groups: amounts identical incl. sign {pct(same_amt / len(pairs))}, "
          f"same value date {pct(same_day / len(pairs))}")

    def hit(tokens, text):
        return any(len(t) >= 8 and any(c.isdigit() for c in t) and t in text for t in tokens)

    tok = re.compile(r"[A-Z0-9]+")
    sig = Counter()
    for a, b in pairs:
        sig["A refs token in B attributes"] += hit(a.tokens, " ".join(tok.findall(b.attrs)))
        sig["A refs token in B references"] += hit(a.tokens, " ".join(tok.findall(b.refs)))
        sig["A attributes token in B text"] += hit(tok.findall(a.attrs), b.squashed)
    print("   reference signal in 1:1 pairs (token >= 8 chars with a digit, found as substring):")
    for k, n in sig.items():
        print(f"     {k:<30} {pct(n / len(pairs))}")
    rules = Counter(b.rule or "(blank)" for b in bank)
    print("   how production matched the bank lines: "
          + ", ".join(f"{k} {pct(n / len(bank))}" for k, n in rules.most_common(4)))
    return {
        "share_bank_lines_in_1to1_groups": shape["1 A : 1 B"] / len(bank),
        "share_bank_lines_unmatched": unmatched / len(bank),
        "share_bank_lines_with_list_target": multi / len(bank),
        "share_1to1_same_amount": same_amt / len(pairs),
        "share_1to1_same_value_date": same_day / len(pairs),
        "share_1to1_with_ref_link": sig["A refs token in B attributes"] / len(pairs),
    }


def evaluate(blocks, truth, th):
    matches = match(blocks, th)
    s = score({m.b_id: m.allocations for m in matches}, truth)
    tiers = defaultdict(lambda: [0, 0])
    for m in matches:
        t = tiers[m.tier]
        t[0] += 1
        t[1] += bool(truth[m.b_id]) and m.allocations <= truth[m.b_id]
    s["tiers"] = {k: {"matched": n, "correct": c, "precision": c / n} for k, (n, c) in sorted(tiers.items())}
    return s, matches


def tune(blocks, truth):
    """Staged grid search on train: ref tier, then date tier, then near tier.

    Each stage keeps the setting with the most correct matches for which the overall
    precision AND every individual tier's precision (pair level) are >= TARGET_PRECISION,
    so a sloppy rule cannot hide behind the slack of a precise one.
    """
    runs = 0

    def best_of(candidates):
        nonlocal runs
        best = None
        for th in candidates:
            s, _ = evaluate(blocks, truth, th)
            runs += 1
            clears = s["precision_pair_level"] >= TARGET_PRECISION and all(
                t["precision"] >= TARGET_PRECISION for t in s["tiers"].values()
            )
            if clears and (
                best is None or s["correct_pair_level"] > best[1]["correct_pair_level"]
            ):
                best = (th, s)
        return best

    off = Thresholds(date_max_k=0, near_window=0)
    stage1 = best_of(
        replace(off, min_token_len=t, ref_window=w, ref_max_k=k, ref_max_rivals=r)
        for t, w, k, r in product((8, 9), (3, 10, 30), (1, 2, 4, 99), (0, 2, 99))
    )
    th = stage1[0] if stage1 else off
    stage2 = best_of(
        replace(th, date_max_k=k, date_max_rivals=r, sibling_guard=g)
        for k, r, g in product((0, 1, 2, 4), (0, 2, 99), (True, False))
    )
    th = stage2[0] if stage2 else th
    stage3 = best_of(
        replace(th, near_window=w, near_max_rivals=r) for w, r in product((0, 1, 3, 6), (0, 2))
    )
    th = stage3[0] if stage3 else th
    return th, runs


def show(title, s):
    print(f"\n-- {title}")
    print(f"   bank lines {s['n_bank_lines']:,} | matchable {s['n_matchable']:,} | matched {s['matched']:,} "
          f"| left for review {pct(s['left_for_review'])}")
    print(f"   pair level : precision {pct(s['precision_pair_level'])}  match rate {pct(s['match_rate_pair_level'])}  "
          f"(wrong {s['matched'] - s['correct_pair_level']:,})")
    print(f"   strict set : precision {pct(s['precision'])}  match rate {pct(s['match_rate'])}")
    for tier, t in s.get("tiers", {}).items():
        print(f"     tier {tier:<5} matched {t['matched']:>6,}  precision {pct(t['precision'])}")


def main():
    t0 = time.time()
    train_a, train_b = data.load_records(data.TRAIN)
    eval_a, eval_b = data.load_records(data.EVAL)
    solution = data.load_solution()
    baseline = data.load_baseline()

    print("STRUCTURAL PROFILE")
    facts = profile("train", train_a, train_b, labelled=True)
    profile("eval", eval_a, eval_b, labelled=False)
    sizes = Counter(min(len(t), 3) for t in solution.values())
    print(f"   eval solution: {sizes[0]:,} unmatched (blank), {sizes[1]:,} single allocation, "
          f"{sizes[2] + sizes[3]:,} list of several")

    print("\nTUNING ON TRAIN")
    train_blocks = Blocks(train_a, train_b)
    train_truth = {b.id: b.target for b in train_b}
    tuned, n_runs = tune(train_blocks, train_truth)
    train_tuned, _ = evaluate(train_blocks, train_truth, tuned)
    train_cons, _ = evaluate(train_blocks, train_truth, CONSERVATIVE)
    print(f"   {n_runs} threshold settings tried; chosen: {tuned.as_dict()}")
    show("train, tuned operating point (max match rate s.t. pair precision >= 99.8%)", train_tuned)
    show("train, conservative operating point (strict uniqueness, untuned)", train_cons)

    print("\nEVALUATION ON EVAL (single pass, thresholds frozen)")
    eval_blocks = Blocks(eval_a, eval_b)
    eval_tuned, matches = evaluate(eval_blocks, solution, tuned)
    eval_cons, _ = evaluate(eval_blocks, solution, CONSERVATIVE)
    base = score(baseline, solution)
    show("eval, tuned operating point  <- headline", eval_tuned)
    show("eval, conservative operating point", eval_cons)
    show("eval, shipped MatcherByChatGPT_submission.csv baseline", base)

    a_used = Counter(m.a_id for m in matches)
    assert max(a_used.values(), default=0) <= 1, "one-to-one violated"
    print("\n   sample explanations:")
    for m in matches[:3]:
        print(f"     B {m.b_id} -> A {m.a_id}: {m.reason}")

    runtime = round(time.time() - t0, 1)
    met = eval_tuned["precision_pair_level"] >= TARGET_PRECISION
    print(f"\n   precision target {pct(TARGET_PRECISION)} on eval: {'MET' if met else 'MISSED'} "
          f"({pct(eval_tuned['precision_pair_level'])}); runtime {runtime}s")

    notes = [
        "Headline precision/match_rate are PAIR LEVEL: an emitted match is correct when the ledger allocation(s) "
        "we attached are a subset of the labelled targetAllocation list of that bank line. *_strict fields require "
        "the predicted set to equal the labelled list exactly.",
        "Strict is capped for any one-to-one matcher: production staff bulk-matched batches of same-amount lines "
        "under one matchId, so the label is the whole batch even when references identify the exact pair.",
        "match_rate = correct matches / matchable bank lines (non-blank target). left_for_review = bank lines with "
        "no emitted match / all bank lines.",
        "Thresholds tuned on train only (staged grid; overall and per-tier pair-level precision >= 99.8%); eval scored once with frozen "
        "thresholds. The conservative point is the untuned default and was fixed before evaluation.",
        "Exact-amount candidates only: bank lines whose true ledger record has a different amount (about 5% in "
        "train) and true one-to-many sums are always abstained.",
        "The Kaggle scoring script was not available; both scoring conventions here are our own reading of "
        "solution.csv. The baseline is scored with the identical function.",
    ]
    results = {
        "dataset": "BenchRec cash v1.0 (eval split, solution.csv)",
        "n_bank_lines": eval_tuned["n_bank_lines"],
        "n_matchable": eval_tuned["n_matchable"],
        "matched": eval_tuned["matched"],
        "correct": eval_tuned["correct_pair_level"],
        "precision": eval_tuned["precision_pair_level"],
        "match_rate": eval_tuned["match_rate_pair_level"],
        "left_for_review": eval_tuned["left_for_review"],
        "precision_strict": eval_tuned["precision"],
        "match_rate_strict": eval_tuned["match_rate"],
        "matched_on_unmatchable_lines": eval_tuned["matched_on_unmatchable_lines"],
        "precision_target": TARGET_PRECISION,
        "precision_target_met_on_eval": met,
        "train_precision": train_tuned["precision_pair_level"],
        "train_match_rate": train_tuned["match_rate_pair_level"],
        "baseline_matched": base["matched"],
        "baseline_precision": base["precision_pair_level"],
        "baseline_match_rate": base["match_rate_pair_level"],
        "baseline_precision_strict": base["precision"],
        "baseline_match_rate_strict": base["match_rate"],
        "conservative_matched": eval_cons["matched"],
        "conservative_precision": eval_cons["precision_pair_level"],
        "conservative_match_rate": eval_cons["match_rate_pair_level"],
        "conservative_train_precision": train_cons["precision_pair_level"],
        "thresholds": tuned.as_dict(),
        "conservative_thresholds": CONSERVATIVE.as_dict(),
        "tiers": eval_tuned["tiers"],
        "train_profile": facts,
        "runtime_seconds": runtime,
        "notes": notes,
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2))
    print(f"   wrote {RESULTS}")


if __name__ == "__main__":
    main()
