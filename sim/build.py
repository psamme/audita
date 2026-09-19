"""Build the client worlds.

    uv run python -m sim.build            # months 1-3 for both clients: data plus the humans' resolutions

The repo never generates the test month. Whoever owns the hidden test keeps a script outside version control
(under keys/, which is gitignored) that appends month 4 with their own seed and traps:

    from sim.build import build_test
    def my_trap(w): ...                   # uses the World API in sim/world.py; call w.resolve() for every item
    build_test("A", seed=1234, traps=[my_trap])

build_test restores the month 1-3 snapshot, flushes bank lines that were in transit at the end of March, runs
the standard generators for April with the given seed, then the traps, and writes keys/<client>_2026-04.json.
"""
import json
import sqlite3
from typing import Callable

from shadow import db
from sim import client_a, client_b
from sim.world import HISTORY, TEST, World, truth_path

MODULES = {"A": client_a, "B": client_b}
SEEDS = {"A": 11, "B": 23}


def _generate(w: World, period: str):
    mod = MODULES[w.client]
    if w.client == "A":
        for gen in mod.GENERATORS:
            gen(w, period)
    else:
        mod.generate(w, period)


def build_history(client_id: str) -> dict:
    mod = MODULES[client_id]
    w = World(client_id, SEEDS[client_id], HISTORY[-1], meta=(mod.NAME, mod.BLURB, mod.CHART), enact_cfg=mod.ENACT)
    for period in HISTORY:
        _generate(w, period)
    return w.finish()


def build_test(client_id: str, seed: int, traps: list[Callable[[World], None]] = (), standard: bool = True) -> dict:
    w = World(client_id, seed, TEST)
    if standard:
        _generate(w, TEST)
    w.source = "blind"
    for trap in traps:
        trap(w)
    return w.finish()


def export_dev_key(client_id: str, period: str = "2026-03") -> str:
    """A development answer key cut from history, so the agent can be tuned without touching the hidden test.

    Train on the periods before `period`, grade against what the humans did in `period`.
    """
    con = sqlite3.connect(truth_path(client_id))
    con.row_factory = sqlite3.Row
    key = {}
    dates = dict(sqlite3.connect(db.db_path(client_id).with_name("history.db")).execute("SELECT id, date FROM bank_line UNION ALL SELECT id, date FROM ledger_entry").fetchall())
    for r in con.execute("SELECT * FROM resolution WHERE period=?", (period,)):
        key[r["item_id"]] = {
            "item_kind": r["item_kind"], "date": dates[r["item_id"]], "action": r["action"], "ledger_ids": json.loads(r["ledger_ids"]),
            "adjustments": json.loads(r["adjustments"]), "escalate_to": r["escalate_to"], "note": r["note"],
            "category": r["category"], "easy": r["by"] == "auto",
            "source": "standard", "alternatives": []}
    out = db.RUNS / "dev"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"key_{client_id}_{period}.json"
    path.write_text(json.dumps(key, indent=1))
    return str(path)


if __name__ == "__main__":
    for cid in MODULES:
        print(cid, json.dumps(build_history(cid)), export_dev_key(cid))
