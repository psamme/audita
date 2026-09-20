"""Month-end close status: built on a tiny invented client so every figure on the page can be checked by hand."""
import json

import pytest

from shadow import close, db


def _res(action, ledger_ids=(), adjustments=(), **kw):
    return {"action": action, "ledger_ids": list(ledger_ids), "adjustments": list(adjustments), "escalate_to": None, "rule_id": None,
            "reason": None} | kw


def _item(bank, tier, res):
    return {"item_id": bank["id"], "item_kind": "bank", "record": bank, "tier": tier, "resolution": res,
            "usage": {"cost_usd": 0.25 if tier == "investigator" else 0.0, "llm_calls": 0}}


@pytest.fixture
def tiny(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(db, "RUNS", tmp_path / "runs")
    con = db.connect("A", path=tmp_path / "data" / "A" / "client.db")
    con.execute("INSERT INTO client VALUES ('A', 'Tiny Co', '', ?, 5)", (json.dumps({"6990": "Small balances"}),))
    bank = [{"id": "A-BL-1", "period": "2026-04", "date": "2026-04-02", "amount": 100.0, "description": "", "counterparty": "", "ref": ""},
            {"id": "A-BL-2", "period": "2026-04", "date": "2026-04-10", "amount": 95.5, "description": "", "counterparty": "", "ref": ""},
            {"id": "A-BL-3", "period": "2026-04", "date": "2026-04-20", "amount": -700.0, "description": "", "counterparty": "", "ref": ""},
            {"id": "A-BL-4", "period": "2026-04", "date": "2026-04-30", "amount": -50.0, "description": "", "counterparty": "", "ref": ""}]
    for b in bank:
        con.execute("INSERT INTO bank_line VALUES (:id, :period, :date, :amount, :description, :counterparty, :ref)", b)
    for i, amt in (("A-LE-1", 100.0), ("A-LE-2", 100.0)):
        con.execute("INSERT INTO ledger_entry VALUES (?, '2026-04', '2026-04-01', '2026-04-01', '4000', ?, '', '', '', NULL)", (i, amt))
    con.commit()
    monkeypatch.setattr(db, "db_path", lambda c: tmp_path / "data" / c / "client.db")
    items = [_item(bank[0], "matcher", _res("match", ["A-LE-1"])),
             _item(bank[1], "rule", _res("match_adjust", ["A-LE-2"], [{"account": "6990", "amount": 4.5}], rule_id="A-R-001")),
             _item(bank[2], "guardrail", _res("escalate", reason="fraud_shaped")) | {"control_flags": [{"flag": "payee_bank_details_changed", "detail": ""}]},
             _item(bank[3], "investigator", _res("escalate", reason="thin_precedent"))]
    run = tmp_path / "runs" / "A_2026-04_corrected"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "client": "A", "period": "2026-04", "track": "main", "condition": "corrected",
                                              "playbook_version": 1, "created_at": "2026-05-01T09:00:00"}))
    (run / "resolutions.jsonl").write_text("\n".join(json.dumps(i) for i in items))
    pb = tmp_path / "data" / "A" / "playbook" / "main"
    pb.mkdir(parents=True)
    (pb / "v1.json").write_text(json.dumps({"version": 1, "rules": [
        {"id": "A-R-001", "status": "approved", "when": {"diff_abs_max": 5}, "then": {"action": "match_adjust", "account": "6990"}}]}))
    return tmp_path


def test_checklist_counts_tie_out_and_headline(tiny):
    s = close.status("A", "2026-04")
    row = {r["key"]: r for r in s["rows"]}
    assert row["imported"]["count"] == 4 and row["imported"]["status"] == "PASS"
    assert (row["matcher"]["count"], row["rules"]["count"], row["investigator"]["count"]) == (1, 1, 0)
    assert row["tieout"]["status"] == "PASS" and row["tieout"]["amount"] == 0          # 95.50 - 100.00 + 4.50
    assert row["writeoffs"]["status"] == "PASS" and row["writeoffs"]["amount"] == 4.5
    assert row["held"]["status"] == "HELD" and row["held"]["amount"] == 700.0
    assert row["open"]["status"] == "OPEN" and row["open"]["count"] == 1 and "0 days" in row["open"]["detail"]
    assert row["unreconciled"]["amount"] == -750.0
    assert row["audit"]["status"] == "INFO"                                            # no audit file: says so, does not fail
    assert s["blocked_items"] == 2 and s["blocked_usd"] == 750.0 and "blocked by 2 items worth $750.00" in s["headline"]


def test_a_later_rerun_and_a_write_off_over_the_limit_change_the_status(tiny):
    rid = "A_2026-04_corrected__after_A-COR-0001"
    run = tiny / "runs" / rid
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"run_id": rid, "client": "A", "period": "2026-04", "track": "main", "condition": "corrected",
                                              "playbook_version": 1, "created_at": "2026-05-01T09:05:00"}))
    bank4 = {"id": "A-BL-4", "period": "2026-04", "date": "2026-04-30", "amount": -50.0}
    (run / "resolutions.jsonl").write_text(json.dumps(_item(bank4, "rule", _res("book", [], [{"account": "6990", "amount": 50.0}], rule_id="A-R-001"))))
    s = close.status("A", "2026-04")
    row = {r["key"]: r for r in s["rows"]}
    assert row["open"]["status"] == "PASS" and s["blocked_items"] == 1                 # the newer resolution stands
    assert row["writeoffs"]["status"] == "FAIL" and "A-BL-4" in row["writeoffs"]["detail"]   # 50.00 booked under a 5.00 limit
    assert "1 check failed" in s["headline"]


def test_an_item_an_undo_reopened_counts_as_open_until_something_resolves_it_again(tiny):
    (tiny / "data" / "A" / "reopened.jsonl").write_text(json.dumps({"run_id": "A_2026-04_corrected", "item_id": "A-BL-2", "reason": "rule_retracted"}) + "\n")
    s = close.status("A", "2026-04")
    row = {r["key"]: r for r in s["rows"]}
    assert row["rules"]["count"] == 0 and row["open"]["count"] == 2 and "re-opened by an undo" in row["open"]["detail"]
