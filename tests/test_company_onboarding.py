"""The onboarding path, end to end, with no model calls.

Client A's built history doubles as a known-good fixture: exporting it to CSV and importing it
back as a different company exercises every coercion, id and reference rule, and the reconstructed
trail can be compared against the original.
"""
import csv
import io
import json
import shutil
import sqlite3

import pytest

from shadow import db, history
from shadow.onboard import coldstart, company, contract, ids, importer, mapping, preflight, staging

pytestmark = pytest.mark.skipif(not db.db_path("A").exists(),
                                reason="run `uv run python -m sim.build` first")

CO = "TESTCO"
ROSTER = [{"id": "ana", "name": "Ana Diaz", "role": "bookkeeper", "senior": False},
          {"id": "sam", "name": "Sam Okafor", "role": "controller", "senior": True}]


def make(client=CO, **kw):
    shutil.rmtree(db.DATA / client, ignore_errors=True)
    args = {"name": "Test Co", "blurb": "A wholesaler.",
            "chart": {"1010": "Cash", "1200": "AR", "6110": "Bank fees", "6990": "Over/short"},
            "users": ROSTER, "reconciler": "ana"}
    args.update(kw)
    return company.create(client, **args)


def csv_bytes(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def do_import(client, role, header, rows, mapping_override=None):
    up = staging.store(client, role, f"{role}.csv", csv_bytes(header, rows))
    m = mapping_override or mapping.guess(role, up["columns"])
    return importer.run_import(client, up["upload_id"], m), up, m


# --- coercion ---------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("$1,234.56", 1234.56), ("(89.00)", -89.0), ("1.234,56", 1234.56), ("12.00-", -12.0),
    ("-45", -45.0), ("  $ 2,000 ", 2000.0), ("1,50", 1.5), ("2,500", 2500.0),
])
def test_amounts_from_real_exports(raw, want):
    assert contract.to_amount(raw) == want


def test_date_order_is_decided_per_file_not_guessed_per_row():
    assert contract.sniff_dayfirst(["14/04/2026", "25/12/2025"]) is True
    assert contract.sniff_dayfirst(["04/14/2026", "12/25/2025"]) is False
    assert contract.sniff_dayfirst(["01/02/2026"]) is None      # genuinely ambiguous, so we ask
    assert contract.to_date("01/02/2026", dayfirst=True) == "2026-02-01"
    assert contract.to_date("01/02/2026", dayfirst=False) == "2026-01-02"


def test_all_three_sign_conventions():
    signed = {"columns": {"amount": "amt"}, "sign": {"mode": "signed"}}
    assert contract.resolve_amount({"amt": "-18.50"}, signed) == -18.5
    inverted = {"columns": {"amount": "amt"}, "sign": {"mode": "signed", "invert": True}}
    assert contract.resolve_amount({"amt": "18.50"}, inverted) == -18.5
    dc = {"columns": {}, "sign": {"mode": "debit_credit", "debit_column": "out",
                                  "credit_column": "in", "credit_is_money_in": True}}
    assert contract.resolve_amount({"out": "", "in": "1200"}, dc) == 1200.0
    assert contract.resolve_amount({"out": "18.50", "in": ""}, dc) == -18.5
    flag = {"columns": {"amount": "amt"}, "sign": {"mode": "flag", "flag_column": "dc",
                                                   "flag_money_out": ["DR"]}}
    assert contract.resolve_amount({"amt": "18.50", "dc": "DR"}, flag) == -18.5
    assert contract.resolve_amount({"amt": "18.50", "dc": "CR"}, flag) == 18.5


def test_a_row_with_both_debit_and_credit_is_reported_not_guessed():
    dc = {"columns": {}, "sign": {"mode": "debit_credit", "debit_column": "out", "credit_column": "in"}}
    with pytest.raises(contract.Bad):
        contract.resolve_amount({"out": "10", "in": "20"}, dc)


# --- company ----------------------------------------------------------------------------------
def test_company_without_a_senior_is_refused():
    with pytest.raises(ValueError, match="senior"):
        make(users=[{"id": "ana", "name": "Ana", "role": "bookkeeper", "senior": False}])


def test_company_id_is_validated_before_it_reaches_the_filesystem():
    assert not company.valid_id("../../etc")
    assert not company.valid_id("waytoolongforthis")
    assert company.valid_id("NWIND")
    with pytest.raises(ValueError):
        make(client="../escape")


def test_creating_a_company_does_not_delete_an_existing_database():
    make()
    do_import(CO, "bank_lines", ["date", "amount", "description"],
              [["2026-04-01", "100.00", "A DEPOSIT"]])
    company.create(CO, name="Test Co", blurb="Renamed.", chart={"1010": "Cash"},
                   users=ROSTER, reconciler="ana")
    con = db.connect(CO, readonly=True)
    assert con.execute("SELECT COUNT(*) FROM bank_line").fetchone()[0] == 1
    con.close()


# --- import -----------------------------------------------------------------------------------
def test_import_derives_period_and_fills_posting_date():
    make()
    rep, _, _ = do_import(CO, "ledger_entries", ["date", "account", "amount", "memo"],
                          [["2026-04-14", "1200", "500.00", "Expected receipt"]])
    assert rep["posted_at_defaulted"] == 1
    assert any("late" in w for w in rep["warnings"])
    con = db.connect(CO, readonly=True)
    row = db.q(con, "SELECT * FROM ledger_entry")[0]
    con.close()
    assert row["period"] == "2026-04" and row["posted_at"] == "2026-04-14"


def test_reimporting_the_same_file_keeps_ids_stable():
    make()
    header = ["date", "amount", "description", "ref"]
    rows = [["2026-04-01", "100.00", "DEPOSIT", "R1"], ["2026-04-02", "-18.50", "FEE", "R2"]]
    first, up, m = do_import(CO, "bank_lines", header, rows)
    assert first["inserted"] == 2
    second = importer.run_import(CO, up["upload_id"], m)
    assert second["inserted"] == 0 and second["updated"] == 2
    assert first["ids"] == second["ids"], "ids must survive a re-upload or the playbook is orphaned"


def test_a_link_takes_the_bank_lines_period_not_the_ledgers():
    """A ledger entry from March cleared by an April bank line belongs to April, because the trail
    is read by bank line. Getting this wrong silently drops every carried-over item."""
    make()
    do_import(CO, "bank_lines", ["date", "amount", "description", "ref"],
              [["2026-04-02", "500.00", "DEPOSIT", "B1"]])
    do_import(CO, "ledger_entries", ["date", "posted_at", "account", "amount", "memo", "ref"],
              [["2026-03-28", "2026-03-28", "1200", "500.00", "Expected", "L1"]])
    do_import(CO, "reconciliations",
              ["bank_ref", "ledger_ref", "reconciled_by", "reconciled_at"],
              [["B1", "L1", "ana", "2026-04-03"]])
    con = db.connect(CO, readonly=True)
    link = db.q(con, "SELECT * FROM reconcile_link")[0]
    con.close()
    assert link["period"] == "2026-04"
    assert link["amount"] == 500.0


def test_a_reference_that_resolves_to_nothing_is_reported_not_written():
    make()
    do_import(CO, "bank_lines", ["date", "amount", "description", "ref"],
              [["2026-04-02", "500.00", "DEPOSIT", "B1"]])
    rep, _, _ = do_import(CO, "adjustments",
                          ["bank_ref", "account", "amount", "posted_by", "posted_at"],
                          [["NOPE", "6110", "5.00", "ana", "2026-04-03"]])
    assert rep["inserted"] == 0
    assert rep["unresolved_references"]
    assert any("not imported yet" in w for w in rep["warnings"])


def test_documents_carry_the_period_internal_mail_is_selected_on():
    make()
    do_import(CO, "documents", ["type", "date", "body", "subject"],
              [["internal_email", "2026-04-05", "Write it off", "the Brightwater short pay"]])
    con = db.connect(CO, readonly=True)
    doc = db.q(con, "SELECT * FROM document")[0]
    con.close()
    assert doc["meta"]["period"] == "2026-04"
    assert history.internal_mail(db.connect(CO, readonly=True), "2026-05")


# --- preflight --------------------------------------------------------------------------------
def test_preflight_blocks_when_nothing_records_a_decision():
    make()
    do_import(CO, "bank_lines", ["date", "amount", "description"],
              [["2026-04-01", "100.00", "DEPOSIT"]])
    out = preflight.run(CO)
    assert not out["ready"]
    assert any(c["check"] == "reconciliation trail" and c["level"] == "block" for c in out["checks"])


def test_preflight_names_a_missing_senior_as_the_blocking_problem():
    make()
    con = db.connect(CO, readonly=False)
    con.execute("UPDATE user SET senior=0")
    con.commit()
    con.close()
    out = preflight.run(CO)
    blocking = [c for c in out["checks"] if c["level"] == "block"]
    assert any(c["check"] == "senior roles" for c in blocking)


def test_preflight_costs_nothing_and_reports_a_price():
    out = preflight.run("A")
    assert out["ready"] is True
    assert out["estimate"]["usd"] > 0
    assert out["clusters"] > 0


# --- cold start -------------------------------------------------------------------------------
def build_trailless_copy(client="COLDCO"):
    """Client A's transactions with every trace of what the humans did removed."""
    make(client=client, name="Cold Co", blurb="Vending and laundry routes.",
         chart={"1010": "Cash", "4000": "Sales", "6990": "Over/short"})
    src = sqlite3.connect(db.db_path("A"))
    src.row_factory = sqlite3.Row
    dst = db.connect(client, readonly=False)
    for table, n in (("bank_line", 7), ("ledger_entry", 10)):
        rows = [tuple(r) for r in src.execute(f"SELECT * FROM {table}")]
        dst.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * n)})", rows)
    dst.commit()
    dst.close()
    src.close()
    return client


def test_cold_start_derives_the_routine_matches_and_leaves_the_exceptions():
    c = build_trailless_copy()
    before = coldstart.residue(c, limit=0)
    out = coldstart.derive_links(c, "ana")
    after = coldstart.residue(c, limit=0)
    assert out["derived"] > 0
    assert after["total"] < before["total"], "deriving should shrink what a person has to work"
    con = db.connect(c, readonly=True)
    derived = db.q(con, "SELECT * FROM reconcile_link LIMIT 5")
    con.close()
    assert all(f"-{coldstart.AUTO_PREFIX}-" in r["id"] for r in derived), "derived links stay labelled"
    assert all(r["reconciled_by"] == "ana" for r in derived)


def test_derived_timestamps_are_spread_so_routine_work_still_reads_as_routine():
    """One shared migration date would make every item look like it took months, which collapses
    every learned band and sends the whole queue to a human."""
    c = build_trailless_copy("COLDT")
    coldstart.derive_links(c, "ana")
    con = db.connect(c, readonly=True)
    whens = [r["reconciled_at"] for r in db.q(con, "SELECT reconciled_at FROM reconcile_link")]
    observed = history.observe(con, "2026-04")
    con.close()
    assert len(set(whens)) > 5
    assert sum(bool(o["routine"]) for o in observed) > 0, "derived links must read as routine handling"


def test_a_trail_of_only_derived_links_is_refused_because_it_teaches_nothing():
    c = build_trailless_copy("COLDB")
    coldstart.derive_links(c, "ana")
    out = preflight.run(c)
    assert not out["ready"]
    assert any(c_["check"] == "decisions of your own" and c_["level"] == "block"
               for c_ in out["checks"])


def test_labelling_one_exception_writes_the_trail_and_opens_the_gate():
    c = build_trailless_copy("COLDL")
    coldstart.derive_links(c, "ana")
    res = coldstart.residue(c, limit=5)
    item = next(i for i in res["items"] if i["candidates"])
    cand = item["candidates"][0]
    out = coldstart.label(c, item["item"]["id"], "match_adjust", ledger_ids=[cand["id"]],
                          adjustments=[{"account": "6990", "amount": cand["difference"]}],
                          handled_by="ana", days_to_handle=1)
    assert out["written"]["links"] == 1 and out["written"]["journal_entries"] == 1
    assert preflight.run(c)["ready"] is True


def test_labelling_rejects_adjustments_that_do_not_account_for_the_difference():
    c = build_trailless_copy("COLDX")
    coldstart.derive_links(c, "ana")
    res = coldstart.residue(c, limit=5)
    item = next(i for i in res["items"] if i["candidates"])
    cand = item["candidates"][0]
    with pytest.raises(ValueError, match="adjustments sum"):
        coldstart.label(c, item["item"]["id"], "match_adjust", ledger_ids=[cand["id"]],
                        adjustments=[{"account": "6990", "amount": 0.01}], handled_by="ana")


def test_a_senior_sign_off_is_recorded_as_an_approval():
    c = build_trailless_copy("COLDS")
    res = coldstart.residue(c, limit=3)
    item = res["items"][0]
    coldstart.label(c, item["item"]["id"], "escalate", handled_by="ana",
                    senior_signed_off=True, escalate_to="controller", days_to_handle=4)
    con = db.connect(c, readonly=True)
    ap = db.q(con, "SELECT * FROM approval")
    observed = {o["item"]["id"]: o for o in history.observe(con, "2026-05")}
    con.close()
    assert ap and ap[0]["approver"] == "sam"
    assert observed[item["item"]["id"]]["outcome"]["action"] == "escalate"


# --- round trip -------------------------------------------------------------------------------
def test_client_a_exported_and_reimported_reproduces_its_own_trail():
    """The strongest check available without a model: take a history whose right answer is known,
    push it through the real importer, and see whether the agent reads back the same story."""
    src = sqlite3.connect(db.db_path("A"))
    src.row_factory = sqlite3.Row
    rt = "RT"
    make(client=rt, name="Round Trip", blurb="Copy of A.",
         chart=json.loads(src.execute("SELECT chart FROM client").fetchone()[0]),
         users=[dict(r) | {"senior": bool(r["senior"])}
                for r in src.execute("SELECT id, name, role, senior FROM user")],
         reconciler=src.execute("SELECT id FROM user WHERE senior=0 LIMIT 1").fetchone()[0])

    bank = list(src.execute("SELECT * FROM bank_line"))
    do_import(rt, "bank_lines", ["date", "amount", "description", "counterparty", "ref", "source_id"],
              [[r["date"], r["amount"], r["description"], r["counterparty"], r["ref"], r["id"]] for r in bank])
    led = list(src.execute("SELECT * FROM ledger_entry"))
    do_import(rt, "ledger_entries",
              ["date", "posted_at", "account", "amount", "memo", "counterparty", "ref", "source_id"],
              [[r["date"], r["posted_at"], r["account"], r["amount"], r["memo"], r["counterparty"],
                r["ref"], r["id"]] for r in led])
    links = list(src.execute("SELECT * FROM reconcile_link WHERE undone_at IS NULL"))
    do_import(rt, "reconciliations", ["bank_ref", "ledger_ref", "reconciled_by", "reconciled_at"],
              [[r["bank_id"], r["ledger_id"], r["reconciled_by"], r["reconciled_at"]] for r in links])
    jes = list(src.execute("SELECT * FROM journal_entry WHERE bank_id IS NOT NULL"))
    do_import(rt, "adjustments", ["bank_ref", "account", "amount", "posted_by", "posted_at", "memo", "date"],
              [[r["bank_id"], r["account"], r["amount"], r["posted_by"], r["posted_at"], r["memo"],
                r["date"]] for r in jes])
    src.close()

    a_con, rt_con = db.connect("A", readonly=True), db.connect(rt, readonly=True)
    try:
        a_obs = history.observe(a_con, "2026-04")
        rt_obs = history.observe(rt_con, "2026-04")
        a_routine = sum(bool(o["routine"]) for o in a_obs)
        rt_routine = sum(bool(o["routine"]) for o in rt_obs)
        a_actions = _actions(a_obs)
        rt_actions = _actions(rt_obs)
    finally:
        a_con.close()
        rt_con.close()

    assert rt_routine == pytest.approx(a_routine, rel=0.02), "routine split must survive the round trip"
    for action in ("match", "match_adjust", "escalate"):
        assert rt_actions.get(action, 0) == pytest.approx(a_actions.get(action, 0), rel=0.05), \
            f"{action} count drifted: {rt_actions} vs {a_actions}"
    assert preflight.run(rt)["ready"] is True


def _actions(observed):
    out: dict = {}
    for o in observed:
        if o["outcome"]:
            out[o["outcome"]["action"]] = out.get(o["outcome"]["action"], 0) + 1
    return out
