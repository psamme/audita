"""Scoring. A prediction is the set of ledger allocations claimed for one bank line.

strict  : predicted set == labelled set (what the labels literally say)
pair    : predicted set is a non-empty subset of the labelled set (every ledger
          record we attached really belongs to that bank line's match group,
          but the group may contain more)
Headline numbers use strict.
"""


def score(predictions: dict[str, frozenset], truth: dict[str, frozenset]) -> dict:
    n = len(truth)
    matchable = sum(1 for t in truth.values() if t)
    emitted = strict = pair = on_unmatchable = 0
    for b_id, target in truth.items():
        pred = predictions.get(b_id) or frozenset()
        if not pred:
            continue
        emitted += 1
        if not target:
            on_unmatchable += 1
        elif pred == target:
            strict += 1
            pair += 1
        elif pred <= target:
            pair += 1
    return {
        "n_bank_lines": n,
        "n_matchable": matchable,
        "matched": emitted,
        "correct": strict,
        "precision": strict / emitted if emitted else 0.0,
        "match_rate": strict / matchable if matchable else 0.0,
        "emitted_share": emitted / n if n else 0.0,
        "left_for_review": 1 - emitted / n if n else 0.0,
        "correct_pair_level": pair,
        "precision_pair_level": pair / emitted if emitted else 0.0,
        "match_rate_pair_level": pair / matchable if matchable else 0.0,
        "matched_on_unmatchable_lines": on_unmatchable,
    }
