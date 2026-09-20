"""One SQLite file per run: ledger, cards, decisions, policies, desk notebooks."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (entry_id TEXT PRIMARY KEY, date TEXT, period TEXT, card_id TEXT, source TEXT,
  prepared_by TEXT, approved_by TEXT, memo TEXT, evidence TEXT, erp_id TEXT, reverses TEXT, posted_on TEXT);
CREATE TABLE IF NOT EXISTS lines (entry_id TEXT, account TEXT, debit INTEGER, credit INTEGER, customer_id TEXT, invoice_id TEXT);
CREATE TABLE IF NOT EXISTS periods (period TEXT PRIMARY KEY, locked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY, line_id TEXT, kind TEXT, date TEXT, period TEXT, status TEXT, doc TEXT);
CREATE TABLE IF NOT EXISTS applications (card_id TEXT, invoice_id TEXT, amount INTEGER, date TEXT);
CREATE TABLE IF NOT EXISTS subledger (account TEXT, customer_id TEXT, card_id TEXT, amount INTEGER, date TEXT);
CREATE TABLE IF NOT EXISTS bank_matches (entry_id TEXT PRIMARY KEY, line_id TEXT, card_id TEXT);
CREATE TABLE IF NOT EXISTS decisions (decision_id TEXT PRIMARY KEY, card_id TEXT, day TEXT, doc TEXT);
CREATE TABLE IF NOT EXISTS policies (code TEXT, version INTEGER, status TEXT, doc TEXT, PRIMARY KEY (code, version));
CREATE TABLE IF NOT EXISTS policy_uses (code TEXT, version INTEGER, card_id TEXT, customer_id TEXT, day TEXT, amount INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS aliases (payer_key TEXT PRIMARY KEY, customer_id TEXT, source TEXT, day TEXT);
CREATE TABLE IF NOT EXISTS notebook (desk TEXT, key TEXT, doc TEXT, PRIMARY KEY (desk, key));
CREATE TABLE IF NOT EXISTS findings (finding_id TEXT PRIMARY KEY, period TEXT, card_id TEXT, doc TEXT);
CREATE TABLE IF NOT EXISTS events (n INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, card_id TEXT, kind TEXT, doc TEXT);
CREATE INDEX IF NOT EXISTS lines_acct ON lines (account);
CREATE INDEX IF NOT EXISTS entries_card ON entries (card_id);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def q(self, sql, *args) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql, *args):
        rows = self.q(sql, *args)
        return rows[0][0] if rows else None

    def x(self, sql, *args):
        self.db.execute(sql, args)

    def commit(self):
        self.db.commit()

    def next_id(self, prefix: str, table: str, col: str, width=5) -> str:
        n = self.one(f"SELECT COUNT(*) FROM {table}") + 1
        return f"{prefix}-{n:0{width}d}"

    # ---- cards ----
    def save_card(self, card: dict):
        self.x("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?)", card["card_id"], card["bank_line"]["line_id"],
               card["kind"], card["bank_line"]["date"], card["bank_line"]["date"][:7], card["status"], json.dumps(card))

    def card(self, card_id: str) -> dict | None:
        doc = self.one("SELECT doc FROM cards WHERE card_id=?", card_id)
        return json.loads(doc) if doc else None

    def cards(self, period: str | None = None, status: str | None = None) -> list[dict]:
        sql, args = "SELECT doc FROM cards WHERE 1=1", []
        if period:
            sql, args = sql + " AND period=?", args + [period]
        if status:
            sql, args = sql + " AND status=?", args + [status]
        return [json.loads(r[0]) for r in self.q(sql + " ORDER BY card_id", *args)]

    # ---- notebooks: each desk's private working papers ----
    def note(self, desk: str, key: str, default=None):
        doc = self.one("SELECT doc FROM notebook WHERE desk=? AND key=?", desk, key)
        return json.loads(doc) if doc else default

    def set_note(self, desk: str, key: str, doc):
        self.x("INSERT OR REPLACE INTO notebook VALUES (?,?,?)", desk, key, json.dumps(doc))

    def event(self, day: str, card_id: str | None, kind: str, doc: dict):
        self.x("INSERT INTO events (day, card_id, kind, doc) VALUES (?,?,?,?)", day, card_id, kind, json.dumps(doc))
