"""Fast checks on the parts where a bug would silently corrupt the numbers. Uses the built history DBs."""
import ast
from pathlib import Path

import pytest

from grade import grade_item, same
from shadow import db, guardrails, history, matcher, rules

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not db.db_path("A").exists(), reason="run `uv run python -m sim.build` first")


def test_agent_code_never_touches_truth_or_keys():
    for path in (ROOT / "shadow").glob("*.py"):
        src = path.read_text()
        for banned in ("truth.db", "truth_path", "keys/", "KEYS", "POLICIES"):
            assert banned not in src, (path.name, banned)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                for name in names:
                    assert not name.startswith("sim") or (path.name == "experiment.py" and name == "sim.experiment_data")  # demo fixtures only, (path.name, name)


def test_agent_db_has_no_resolution_table():
    con = db.connect("A", readonly=True)
    tables = {r["name"] for r in db.q(con, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "resolution" not in tables and {"reconcile_link", "journal_entry", "approval"} <= tables


def test_grader():
    key = {"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6120", "amount": 10.0}], "alternatives": []}
    assert same({"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6120", "amount": 10.004}]}, key)
    assert not same({"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6110", "amount": 10.0}]}, key)
    wrong = grade_item({"action": "match", "ledger_ids": ["L2"], "adjustments": []}, key)
    assert wrong["wrong_match"] and wrong["wrong_auto"] and not wrong["correct"]
    esc = grade_item({"action": "escalate", "ledger_ids": [], "adjustments": []}, key)
    assert not esc["wrong_match"] and not esc["wrong_auto"] and not esc["correct"]
    assert grade_item(None, key)["missing"]


@pytest.mark.parametrize("client", ["A", "B"])
def test_matcher_never_double_claims_and_agrees_with_the_trail(client):
    con = db.connect(client, readonly=True)
    matches, used = matcher.run(con, "2026-03")
    claimed = [i for m in matches for i in m["ledger_ids"]]
    assert len(claimed) == len(set(claimed)) == len(used)
    trail = {o["item"]["id"]: o for o in history.observe(con, "2026-04") if o["item_kind"] == "bank"}
    checked = wrong = 0
    for m in matches:
        o = trail.get(m["bank_id"])
        if o and o["ledger"] and not o["reviewed_by"]:
            checked += 1
            wrong += sorted(e["id"] for e in o["ledger"]) != sorted(m["ledger_ids"])
    assert checked > 50 and wrong / checked < 0.01


def test_rules_abstain_when_two_items_claim_one_entry():
    con = db.connect("A", readonly=True)
    e = db.q(con, "SELECT * FROM ledger_entry WHERE period='2026-03' AND amount > 300 LIMIT 1")[0]
    bank = [{"id": f"X{i}", "period": "2026-03", "date": e["date"], "amount": e["amount"] - 5 - i, "description": "DEPOSIT",
             "counterparty": "", "ref": ""} for i in range(2)]
    pb = {"rules": [{"id": "T-1", "status": "approved", "executable": True, "text": "",
                     "when": {"candidate": {"by": "amount_near", "window_days": 3}, "diff_min": 0, "diff_max": 6.5},
                     "then": {"action": "match_adjust", "account": "6990"}}]}
    ctx = rules.Ctx(con, "2026-03", [e], set())
    planned = rules.plan(pb, [("bank", b) for b in bank], ctx)
    assert all(out.get("contested") == e["id"] for _, out in planned.values())


def test_guardrail_flags_new_payee_account_once():
    con = db.connect("B", readonly=True)
    flagged = guardrails.scan(con, "2026-02")
    kinds = {f["flag"] for fl in flagged.values() for f in fl}
    assert "payee_bank_details_changed" in kinds
    assert not any(f["flag"] == "payee_bank_details_changed" for fl in guardrails.scan(con, "2026-03").values() for f in fl)
