"""Bands, band answers, retraction and stale evidence: all deterministic, no model calls."""
import json
import shutil

import pytest

from shadow import correct, db, guardrails, pipeline, playbook, rules, stale, unlearn

pytestmark = pytest.mark.skipif(not db.db_path("A").exists(), reason="run `uv run python -m sim.build` first")
TRACK = "t_unit"


@pytest.fixture
def fee_playbook():
    shutil.rmtree(playbook.pb_dir("A", TRACK), ignore_errors=True)
    con = db.connect("A", readonly=True)
    pb = {"trained_before": "2026-03", "rules": [{
        "id": "A-R-001", "text": "Small bank fees are booked to bank fees.", "executable": True, "status": "proposed",
        "when": {"direction": "out", "counterparty_regex": "first prairie", "amount_max": 500}, "then": {"action": "book", "account": "6110"},
        "precedent_ids": [], "open_question": None}]}
    playbook.backtest(con, pb)
    saved = playbook.save("A", TRACK, pb, {"type": "induction", "before": "2026-03"})
    yield con, saved
    shutil.rmtree(playbook.pb_dir("A", TRACK), ignore_errors=True)
    for name in (f"corrections_{TRACK}.jsonl", f"reopened_{TRACK}.jsonl"):
        (db.DATA / "A" / name).unlink(missing_ok=True)


def test_band_comes_from_the_trail_not_from_the_written_number(fee_playbook):
    con, pb = fee_playbook
    band = pb["rules"][0]["bands"]["amount_max"]
    assert band["written"] == 500 and 0 < band["lo"] < 30          # the model wrote 500; the client never booked one that large unreviewed
    assert band["hi"] is None or band["hi"] > band["lo"]
    assert pb["rules"][0]["status"] == "approved" and band["lo_precedent"]


def test_item_inside_the_band_is_escalated_not_guessed(fee_playbook):
    con, pb = fee_playbook
    rule, lo = pb["rules"][0], pb["rules"][0]["bands"]["amount_max"]["lo"]
    hi = pb["rules"][0]["bands"]["amount_max"]["hi"]
    ctx = rules.Ctx(con, "2026-03", [], set())
    line = lambda amt: {"id": "X", "period": "2026-03", "date": "2026-03-10", "amount": -amt, "description": "SERVICE CHARGE",
                        "counterparty": "First Prairie Bank", "ref": ""}
    assert rules.evaluate(rule, line(lo - 1), "bank", ctx)["resolution"]["action"] == "book"
    inside = (lo + hi) / 2 if hi else lo + 5
    out = rules.evaluate(rule, line(inside), "bank", ctx)
    assert out["in_band"]["lo"] == lo and out["in_band"]["lo_precedent"]
    if hi:
        assert rules.evaluate(rule, line(hi + 1), "bank", ctx) is None


def test_band_answer_then_retraction_restores_the_band(fee_playbook):
    con, pb = fee_playbook
    before = dict(pb["rules"][0]["bands"]["amount_max"])
    value = before["lo"] + 4
    ans = correct.answer_band("A", TRACK, "A-R-001", "amount_max", value, review=True)
    assert ans["band"]["hi"] == value and ans["band"]["source"] == "interview" and ans["diff"]["changed"]
    out = unlearn.retract("A", TRACK, correction_id=ans["correction_id"])
    now = playbook.load("A", TRACK)
    assert now["cause"]["type"] == "retraction" and now["rules"][0]["bands"]["amount_max"]["hi"] == before["hi"]
    assert out["rules_changed"] == ["A-R-001"] and "resolutions_checked" in out
    with pytest.raises(ValueError):
        unlearn.retract("A", TRACK, correction_id="A-COR-9999")


def test_retraction_is_a_clean_inverse(fee_playbook):
    con, pb = fee_playbook
    core = lambda p: json.dumps([{k: v for k, v in r.items()} for r in p["rules"]], sort_keys=True)
    before = core(playbook.load("A", TRACK))
    ans = correct.answer_band("A", TRACK, "A-R-001", "amount_max", limit=50.0)
    rule = playbook.load("A", TRACK)["rules"][0]
    assert rule["when"]["amount_max"] == 50.0 and "50.00" in rule["text"]          # the sentence and the condition follow the stated limit
    out = unlearn.retract("A", TRACK, correction_id=ans["correction_id"])
    assert core(playbook.load("A", TRACK)) == before and out["rules_changed"] == ["A-R-001"]


def test_held_answers_are_logged_as_held_and_cannot_be_retracted(fee_playbook):
    held = correct.answer_band("A", TRACK, "A-R-001", "amount_max", limit=90.0, role="bookkeeper")
    log = [json.loads(l) for l in correct.log_path("A", TRACK).read_text().splitlines()]
    assert log[-1]["status"] == "held" and log[-1]["summary"].startswith("Held")
    with pytest.raises(ValueError):
        unlearn.retract("A", TRACK, correction_id=held["correction_id"])


def test_stated_limit_closes_the_band_and_not_amount_blocks_the_rule(fee_playbook):
    con, pb = fee_playbook
    ans = correct.answer_band("A", TRACK, "A-R-001", "amount_max", limit=25.0)
    assert (ans["band"]["lo"], ans["band"]["hi"], ans["band"]["source"]) == (25.0, 25.01, "stated")
    assert playbook.band_questions(playbook.load("A", TRACK)) == []
    held = correct.answer_band("A", TRACK, "A-R-001", "amount_max", limit=90.0, role="bookkeeper")
    assert held["diff"] is None and "senior" in held["held"]
    correct.answer_band("A", TRACK, "A-R-001", "amount_max", not_amount=True)
    rule = playbook.load("A", TRACK)["rules"][0]
    assert rule["status"] == "proposed" and rule["open_question"]


def test_usual_way_does_not_widen_a_rule_with_an_open_question(fee_playbook):
    con, pb = fee_playbook
    d = playbook.load("A", TRACK)
    d["rules"][0]["open_question"] = "Is this about the amount?"
    playbook.save("A", TRACK, {k: v for k, v in d.items() if k not in ("version", "created_at", "cause")}, {"type": "test"})
    lo = d["rules"][0]["bands"]["amount_max"]["lo"]
    ans = correct.answer_band("A", TRACK, "A-R-001", "amount_max", value=lo + 10, review=False)
    assert ans["held"] and ans["band"]["lo"] == lo and ans["diff"] is None
    assert playbook.load("A", TRACK)["rules"][0]["bands"]["amount_max"]["lo"] == lo


def test_stale_evidence_is_noticed(tmp_path):
    copy = tmp_path / "client.db"
    shutil.copy(db.db_path("A"), copy)
    shutil.rmtree(playbook.pb_dir("A", "no_playbook"), ignore_errors=True)
    pipeline.run("A", "2026-03", "cold_start", "no_playbook", use_llm=False, run_id="t_stale", date_to="2026-03-05", db_file=copy)
    assert stale.verify("A", "t_stale", db_file=copy)["stale"] == []
    items = [json.loads(l) for l in (db.RUNS / "t_stale" / "resolutions.jsonl").read_text().splitlines()]
    victim = next(i for i in items if i["tier"] == "matcher")["resolution"]["ledger_ids"][0]
    import sqlite3
    con = sqlite3.connect(copy)
    con.execute("UPDATE ledger_entry SET amount = amount + 10 WHERE id=?", (victim,))
    con.commit()
    found = stale.verify("A", "t_stale", db_file=copy)["stale"]
    assert len(found) == 1 and found[0]["changes"][0]["fields"]["amount"]["now"] - found[0]["changes"][0]["fields"]["amount"]["was"] == pytest.approx(10)
    shutil.rmtree(db.RUNS / "t_stale")


def test_change_request_document_stops_the_first_payment_even_without_an_account_token(tmp_path):
    import sqlite3
    copy = tmp_path / "client.db"
    shutil.copy(db.db_path("A"), copy)
    con = sqlite3.connect(copy)
    pay = con.execute("SELECT id, counterparty, date FROM bank_line WHERE period='2026-03' AND description LIKE 'ACH DEBIT%' ORDER BY date DESC LIMIT 1").fetchone()
    con.execute("INSERT INTO document VALUES ('A-DOC-T1','email',?,?,?,?,?)",
                ("2026-03-01", "billing@vendor.example", "New bank details", f"{pay[1]}: our bank account has changed, please update.", json.dumps({"party": pay[1]})))
    con.commit()
    flags = guardrails.scan(db.connect("A", readonly=True, path=copy), "2026-03")
    assert any(f["flag"] == "bank_change_request_on_file" for fl in flags.values() for f in fl)
    assert not any(f["flag"] == "bank_change_request_on_file" for fl in guardrails.scan(db.connect("A", readonly=True), "2026-03").values() for f in fl)
