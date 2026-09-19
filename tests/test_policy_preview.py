"""No-model integration tests for preview, authority, replay and evidence lifecycle."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from shadow import correct, db, llm, pipeline, playbook, preview, rules, server, stale, unlearn


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.setattr(db, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(server, "CLIENTS", ("T",))
    preview.PREVIEWS.clear()
    con = db.connect("T")
    con.execute("INSERT INTO client VALUES ('T','Test company','Test fixture','{}',3)")
    con.execute("INSERT INTO user VALUES ('u1','Controller','controller',1)")
    con.execute("INSERT INTO user VALUES ('u2','Clerk','clerk',0)")
    for i, amount in enumerate((8, 12.4, 25)):
        con.execute("INSERT INTO bank_line VALUES (?, '2026-03','2026-03-10',?,'Service fee','Bank','')", (f"B{i}", -amount))
    con.commit()
    rule = {"id": "T-R-001", "status": "approved", "executable": True, "text": "Book fees up to 10.",
            "when": {"direction": "out", "amount_max": 10}, "then": {"action": "book", "account": "fees"},
            "bands": {"amount_max": {"lo": 10, "hi": 20, "side": "upper", "source": "trail", "n_known": 3}},
            "open_question": None, "backtest": {"conflicts": 0}}
    playbook.save("T", "dev", {"trained_before": "2026-03", "rules": [rule]}, {"type": "induction"})
    def no_llm(*a, **kw):
        pytest.fail("No model call is allowed")
    monkeypatch.setattr(llm, "call", no_llm)
    yield con, TestClient(server.app)
    con.close()
    preview.PREVIEWS.clear()


def payload(**kw):
    return dict(client="T", track="dev", period="2026-03", rule_id="T-R-001", condition="amount_max",
                answer="limit", limit=15, role="controller") | kw


def test_preview_is_read_only_and_apply_undo_is_complete(sandbox):
    con, api = sandbox
    old = playbook.load("T", "dev")
    response = api.post("/api/playbook/preview-band", json=payload())
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["before"]["automatic_exceptions"] == 1
    assert report["after"]["automatic_exceptions"] == 2
    assert report["after"]["needs_review"] == 1
    assert report["changed_items"][0]["item_id"] == "B1"
    assert report["llm_calls"] == 0
    assert playbook.load("T", "dev") == old
    assert not correct.log_path("T", "dev").exists()
    assert not db.RUNS.exists()
    applied = api.post("/api/playbook/apply-preview", json={"preview_id": report["preview_id"], "role": "controller"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["new_version"] == 2
    pipeline.run("T", "2026-03", "corrected", "dev", use_llm=False, run_id="approved")
    undo = api.post("/api/retract", json={"client": "T", "track": "dev", "role": "controller", "correction_id": applied.json()["correction_id"]})
    assert undo.status_code == 200, undo.text
    assert playbook.load("T", "dev")["rules"] == old["rules"]
    assert {it["item_id"] for it in undo.json()["reopened"]} == {"B1"}
    version = playbook.load("T", "dev")["version"]
    again = api.post("/api/retract", json={"client": "T", "track": "dev", "role": "controller", "correction_id": applied.json()["correction_id"]})
    assert again.json()["already_retracted"]
    assert playbook.load("T", "dev")["version"] == version
    assert api.post("/api/playbook/apply-preview", json={"preview_id": report["preview_id"], "role": "controller"}).status_code == 409


@pytest.mark.parametrize("mutation", ["ledger", "policy"])
def test_stale_preview_rejected(sandbox, mutation):
    con, api = sandbox
    report = api.post("/api/playbook/preview-band", json=payload()).json()
    if mutation == "ledger":
        con.execute("UPDATE bank_line SET amount=-13 WHERE id='B1'")
        con.commit()
    else:
        pb = playbook.load("T", "dev")
        playbook.save("T", "dev", pb, {"type": "test"})
    result = api.post("/api/playbook/apply-preview", json={"preview_id": report["preview_id"], "role": "controller"})
    assert result.status_code == 409
    assert not correct.log_path("T", "dev").exists()


@pytest.mark.parametrize("role", [None, "clerk", "invented"])
def test_no_missing_or_junior_teaching_authority(sandbox, role):
    con, api = sandbox
    assert api.post("/api/playbook/preview-band", json=payload(role=role)).status_code == 403
    result = api.post("/api/playbook/answer-band", json=payload(role=role))
    assert result.json()["diff"] is None
    assert playbook.load("T", "dev")["version"] == 1
    assert api.post("/api/playbook/answer", json={"client": "T", "track": "dev", "rule_id": "T-R-001", "answer": "Approve", "role": role}).status_code == 403
    assert api.post("/api/conflicts/unknown", json={"client": "T", "track": "dev", "outcome": "policy_change", "role": role}).status_code == 403
    assert api.post("/api/retract", json={"client": "T", "track": "dev", "correction_id": "unknown", "role": role}).status_code == 403


@pytest.mark.parametrize("value", [5, 10, 20, 25, float("inf"), float("nan")])
def test_band_values_must_be_finite_and_inside_current_band(sandbox, value):
    pb = playbook.load("T", "dev")
    before = copy.deepcopy(pb)
    with pytest.raises(ValueError):
        playbook.apply_band_answer(pb, "T-R-001", "amount_max", value, review=False)
    assert pb == before


def test_equal_numbers_do_not_couple_unrelated_scopes(sandbox):
    pb = playbook.load("T", "dev")
    unrelated = copy.deepcopy(pb["rules"][0])
    unrelated.update(id="T-R-002", when={"counterparty_regex": "Other", "amount_min": 20}, then={"action": "escalate"})
    unrelated["bands"] = {"amount_min": {"lo": 10, "hi": 20, "side": "lower"}}
    pb["rules"].append(unrelated)
    before = copy.deepcopy(unrelated)
    playbook.apply_band_answer(pb, "T-R-001", "amount_max", 15, review=False)
    assert unrelated == before


def test_conflicting_approved_rules_abstain(sandbox):
    con, api = sandbox
    r = playbook.load("T", "dev")["rules"][0]
    other = copy.deepcopy(r)
    other.update(id="T-R-002", then={"action": "book", "account": "other"})
    item = db.q(con, "SELECT * FROM bank_line WHERE id='B0'")[0]
    out = rules.apply({"rules": [r, other]}, item, "bank", rules.Ctx(con, "2026-03", [], set()))[1]
    assert out["defer"] and out["reason"] == "conflicting_precedents"


def test_replay_preserves_validated_approval_and_effective_date(sandbox):
    rule = playbook.load("T", "dev")["rules"][0]
    patch = {"kind": "ops", "origin": "correction C1", "ops": [{"op": "modify", "rule_id": rule["id"], "text": "Updated"}],
             "safety": {rule["id"]: {"status": "proposed", "human_confirmed": False, "awaiting_senior": True,
                                    "open_question": "Senior approval required", "valid_from": "2026-03-20"}}}
    pb, notes = unlearn._replay("T", {"rules": [rule]}, [{"correction_id": "C1", "patch": patch}], None)
    assert pb["rules"][0]["status"] == "proposed"
    assert pb["rules"][0]["valid_from"] == "2026-03-20"
    assert pb["rules"][0]["awaiting_senior"]


def test_policy_metadata_changes_are_visible(sandbox):
    pb = playbook.load("T", "dev")
    after = copy.deepcopy(pb)
    after["rules"][0]["valid_from"] = "2026-03-20"
    assert playbook.diff(pb, after)["changed"]


def test_no_support_band_never_executes(sandbox):
    con, api = sandbox
    pb = playbook.load("T", "dev")
    pb["rules"][0]["bands"]["amount_max"]["n_known"] = 0
    item = db.q(con, "SELECT * FROM bank_line WHERE id='B0'")[0]
    assert rules.apply(pb, item, "bank", rules.Ctx(con, "2026-03", [], set()))[1]["defer"]


@pytest.mark.parametrize("mutation", ["document", "link", "reversal", "bank", "new_entry", "harmless"])
def test_evidence_lifecycle(sandbox, mutation):
    con, api = sandbox
    con.execute("INSERT INTO ledger_entry VALUES ('L1','2026-03','2026-03-10','2026-03-10','cash',-8,'Payment','Bank','','')")
    con.execute("INSERT INTO document VALUES ('D1','remittance','2026-03-10','Bank','Payment','Original','{}')")
    con.execute("INSERT INTO reconcile_link VALUES ('R1','2026-03','B0','L1',-8,'u1','2026-03-10',NULL)")
    con.execute("INSERT INTO journal_entry VALUES ('J1','2026-03','2026-03-10','2026-03-10','u1','fees',8,'Fee','B0',NULL)")
    con.commit()
    folder = db.RUNS / "test"
    folder.mkdir(parents=True)
    resolution = {"action": "match", "ledger_ids": ["L1"], "evidence_ids": ["D1"]}
    fp = stale.fingerprint(con, resolution, {"id": "B0"})
    (folder / "run.json").write_text(json.dumps({"period": "2026-03", "created_at": "2026-03-10T12:00:00", "ledger_snapshot_ids": ["L1"]}))
    (folder / "resolutions.jsonl").write_text(json.dumps({"item_id": "B0", "record": {}, "resolution": resolution, "evidence_fingerprint": fp}))
    if mutation == "document": con.execute("UPDATE document SET body='Withdrawn'")
    if mutation == "link": con.execute("UPDATE reconcile_link SET undone_at='2026-03-11'")
    if mutation == "reversal": con.execute("INSERT INTO journal_entry VALUES ('J2','2026-03','2026-03-11','2026-03-11','u1','fees',-8,'Reversal',NULL,'J1')")
    if mutation == "bank": con.execute("UPDATE bank_line SET amount=-9 WHERE id='B0'")
    if mutation == "new_entry": con.execute("INSERT INTO ledger_entry SELECT 'L2',period,date,posted_at,account,amount,memo,counterparty,ref,invoice_id FROM ledger_entry WHERE id='L1'")
    if mutation == "harmless": con.execute("UPDATE ledger_entry SET memo='Spelling correction'")
    con.commit()
    result = stale.verify("T", "test")
    assert bool(result["stale"]) == (mutation not in {"new_entry", "harmless"})
    assert bool(result["posted_after_reconciliation"]) == (mutation == "new_entry")


def test_lower_escalation_band_answer_moves_review_boundary(sandbox):
    pb = playbook.load("T", "dev")
    r = pb["rules"][0]
    r["when"] = {"amount_min": 20}
    r["then"] = {"action": "escalate", "escalate_to": "controller"}
    r["bands"] = {"amount_min": {"lo": 10, "hi": 20, "side": "lower"}}
    playbook.apply_band_answer(pb, r["id"], "amount_min", 15, review=True)
    assert r["bands"]["amount_min"]["hi"] == 15


def test_bank_change_is_hard_hold_even_if_model_would_match(sandbox):
    from shadow import investigator
    result = investigator.finalize({"action": "match", "ledger_ids": ["L1"], "confidence": 1}, None,
                                   [{"flag": "bank_change_request_on_file", "detail": "unverified"}], ["controller"])
    assert result["action"] == "escalate"
    assert result["ledger_ids"] == []
    assert result["reason"] == "fraud_shaped"


def test_bank_change_hold_needs_senior_verification(sandbox):
    from shadow import guardrails
    con, api = sandbox
    con.execute("INSERT INTO document VALUES ('D1','email','2026-03-01','Bank','New bank details','Our account changed.',?)", (json.dumps({"party": "Bank"}),))
    assert len(guardrails.scan(con, "2026-03")) == 3
    con.execute("INSERT INTO approval VALUES ('AP1','2026-03','2026-03-09','Bank change','D1','u2','u2','approved','Verified')")
    assert len(guardrails.scan(con, "2026-03")) == 3
    con.execute("UPDATE approval SET approver='u1'")
    assert not guardrails.scan(con, "2026-03")


def test_patch_retraction_skips_dependent_edits(sandbox):
    added = {"op": "add", "assigned_id": "T-R-002", "text": "New rule", "executable": True,
             "when": {"amount_max": 5}, "then": {"action": "book", "account": "fees"}}
    causes = [{"correction_id": "C1", "patch": {"kind": "ops", "origin": "correction C1", "ops": [added]}},
              {"correction_id": "C2", "patch": {"kind": "ops", "origin": "correction C2", "ops": [
                  {"op": "modify", "rule_id": "T-R-002", "text": "Edited"}]}}]
    result, notes = unlearn._replay("T", {"rules": []}, causes, "C1")
    assert result["rules"] == [] and any("C2" in n for n in notes)


def test_retraction_keeps_prior_evidence_exclusions(sandbox, monkeypatch):
    con, api = sandbox
    correct.answer_band("T", "dev", "T-R-001", "amount_max", limit=15, role="controller")
    current = playbook.load("T", "dev")
    cid = current["cause"]["correction_id"]
    current["excluded_precedents"] = ["withdrawn"]
    playbook.save("T", "dev", current, {"type": "one_off_exception"})
    seen = []
    def check_exclusions(con, pb):
        seen.extend(pb.get("excluded_precedents", []))
        return pb
    monkeypatch.setattr(playbook, "backtest", check_exclusions)
    unlearn.retract("T", "dev", correction_id=cid)
    assert "withdrawn" in seen
    assert playbook.load("T", "dev")["excluded_precedents"] == ["withdrawn"]


def test_answer_updates_the_rule_sentence_and_condition(sandbox):
    pb = playbook.load("T", "dev")
    playbook.apply_band_answer(pb, "T-R-001", "amount_max", 15, review=False)
    assert pb["rules"][0]["when"]["amount_max"] == 15
    assert "15.00" in pb["rules"][0]["text"]


def test_partial_rerun_still_sees_other_ledger_claimants(sandbox):
    con, api = sandbox
    for iid, amount in [("X", 90), ("Y", 85)]:
        con.execute("INSERT INTO bank_line VALUES (?, '2026-03','2026-03-10',?,'Receipt','Customer','SHARED')", (iid, amount))
    con.execute("INSERT INTO ledger_entry VALUES ('L1','2026-03','2026-03-09','2026-03-09','cash',100,'Invoice','Customer','SHARED','')")
    con.commit()
    pb = {"version": 1, "rules": [{"id": "T-R-001", "text": "Adjust a shortfall", "status": "approved", "executable": True,
          "when": {"direction": "in", "candidate": {"by": "ref"}, "diff_abs_max": 20}, "then": {"action": "match_adjust", "account": "fees"}}]}
    result = pipeline.run("T", "2026-03", "corrected", "dev", use_llm=False, only={"X"},
                          persist=False, playbook_override=pb)
    assert [it["item_id"] for it in result["items"]] == ["X"]
    assert result["items"][0]["resolution"]["action"] == "escalate"
