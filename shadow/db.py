"""Canonical data model. One SQLite file per client holds everything the agent may see.

The month-4 answer key is never stored here; the simulator writes it under keys/.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"

SCHEMA = """
CREATE TABLE IF NOT EXISTS client (id TEXT PRIMARY KEY, name TEXT, blurb TEXT, chart JSON, close_days INTEGER);
CREATE TABLE IF NOT EXISTS bank_line (
  id TEXT PRIMARY KEY, period TEXT, date TEXT, amount REAL, description TEXT,
  counterparty TEXT, ref TEXT);
CREATE TABLE IF NOT EXISTS ledger_entry (
  id TEXT PRIMARY KEY, period TEXT, date TEXT, posted_at TEXT, account TEXT, amount REAL,
  memo TEXT, counterparty TEXT, ref TEXT, invoice_id TEXT);
CREATE TABLE IF NOT EXISTS invoice (
  id TEXT PRIMARY KEY, party TEXT, direction TEXT, date TEXT, due_date TEXT, amount REAL,
  terms TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS document (
  id TEXT PRIMARY KEY, type TEXT, date TEXT, sender TEXT, subject TEXT, body TEXT, meta JSON);
-- Resolutions present in the DB are history only (months 1-3): what the client's humans did.
CREATE TABLE IF NOT EXISTS resolution (
  id TEXT PRIMARY KEY, period TEXT, item_kind TEXT, item_id TEXT, by TEXT, action TEXT,
  ledger_ids JSON, adjustments JSON, escalate_to TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_bank_period ON bank_line(period);
CREATE INDEX IF NOT EXISTS ix_ledger_period ON ledger_entry(period);
CREATE INDEX IF NOT EXISTS ix_res_item ON resolution(item_id);
"""

JSON_COLS = {"chart", "meta", "ledger_ids", "adjustments"}


def db_path(client: str) -> Path:
    return DATA / client / "client.db"


def connect(client: str, readonly: bool = False) -> sqlite3.Connection:
    path = db_path(client)
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d


def rows(cur) -> list[dict]:
    return [row(r) for r in cur]


def q(con, sql: str, *args) -> list[dict]:
    return rows(con.execute(sql, args))


def cents(x: float) -> int:
    return int(round(x * 100))
