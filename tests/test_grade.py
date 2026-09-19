"""The grader is the scoreboard; these pin the behaviours a run could otherwise game."""
import json

import grade


def item(item_id, action, date="2026-04-10", cost=0.0, calls=0, tier="investigator", **res):
    return {"item_id": item_id, "record": {"date": date}, "tier": tier, "usage": {"cost_usd": cost, "llm_calls": calls},
            "resolution": {"action": action, **res}}


def run(tmp_path, items, key, **kw):
    (tmp_path / "resolutions.jsonl").write_text("\n".join(json.dumps(i) for i in items))
    (tmp_path / "key.json").write_text(json.dumps(key))
    return grade.grade(tmp_path, tmp_path / "key.json", **kw)


def test_clean_match_with_invented_adjustment_is_wrong():
    key = {"action": "match", "ledger_ids": ["L1"], "adjustments": []}
    assert grade.grade_item({"action": "match", "ledger_ids": ["L1"], "adjustments": []}, key)["correct"]
    assert not grade.grade_item({"action": "match", "ledger_ids": ["L1"], "adjustments": [{"account": "6999", "amount": 500}]}, key)["correct"]


def test_adjustment_tolerance_is_one_cent():
    key = {"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6110", "amount": 48.91}]}
    pred = lambda amt: {"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6110", "amount": amt}]}
    assert grade.grade_item(pred(48.92), key)["correct"]
    assert not grade.grade_item(pred(48.93), key)["correct"]
    assert not grade.grade_item(pred(-48.91), key)["correct"]


def test_wrong_ledger_is_a_wrong_match_but_escalating_never_is():
    key = {"action": "match", "ledger_ids": ["L1"]}
    assert grade.grade_item({"action": "match", "ledger_ids": ["L2"]}, key)["wrong_match"]
    g = grade.grade_item({"action": "escalate", "escalate_to": "owner"}, key)
    assert not g["correct"] and not g["wrong_match"] and not g["wrong_auto"]


def test_escalation_accepted_through_an_alternative_counts_and_routes():
    key = {"action": "match", "ledger_ids": ["L1"], "alternatives": [{"action": "escalate", "escalate_to": "owner"}]}
    g = grade.grade_item({"action": "escalate", "escalate_to": "owner"}, key)
    assert g["correct"] and g["should_escalate"] and g["routed"]
    assert not grade.grade_item({"action": "escalate", "escalate_to": "ops_manager"}, key)["routed"]


def test_unkeyed_items_still_cost_money_and_review_time(tmp_path):
    key = {"B1": {"action": "escalate", "escalate_to": "owner", "date": "2026-04-10"}}
    items = [item("B1", "escalate", cost=1.0, calls=2, escalate_to="owner"), item("L9", "escalate", cost=0.5, calls=1, escalate_to="owner")]
    m = run(tmp_path, items, key)["metrics"]
    assert m["n_items"] == 1 and m["unkeyed"] == 1 and m["unkeyed_escalated"] == 1
    assert m["cost_usd"] == 1.5 and m["llm_calls"] == 3 and m["review_load"] == 2
    assert m["escalation_precision"] == 1.0


def test_escalating_everything_scores_badly_and_missing_items_are_incorrect(tmp_path):
    key = {f"B{i}": {"action": "match", "ledger_ids": [f"L{i}"], "date": "2026-04-10", "easy": True} for i in range(9)}
    key["B9"] = {"action": "escalate", "escalate_to": "owner", "date": "2026-04-10"}
    m = run(tmp_path, [item(k, "escalate", escalate_to="owner") for k in key], key)["metrics"]
    assert m["accuracy"] == 0.1 and m["escalation_precision"] == 0.1 and m["wrong_match_of_matched"] is None
    m = run(tmp_path, [], key)["metrics"]
    assert m["accuracy"] == 0.0 and m["missing"] == 10 and m["wrong_match_rate"] == 0.0


def test_slice_uses_the_keys_date_not_the_runs(tmp_path):
    key = {"B1": {"action": "match", "ledger_ids": ["L1"], "date": "2026-04-20"}}
    moved = [item("B1", "match", date="2026-04-02", ledger_ids=["L9"])]     # the run claims an earlier date to dodge the window
    m = run(tmp_path, moved, key, date_from="2026-04-16")["metrics"]
    assert m["n_items"] == 1 and m["wrong_match_rate"] == 1.0


def test_wrong_match_rate_is_over_matches_made(tmp_path):
    key = {"B1": {"action": "match", "ledger_ids": ["L1"], "date": "2026-04-10"}, "B2": {"action": "match", "ledger_ids": ["L2"], "date": "2026-04-10"},
           "B3": {"action": "escalate", "escalate_to": "owner", "date": "2026-04-10"}, "B4": {"action": "escalate", "escalate_to": "owner", "date": "2026-04-10"}}
    items = [item("B1", "match", ledger_ids=["L1"]), item("B2", "match", ledger_ids=["LX"]), item("B3", "escalate", escalate_to="owner"), item("B4", "escalate", escalate_to="owner")]
    m = run(tmp_path, items, key)["metrics"]
    assert m["matches_made"] == 2 and m["wrong_match_of_matched"] == 0.5 and m["wrong_match_rate"] == 0.25
