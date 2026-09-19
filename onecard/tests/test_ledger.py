"""The posting tool is the only write path, and it refuses what the build doc says it refuses."""
import pytest

from spine.ledger import Ledger, PostingRefused
from spine.store import Store

GOOD = [{"account": "cash", "debit": 100, "credit": 0}, {"account": "ar", "debit": 0, "credit": 100}]


@pytest.fixture
def ledger(tmp_path):
    return Ledger(Store(tmp_path / "l.db"))


def test_refuses_unbalanced(ledger):
    with pytest.raises(PostingRefused, match="unbalanced"):
        ledger.post(date="2026-01-05", lines=[GOOD[0]], memo="x", evidence=["bank:1"], source="t")


def test_refuses_unevidenced(ledger):
    with pytest.raises(PostingRefused, match="evidence"):
        ledger.post(date="2026-01-05", lines=GOOD, memo="x", evidence=[], source="t")


def test_refuses_locked_period(ledger):
    ledger.lock("2026-01")
    with pytest.raises(PostingRefused, match="locked"):
        ledger.post(date="2026-01-05", lines=GOOD, memo="x", evidence=["bank:1"], source="t")
    assert ledger.post(date="2026-02-05", lines=GOOD, memo="x", evidence=["bank:1"], source="t")


def test_refuses_when_the_checker_hook_fails(ledger):
    with pytest.raises(PostingRefused, match="amounts_tie"):
        ledger.post(date="2026-01-05", lines=GOOD, memo="x", evidence=["bank:1"], source="t",
                    hook=lambda: [{"rule": "amounts_tie", "ok": False}])
    assert ledger.balance("cash") == 0


def test_reversal_nets_to_zero_and_keeps_both_entries(ledger):
    e = ledger.post(date="2026-01-05", lines=GOOD, memo="x", evidence=["bank:1"], source="t", card_id="C-1")
    r = ledger.reverse(e, date="2026-01-09", memo="undo", evidence=[f"entry:{e}"], card_id="C-1", source="t")
    assert ledger.balance("cash") == 0 and ledger.card_net("C-1") == {}
    assert ledger.entry(r)["reverses"] == e and ledger.entry(e) is not None
