"""Isolated safety contracts. No built client data, hidden keys, network or model calls.

Each reviewed defect is now an ordinary passing regression test.
"""
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from shadow import correct, db, guardrails, llm, matcher, pipeline, playbook, rules, stale, unlearn

PERIOD = "2026-03"


@pytest.fixture
def con(monkeypatch, tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    c.execute("INSERT INTO client VALUES ('T','Test','fixture','{}',3)")
    c.execute("INSERT INTO user VALUES ('senior','Senior','controller',1)")
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(db, "RUNS", tmp_path / "runs")
    (db.DATA / "T").mkdir(parents=True)
    monkeypatch.setattr(db, "connect", lambda *a, **kw: c)
    def no_model(*a, **kw):
        pytest.fail("Safety tests must not call a model")
    monkeypatch.setattr(llm, "call", no_model)
    yield c
    c.close()


def bank(con, bid="T-B-1", amount=100, **kw):
    b = dict(id=bid, period=PERIOD, date="2026-03-10", amount=amount,
             description="Transfer", counterparty="Acme", ref="") | kw
    con.execute("INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)", tuple(b.values()))
    return b


def ledger(con, lid="T-L-1", amount=100, **kw):
    e = dict(id=lid, period=PERIOD, date="2026-03-09", posted_at="2026-03-09",
             account="cash", amount=amount, memo="Expected transfer", counterparty="Acme",
             ref="", invoice_id="") | kw
    con.execute("INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(e.values()))
    return e


def book_rule(**kw):
    return dict(id="T-R-001", status="approved", executable=True, text="Book small fees.",
                when={"direction": "out", "amount_max": 10},
                then={"action": "book", "account": "fees"}, open_question=None) | kw


def context(con):
    return rules.Ctx(con, PERIOD, matcher.open_ledger(con, PERIOD), set())


def test_matcher_rejects_late_posting(con):
    bank(con)
    ledger(con, posted_at="2026-04-20")
    assert matcher.run(con, PERIOD) == ([], set())


def test_rules_also_reject_late_posting(con):
    b = bank(con)
    ledger(con, posted_at="2026-04-20")
    r = book_rule(when={"candidate": {"by": "counterparty"}, "diff_abs_max": 0}, then={"action": "match"})
    _, out = rules.apply({"rules": [r]}, b, "bank", context(con))
    assert not out or "resolution" not in out or out["resolution"]["action"] == "escalate"


@pytest.mark.parametrize("evidence", ["none", "description", "document"])
def test_invoice_batch_requires_references(con, evidence):
    bank(con, amount=100, description="INV-100 INV-200" if evidence == "description" else "Transfer")
    ledger(con, "L1", 40, invoice_id="INV-100")
    ledger(con, "L2", 60, invoice_id="INV-200")
    if evidence == "document":
        con.execute("INSERT INTO document VALUES ('D1','remittance','2026-03-10','acme','Payment','INV-100 INV-200','{}')")
    matches, _ = matcher.run(con, PERIOD)
    assert bool(matches) == (evidence != "none")


def test_anonymous_batch_still_needs_evidence(con):
    bank(con, amount=100, counterparty="")
    ledger(con, "L1", 40)
    ledger(con, "L2", 60)
    assert matcher.run(con, PERIOD) == ([], set())


def test_two_receipts_contesting_same_invoice_batch_abstain(con):
    for bid in ["B1", "B2"]:
        bank(con, bid, 100, description="INV-100 INV-200")
    ledger(con, "L1", 40, invoice_id="INV-100")
    ledger(con, "L2", 60, invoice_id="INV-200")
    assert matcher.run(con, PERIOD) == ([], set())


def test_bank_change_document_holds_second_payment_too(con):
    bank(con, "B1", -100)
    bank(con, "B2", -200, date="2026-03-11")
    ledger(con, "L1", -100)
    ledger(con, "L2", -200)
    con.execute("INSERT INTO document VALUES ('D1','email','2026-03-08','billing@acme.example','New bank details','Our bank account has changed.',?)", (json.dumps({"party": "Acme"}),))
    flags = guardrails.scan(con, PERIOD)
    assert {"B1", "B2"} <= flags.keys()
    assert matcher.run(con, PERIOD, skip=set(flags)) == ([], set())


def test_duplicate_payment_without_refs_is_flagged(con):
    bank(con, "B1", -100)
    bank(con, "B2", -100, date="2026-03-11")
    ledger(con, amount=-100)
    flags = guardrails.scan(con, PERIOD)
    assert all(any(f["flag"] == "possible_duplicate_payment" for f in flags[b]) for b in ["B1", "B2"])


@pytest.mark.parametrize("amount,expected", [(10, "resolution"), (12.4, "in_band"), (20, None)])
def test_band_edges(con, amount, expected):
    b = bank(con, amount=-amount)
    r = book_rule(bands={"amount_max": {"lo": 10, "hi": 20, "side": "upper"}})
    out = rules.evaluate(r, b, "bank", context(con))
    assert out is None if expected is None else expected in out


def test_pipeline_labels_band_escalation(con, monkeypatch):
    bank(con, amount=-12.4)
    r = book_rule(bands={"amount_max": {"lo": 10, "hi": 20, "side": "upper"}})
    monkeypatch.setattr(playbook, "load", lambda *a, **kw: {"version": 1, "rules": [r]})
    pipeline.run("T", PERIOD, "playbook", "dev", use_llm=False, run_id="band")
    item = json.loads((db.RUNS / "band" / "resolutions.jsonl").read_text().splitlines()[0])
    assert item["resolution"]["action"] == "escalate"
    assert item["resolution"]["reason"] == "in_band"


def test_proposed_open_question_defers(con):
    b = bank(con, amount=-5)
    r = book_rule(status="proposed", open_question="May this be booked?")
    assert rules.apply({"rules": [r]}, b, "bank", context(con))[1] == {"defer": True}


def test_approved_rule_with_open_question_cannot_execute(con):
    b = bank(con, amount=-5)
    r = book_rule(open_question="May this be booked?", human_confirmed=False)
    out = rules.apply({"rules": [r]}, b, "bank", context(con))[1]
    assert not out or out.get("defer")


def test_empty_interview_patch_does_not_approve(con, monkeypatch):
    pb = {"version": 1, "rules": [book_rule(status="proposed", open_question="May this be booked?")]}
    monkeypatch.setattr(playbook, "load", lambda *a, **kw: copy.deepcopy(pb))
    monkeypatch.setattr(llm, "call", lambda *a, **kw: SimpleNamespace(text=json.dumps({"explanation": "No change", "ops": []})))
    def no_save(*a, **kw):
        pytest.fail("Empty patch must not save a playbook")
    monkeypatch.setattr(playbook, "save", no_save)
    result = correct.answer("T", "dev", "T-R-001", "I do not know", role="controller")
    assert result["diff"] is None and result["new_version"] == 1
    assert pb["rules"][0]["status"] == "proposed"


def test_replay_preserves_junior_approval_restriction(con):
    base = {"rules": [book_rule(status="proposed", awaiting_senior=True, human_confirmed=False)]}
    cause = {"correction_id": "C1", "by_role": "junior", "patch": {"kind": "ops", "origin": "correction C1", "ops": [
        {"op": "modify", "rule_id": "T-R-001", "text": "Book fees under the revised policy."}]}}
    replayed, _ = unlearn._replay("T", base, [cause], None)
    assert replayed["rules"][0]["status"] != "approved"


def test_future_policy_does_not_apply_to_earlier_transaction(con):
    b = bank(con, amount=-5)
    r = book_rule(valid_from="2026-03-20")
    out = rules.apply({"rules": [r]}, b, "bank", context(con))[1]
    assert not out or out.get("defer")


def test_empty_observation_set_does_not_reload_excluded_precedents(con, monkeypatch):
    def no_history(*a, **kw):
        pytest.fail("Explicitly empty observations must stay empty")
    monkeypatch.setattr(playbook.history, "observe", no_history)
    playbook.compute_bands(con, {"trained_before": PERIOD, "rules": [book_rule()]}, observed={})


@pytest.mark.parametrize("field,value", [("amount", 101), ("date", "2026-03-08"), ("account", "other")])
def test_stale_detects_ledger_edits(con, field, value):
    ledger(con)
    seed_run(con)
    con.execute(f"UPDATE ledger_entry SET {field}=?", (value,))
    assert stale.verify("T", "prior")["stale"][0]["changes"][0]["fields"][field]["now"] == value


def seed_run(con):
    res = {"action": "match", "ledger_ids": ["T-L-1"], "evidence_ids": ["T-DOC-1"]}
    folder = db.RUNS / "prior"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"period": PERIOD, "created_at": "2026-03-12T12:00:00"}))
    con.execute("INSERT INTO document VALUES ('T-DOC-1','remittance','2026-03-10','acme','Payment','Original instruction','{}')")
    (folder / "resolutions.jsonl").write_text(json.dumps({"item_id": "B1", "record": {}, "resolution": res,
                                                        "evidence_fingerprint": stale.fingerprint(con, res)}) + "\n")


def test_stale_detects_changed_supporting_document(con):
    ledger(con)
    seed_run(con)
    con.execute("UPDATE document SET body='Payment instruction withdrawn'")
    assert stale.verify("T", "prior")["stale"]


def test_invalid_correction_is_not_published(con, monkeypatch):
    b = bank(con, amount=-5)
    playbook.save("T", "dev", {"trained_before": PERIOD, "rules": []}, {"type": "induction"})
    reply = {"explanation": "Wrong account", "ops": [{"op": "add", "text": "Book fees.", "executable": True,
             "when": {"direction": "out"}, "then": {"action": "book", "account": "wrong"}}]}
    monkeypatch.setattr(llm, "call", lambda *a, **kw: SimpleNamespace(text=json.dumps(reply)))
    item = {"item_id": b["id"], "record": b, "item_kind": "bank", "tier": "investigator", "resolution": {"action": "escalate"}}
    human = {"action": "book", "ledger_ids": [], "adjustments": [{"account": "fees", "amount": 5}]}
    result = correct.correct("T", "dev", item, human, "Use the fees account", role="controller")
    assert result["diff"] is None
    assert playbook.load("T", "dev")["version"] == 1
