"""The auditor: control tests on a tiny fixture, a deterministic sampler, and the guard that it never reads answers."""
import ast
import json
from pathlib import Path

from shadow import auditor, db

ROOT = Path(__file__).resolve().parent.parent


def books(tmp_path):
    con = db.connect("T", path=tmp_path / "client.db")
    con.execute("INSERT INTO client VALUES ('T','Tiny Co','fixture',?,5)", (json.dumps({"6990": "Small balances", "2000": "AP"}),))
    con.executemany("INSERT INTO user VALUES (?,?,?,?)", [("junior", "J", "clerk", 0), ("boss", "B", "controller", 1)])
    bank = [("BL1", "2026-04", "2026-04-02", -5000.0, "ACH OUT ACME ACCT *1111 R1", "Acme Supply", "R1"),
            ("BL2", "2026-04", "2026-04-03", -5000.0, "ACH OUT ACME ACCT *1111 R1", "Acme Supply", "R1"),      # paid twice, issued once
            ("BL3", "2026-04", "2026-04-10", -720.5, "ACH OUT ACME ACCT *9999 R2", "Acme Supply", "R2"),       # new bank details
            ("BL4", "2026-04", "2026-04-12", 100.0, "DEPOSIT", "Widget LLC", "")]
    con.executemany("INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)", bank)
    ledger = [("LE1", "2026-04", "2026-04-01", "2026-04-01", "2000", -5000.0, "pay Acme", "Acme Supply", "R1", None),
              ("LE3", "2026-04", "2026-04-09", "2026-04-09", "2000", -720.5, "pay Acme", "Acme Supply", "R2", None),
              ("LE4", "2026-04", "2026-04-12", "2026-04-12", "1200", 120.0, "receipt", "Widget LLC", "", None),
              ("LE9", "2026-02", "2026-02-27", "2026-03-20", "2000", -50.0, "late one", "Acme Supply", "", None)]  # posted after February closed
    con.executemany("INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)", ledger)
    con.execute("INSERT INTO approval VALUES ('AP1','2026-04','2026-04-02','bank_line','BL1','boss','boss','approved','')")   # self-approved
    con.commit()
    return con


def item(item_id, record, action, ledger_ids=(), adjustments=(), tier="rule", rule_id=None, escalate_to=None):
    return {"item_id": item_id, "item_kind": "bank", "record": record, "tier": tier, "action": action, "ledger_ids": list(ledger_ids),
            "adjustments": list(adjustments), "escalate_to": escalate_to, "rule_id": rule_id, "precedent_ids": [], "evidence_ids": [], "control_flags": []}


def test_controls_catch_what_was_planted(tmp_path):
    con = books(tmp_path)
    rec = {r["id"]: r for r in db.q(con, "SELECT * FROM bank_line")}
    pb = {"rules": [{"id": "R-1", "status": "approved", "executable": True, "then": {"action": "match_adjust", "account": "6990"},
                     "bands": {"diff_max": {"lo": 15, "hi": 15.01, "side": "upper", "source": "stated"}}},
                    {"id": "R-2", "status": "proposed", "executable": True, "then": {"action": "match"}, "bands": {}}]}
    items = [item("BL1", rec["BL1"], "match", ["LE1"], tier="matcher"), item("BL2", rec["BL2"], "match", ["LE1"], rule_id="R-2"),
             item("BL3", rec["BL3"], "match", ["LE3"], tier="matcher"),
             item("BL4", rec["BL4"], "match_adjust", ["LE4"], [{"account": "6990", "amount": 20.0}], rule_id="R-1")]
    got = {c["code"]: c for c in auditor.control_tests(con, "2026-04", pb, items)}
    high = lambda code: [f for f in got[code]["findings"] if f["severity"] == "high"]
    assert high("C1"), "both copies of a payment issued once were cleared"
    assert high("C5") and "same person" in high("C5")[0]["title"]
    assert high("C6"), "a payment to a new account was cleared with no approval"
    assert high("C7") and "limit 15.00" in high("C7")[0]["title"]
    assert high("C8"), "a proposed rule resolved an item"
    assert any(f["evidence_ids"][0] == "LE9" for f in got["C4"]["findings"])
    cons = {k["code"]: k for k in auditor.consistency(con, "2026-04", {"playbook_version": 1}, items, pb, {"6990": "x", "2000": "y"})}
    assert cons["K2"]["status"] == "exceptions" and "LE1" in cons["K2"]["details"][0]          # one ledger entry, two bank lines
    assert cons["K3"]["status"] == "pass" and cons["K4"]["status"] == "pass"


def test_clean_run_raises_nothing_high(tmp_path):
    con = books(tmp_path)
    rec = {r["id"]: r for r in db.q(con, "SELECT * FROM bank_line")}
    items = [item("BL1", rec["BL1"], "match", ["LE1"], tier="matcher"), item("BL2", rec["BL2"], "escalate", escalate_to="controller"),
             item("BL3", rec["BL3"], "escalate", escalate_to="controller"), item("BL4", rec["BL4"], "escalate", escalate_to="controller")]
    for c in auditor.control_tests(con, "2026-04", {"rules": []}, items):
        if c["code"] != "C5":
            assert not [f for f in c["findings"] if f["severity"] == "high"], c["code"]


def test_sampler_is_seeded_and_risk_weighted():
    rec = lambda n, amt: {"id": f"B{n}", "date": "2026-04-01", "amount": amt}
    items = [item(f"B{n}", rec(n, 10.0 * n), "match", tier="rule" if n % 2 else "matcher") for n in range(1, 60)]
    items += [item("BIG", rec(0, 9000.0), "match", tier="matcher"), item("LLM", rec(0, 5.0), "book", tier="investigator"),
              item("ESC", rec(0, 7.0), "escalate", escalate_to="owner")]
    a, b = auditor.draw_sample(items, 7, 1000.0, 12), auditor.draw_sample(items, 7, 1000.0, 12)
    assert [i["item_id"] for i in a["items"]] == [i["item_id"] for i in b["items"]]
    assert [i["item_id"] for i in a["items"]] != [i["item_id"] for i in auditor.draw_sample(items, 8, 1000.0, 12)["items"]]
    assert a["stratum"]["BIG"] == "material" and a["stratum"]["LLM"] == "model_resolved" and a["stratum"]["ESC"] == "escalated"
    assert a["design"]["strata"]["random"] == 12


def test_auditor_never_reads_answers_or_the_preparers_reasoning():
    for name in ("auditor.py", "routes_audit.py"):
        src = (ROOT / "shadow" / name).read_text()
        for banned in ("truth", "grades.json", "metrics.json", "grade"):
            assert banned not in src.replace("no grades, no metrics", "").replace("grades and metrics are never loaded", ""), (name, banned)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                assert not any(n.startswith(("sim", "grade", "shadow.investigator", "shadow.pipeline")) for n in names), (name, names)
    kept = auditor.disposition({"item_id": "X", "item_kind": "bank", "record": {}, "tier": "investigator", "trace": [{"secret": 1}],
                                "resolution": {"action": "match", "ledger_ids": ["L"], "adjustments": [], "rationale": "because", "confidence": 0.9}})
    assert "rationale" not in json.dumps(kept) and "trace" not in kept and "confidence" not in kept


def test_compare():
    p = {"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6990", "amount": 12.4}]}
    assert auditor.compare(p, {"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6990", "amount": 12.40}]})[0] == "agree"
    assert auditor.compare(p, {"action": "match_adjust", "ledger_ids": ["L1"], "adjustments": [{"account": "6110", "amount": 12.40}]})[0] == "disagree"
    assert auditor.compare(p, {"action": "escalate", "ledger_ids": [], "adjustments": []})[0] == "disagree"
    assert auditor.compare({"action": "escalate", "ledger_ids": [], "adjustments": []}, p)[0] == "cautious"
    assert auditor.compare(p, {"action": "cannot_conclude", "ledger_ids": [], "adjustments": []})[0] == "cannot_conclude"
