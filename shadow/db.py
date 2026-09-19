"""Canonical data model. One SQLite file per client holds everything the agent may see.

Answer keys and ground truth are never stored here; they belong to the simulator and the grader.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"

SCHEMA = """
CREATE TABLE IF NOT EXISTS client (id TEXT PRIMARY KEY, name TEXT, blurb TEXT, chart JSON, close_days INTEGER);
CREATE TABLE IF NOT EXISTS user (id TEXT PRIMARY KEY, name TEXT, role TEXT, senior INTEGER);
CREATE TABLE IF NOT EXISTS bank_line (
  id TEXT PRIMARY KEY, period TEXT, date TEXT, amount REAL, description TEXT,
  counterparty TEXT, ref TEXT);
-- cash-side entries the business posted: expected receipts, payments issued, sales batches
CREATE TABLE IF NOT EXISTS ledger_entry (
  id TEXT PRIMARY KEY, period TEXT, date TEXT, posted_at TEXT, account TEXT, amount REAL,
  memo TEXT, counterparty TEXT, ref TEXT, invoice_id TEXT);
CREATE TABLE IF NOT EXISTS invoice (
  id TEXT PRIMARY KEY, party TEXT, direction TEXT, date TEXT, due_date TEXT, amount REAL,
  terms TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS document (
  id TEXT PRIMARY KEY, type TEXT, date TEXT, sender TEXT, subject TEXT, body TEXT, meta JSON);
-- what the ERP keeps of past reconciliation work: links, adjustment entries, the odd approval. No reasons.
CREATE TABLE IF NOT EXISTS reconcile_link (
  id TEXT PRIMARY KEY, period TEXT, bank_id TEXT, ledger_id TEXT, amount REAL,
  reconciled_by TEXT, reconciled_at TEXT, undone_at TEXT);
CREATE TABLE IF NOT EXISTS journal_entry (
  id TEXT PRIMARY KEY, period TEXT, date TEXT, posted_at TEXT, posted_by TEXT, account TEXT, amount REAL,
  memo TEXT, bank_id TEXT, reverses_id TEXT);
CREATE TABLE IF NOT EXISTS approval (
  id TEXT PRIMARY KEY, period TEXT, date TEXT, subject TEXT, subject_id TEXT, requested_by TEXT,
  approver TEXT, status TEXT, comment TEXT);
CREATE INDEX IF NOT EXISTS ix_bank_period ON bank_line(period);
CREATE INDEX IF NOT EXISTS ix_ledger_period ON ledger_entry(period);
CREATE INDEX IF NOT EXISTS ix_link_bank ON reconcile_link(bank_id);
CREATE INDEX IF NOT EXISTS ix_je_bank ON journal_entry(bank_id);
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
