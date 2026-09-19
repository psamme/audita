"""The answer key must never reach a desk: not through the tool layer, not through a file, not through an import."""
import csv
import json
import re
from pathlib import Path

import pytest

from spine.store import Store
from spine.tools import Tools

ROOT = Path(__file__).resolve().parent.parent
TRUTH_KEYS = {"traps", "trap", "hero", "needs_human", "payer_profiles", "control_breaches", "duplicate_entries", "role",
              "lookalike_of", "dup_of"}


def keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from keys(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from keys(v)


def test_tool_layer_refuses_the_truth_folder(world, tmp_path):
    with pytest.raises(ValueError):
        Tools(world / "truth", Store(tmp_path / "x.db"))


def test_public_files_carry_no_generator_labels(world):
    for f in (world / "public").rglob("*"):
        if f.suffix == ".csv":
            cols = set(next(csv.reader(open(f))))
            assert not cols & TRUTH_KEYS and not any(c.startswith("_") for c in cols), f
        elif f.suffix == ".json":
            found = set(keys(json.loads(f.read_text())))
            assert not found & TRUTH_KEYS and not any(k.startswith("_") for k in found), f
        elif f.suffix == ".txt":
            assert not re.search(r"trap|answer key|hidden policy", f.read_text(), re.I), f


def test_tool_outputs_carry_no_truth_fields(world, tmp_path):
    t = Tools(world / "public", Store(tmp_path / "x.db"))
    t.today = "2026-03-31"
    outputs = [t.customers(), t.open_invoices(), t.bank_lines(), t.payouts(), t.history(), t.outflow_schedule(),
               t.opening_balances(), t.search_emails(terms=["a"]), [t.erp_entries(b["date"]) for b in t.bank_lines()[:40]],
               [t.identify_payer(b["text"]) for b in t.bank_lines()], [t.contract(c["customer_id"]) for c in t.customers()]]
    assert not set(keys(outputs)) & TRUTH_KEYS
    assert "hidden policy" not in json.dumps(outputs).lower()


def test_tool_layer_never_shows_tomorrow(world, tmp_path):
    t = Tools(world / "public", Store(tmp_path / "x.db"))
    t.today = "2026-01-15"
    assert all(b["date"] <= t.today for b in t.bank_lines())
    assert all(e["date"] <= t.today for e in t.search_emails(terms=["a", "e"]))
    assert all(i["issue_date"] <= t.today for i in t.open_invoices())
    assert t.erp_entries("2026-02-01") == []


def test_desks_and_spine_never_import_the_world_side():
    for f in list((ROOT / "desks").glob("*.py")) + list((ROOT / "spine").glob("*.py")):
        src = f.read_text()
        assert not re.search(r"truth\.json|hidden_policy|^\s*(from|import) world", src, re.M), f
