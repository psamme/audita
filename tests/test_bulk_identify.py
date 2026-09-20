"""Dropping a folder in: what each file is, and importing them in an order that resolves.

The two failures worth catching here are quiet ones. A misread kind does not raise, it writes
ledger entries into the bank table and makes every later answer worse. A batch imported in the
order the person happened to select the files leaves every reconciliation row dangling, because
its bank and ledger references are resolved against records that are not there yet.
"""
import shutil

import pytest
from fastapi.testclient import TestClient

from shadow import db
from shadow.onboard import classify, company, staging

pytestmark = pytest.mark.skipif(not db.db_path("A").exists(),
                                reason="run `uv run python -m sim.build` first")

CO = "DROPCO"

# Deliberately not our column names, and deliberately not named after their kind: this is what a
# finance team's export folder looks like before anyone has been asked to tidy it.
FILES = {
    "export (3).csv": ("bank_lines",
        "Line Id,Posting Date,Details,Paid In,Paid Out,Balance\n"
        "B1,14/01/2026,CARD SETTLEMENT 88213,4102.55,,91233.10\n"
        "B2,15/01/2026,SUPPLIER PMT ACME,,1200.00,90033.10\n"),
    "download.csv": ("ledger_entries",
        "Line Id,Value Date,Nominal,Net,Narrative,Customer\n"
        "L001,2026-01-14,1200,4102.55,Card settlement,Acme\n"
        "L002,2026-01-15,2000,-1200.00,Supplier payment,Beta\n"),
    "sheet1.csv": ("reconciliations",
        "Statement Line,Journal Line,Cleared By,Matched On\n"
        "B1,L001,ana,2026-01-16\n"),
    "q1 output.csv": ("adjustments",
        "Bank Line,GL Account,Value,Entered By,Entry Date,Reason\n"
        "B2,6990,2.40,ana,2026-01-16,Short pay written off\n"),
    "untitled.csv": ("approvals",
        "Applies To,Approved By,Outcome,Date,Raised By\n"
        "B2,sam,approved,2026-01-20,ana\n"),
    "data.csv": ("invoices",
        "Invoice No,Invoice Date,Gross,Customer,Due Date,Payment Terms\n"
        "INV-1,2026-01-02,4102.55,Acme,2026-02-01,Net 30\n"),
    "mailbox.csv": ("documents",
        "Kind,Date,From,Title,Content\n"
        "remittance,2026-01-14,ap@acme.com,Payment advice,"
        "\"We have paid INV-1 in full, less a 2.40 short pay.\"\n"),
    "list.csv": ("people",
        "Employee Id,Full Name,Job Title,Can Approve\n"
        "ana,Ana Diaz,Bookkeeper,no\nsam,Sam Okafor,Controller,yes\n"),
    "file.csv": ("chart_of_accounts",
        "Account Code,Account Name\n1200,Accounts receivable\n6990,Over and short\n"),
}


def columns_of(text: str):
    parsed = staging.read(text.encode())
    return staging.profile(parsed["header"], parsed["rows"])


@pytest.fixture(scope="module")
def client():
    from shadow.app import app
    shutil.rmtree(db.DATA / CO, ignore_errors=True)
    c = TestClient(app)
    res = c.post("/api/onboarding/company", json={
        "id": CO, "name": "Drop Co", "blurb": "A wholesaler.",
        "chart": {"1010": "Cash", "1200": "AR", "2000": "AP", "6990": "Over and short"},
        "users": [{"id": "ana", "name": "Ana Diaz", "role": "bookkeeper", "senior": False},
                  {"id": "sam", "name": "Sam Okafor", "role": "controller", "senior": True}],
        "reconciler": "ana"})
    assert res.status_code == 200, res.text
    company.register(CO)
    yield c
    shutil.rmtree(db.DATA / CO, ignore_errors=True)


# --- naming the kind --------------------------------------------------------------------------
@pytest.mark.parametrize("filename", list(FILES))
def test_each_kind_is_recognised_from_its_columns_alone(filename):
    want, text = FILES[filename]
    got = classify.classify(columns_of(text), filename)
    assert got["role"] == want, f"{filename} read as {got['role']}: {got['why']}"


def test_a_kind_is_never_named_when_its_required_fields_are_absent():
    """Coverage before resemblance. A statement with no amount column is not a statement."""
    for role in classify.ORDER:
        s = classify.score(role, columns_of("Colour,Shape\nred,round\nblue,square\n"), "x.csv")
        assert s["fit"] == 0.0 and s["missing"], role
    got = classify.classify(columns_of("Colour,Shape\nred,round\n"), "x.csv")
    assert got["role"] is None
    assert "reads as" in got["why"] or "explains" in got["why"]


def test_a_ledger_is_not_mistaken_for_a_statement_because_memo_reads_as_a_description():
    """The trap in the whole idea: our bank statement accepts a column named `memo` as its
    description, so a ledger fits the bank shape unless the account column is allowed to count
    for more than the columns every kind shares."""
    cols = columns_of(FILES["download.csv"][1])
    bank, ledger = classify.score("bank_lines", cols, "x"), classify.score("ledger_entries", cols, "x")
    assert not bank["missing"], "the bank shape genuinely does fit, which is why this matters"
    assert ledger["fit"] > bank["fit"]


def test_the_filename_cannot_outvote_the_columns():
    _, ledger_text = FILES["download.csv"]
    got = classify.classify(columns_of(ledger_text), "bank statement january.csv")
    assert got["role"] == "ledger_entries"


def test_a_close_call_is_reported_rather_than_guessed():
    """A file with both a free-text description and a GL account really could be either, and
    saying so is worth more than a coin flip presented as an answer."""
    got = classify.classify(columns_of("Date,Amount,Description,Account\n2026-01-14,10,Card,1200\n"),
                            "x.csv")
    assert got["role"] == "ledger_entries" and got["close"] is True
    assert "bank_lines" in got["alternatives"]
    assert got["confidence"] < 0.6, "a close call must not present itself as settled"
    assert "check this one" in got["why"]


# --- the round trip ---------------------------------------------------------------------------
def identify(client, filename, purpose="history", **params):
    _, text = FILES[filename]
    q = "&".join(f"{k}={v}" for k, v in {"filename": filename, "purpose": purpose, **params}.items())
    res = client.post(f"/api/onboarding/identify?{q}", content=text.encode(),
                      headers={"content-type": "text/csv"})
    assert res.status_code == 200, res.text
    return res.json()


def test_identify_stages_the_file_and_checks_the_mapping_it_proposed(client):
    card = identify(client, "export (3).csv")
    assert card["role"] == "bank_lines" and card["upload_id"].startswith("up_")
    assert card["check"]["ok"], card["check"]["blocking"]
    # Paid In and Paid Out, read as a debit/credit pair rather than one signed column.
    assert card["mapping"]["sign"]["mode"] == "debit_credit"
    assert card["check"]["stats"]["money_in"] == 1 and card["check"]["stats"]["money_out"] == 1
    assert card["usage"]["llm_calls"] == 0, "naming a file must not cost anything"


def test_you_can_override_what_we_decided(client):
    card = identify(client, "download.csv", role="bank_lines")
    assert card["role"] == "bank_lines" and card["source"] == "you"


def test_dropping_the_whole_folder_imports_in_an_order_that_resolves(client):
    """Selected in the worst possible order: the trail first, the records it points at last."""
    cards = [identify(client, name) for name in
             ["sheet1.csv", "q1 output.csv", "untitled.csv", "data.csv", "mailbox.csv",
              "download.csv", "export (3).csv", "file.csv", "list.csv"]]
    assert all(c.get("upload_id") for c in cards), [c.get("blocked") for c in cards]
    assert {c["role"] for c in cards} == set(classify.ORDER)

    res = client.post("/api/onboarding/import/batch", json={"files": [
        {"upload_id": c["upload_id"], "mapping": c["mapping"]} for c in cards]})
    assert res.status_code == 200, res.text
    done = wait(client, res.json()["job_id"])
    assert done["state"] == "done", done.get("error")
    out = done["result"]
    assert out["failed"] == [], out["failed"]
    assert out["order"].index("bank_lines") < out["order"].index("reconciliations")
    assert out["order"].index("ledger_entries") < out["order"].index("reconciliations")
    assert out["order"].index("bank_lines") < out["order"].index("adjustments")

    con = db.connect(CO, readonly=True)
    try:
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("bank_line", "ledger_entry", "reconcile_link", "journal_entry",
                            "approval", "invoice", "document")}
    finally:
        con.close()
    assert counts["bank_line"] == 2 and counts["ledger_entry"] == 2
    assert counts["reconcile_link"] == 1, "the trail resolved against records imported before it"
    assert counts["journal_entry"] == 1 and counts["approval"] == 1
    assert counts["invoice"] == 1 and counts["document"] == 1
    assert not any("not imported yet" in w for w in out["warnings"]), out["warnings"]


def wait(client, job_id, tries=200):
    import time
    for _ in range(tries):
        rec = client.get(f"/api/jobs/{job_id}").json()
        if rec["state"] in ("done", "error", "lost"):
            return rec
        time.sleep(0.05)
    raise AssertionError("job never finished")
