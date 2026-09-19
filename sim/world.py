"""World builder: the only API generators and trap plug-ins use to create data.

Everything passed to `resolve()` for a history period lands in the client DB as what the client's humans did.
For the test period it lands in the hidden answer key instead. The repo builds history only; the test period
is appended by a script kept outside the repo (see sim/build.py: build_test).
"""
import json
import random
import shutil
from datetime import date, timedelta

from shadow import db

HISTORY = ["2026-01", "2026-02", "2026-03"]
TEST = "2026-04"
KEYS = db.ROOT / "keys"
ACTIONS = {"match", "match_adjust", "book", "escalate", "carry_forward"}


def d(s: str) -> date:
    return date.fromisoformat(s)


def month_days(period: str) -> list[date]:
    y, m = map(int, period.split("-"))
    out, cur = [], date(y, m, 1)
    while cur.month == m:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def bizdays(period: str) -> list[date]:
    return [x for x in month_days(period) if x.weekday() < 5]


def next_biz(x: date, lag: int = 0) -> date:
    x += timedelta(days=lag)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x


class World:
    def __init__(self, client_id: str, seed: int, last_period: str, meta: tuple | None = None):
        """meta=(name, blurb, chart) starts a fresh DB; meta=None reopens the history snapshot to append to it."""
        self.client = client_id
        self.rng = random.Random(seed)
        self.last_period = last_period
        self.key: dict[str, dict] = {}
        self.key_period: str | None = None
        self.source = "standard"  # "blind" while trap plug-ins run
        self.deferred: list[dict] = []
        self.state: dict = {}
        path, snap = db.db_path(client_id), db.db_path(client_id).with_name("history.db")
        if meta:
            path.unlink(missing_ok=True)
            self.con = db.connect(client_id)
            self.con.execute("INSERT INTO client VALUES (?,?,?,?,?)", (client_id, meta[0], meta[1], json.dumps(meta[2]), 5))
            self.n = {"BL": 0, "LE": 0, "INV": 0, "DOC": 0, "RES": 0}
        else:
            shutil.copy(snap, path)
            self.con = db.connect(client_id)
            saved = json.loads(snap.with_name("sim_state.json").read_text())
            self.n, self.state, carried = saved["n"], saved["state"], saved["deferred"]
            self.key_period = last_period
            for item in carried:  # bank lines the history build scheduled past its cut-off
                bl = self.bank(d(item["date"]), *item["args"])
                if item["resolve"]:
                    self.resolve("bank", bl, **item["resolve"])

    def _id(self, kind: str) -> str:
        self.n[kind] += 1
        return f"{self.client}-{kind}-{self.n[kind]:05d}"

    # --- records -------------------------------------------------------------------------
    def bank(self, on: date, amount: float, description: str, counterparty: str = "", ref: str = "") -> str:
        if on.isoformat()[:7] > self.last_period:
            self.deferred.append({"date": on.isoformat(), "args": [round(amount, 2), description, counterparty, ref],
                                  "resolve": None})
            return f"DEFER-{len(self.deferred) - 1}"
        i = self._id("BL")
        self.con.execute("INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)",
                         (i, on.isoformat()[:7], on.isoformat(), round(amount, 2), description, counterparty, ref))
        return i

    def ledger(self, on: date, account: str, amount: float, memo: str, counterparty: str = "",
               ref: str = "", invoice_id: str | None = None, posted_at: date | None = None,
               period: str | None = None) -> str:
        """period defaults to the month of `on`; pass it to review an entry in the period it was posted."""
        i = self._id("LE")
        self.con.execute("INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (i, period or on.isoformat()[:7], on.isoformat(), (posted_at or on).isoformat(), account,
                          round(amount, 2), memo, counterparty, ref, invoice_id))
        return i

    def invoice(self, party: str, direction: str, on: date, amount: float, terms: str = "net30",
                status: str = "open") -> str:
        i = self._id("INV")
        self.con.execute("INSERT INTO invoice VALUES (?,?,?,?,?,?,?,?)",
                         (i, party, direction, on.isoformat(), (on + timedelta(days=30)).isoformat(),
                          round(amount, 2), terms, status))
        return i

    def doc(self, type: str, on: date, sender: str, subject: str, body: str, meta: dict | None = None) -> str:
        i = self._id("DOC")
        self.con.execute("INSERT INTO document VALUES (?,?,?,?,?,?,?)",
                         (i, type, on.isoformat(), sender, subject, body, json.dumps(meta or {})))
        return i

    # --- truth ---------------------------------------------------------------------------
    def resolve(self, item_kind: str, item_id: str, action: str, ledger_ids=(), adjustments=(),
                escalate_to: str | None = None, note: str = "", easy: bool = False,
                category: str = "", alternatives=()):
        """Record the correct handling of one bank line (item_kind='bank') or ledger entry ('ledger').

        action: match | match_adjust | book | escalate | carry_forward
        adjustments: [{"account": "6110", "amount": 18.00}], sum == sum(ledger amounts) - bank amount
        alternatives: other acceptable resolutions (dicts with action, ledger_ids, adjustments, escalate_to)
        easy: True when a human never had to think about it (exact one-to-one match)
        """
        assert action in ACTIONS, action
        ledger_ids = sorted(ledger_ids)
        adjustments = [{"account": a["account"], "amount": round(a["amount"], 2)} for a in adjustments]
        if item_id.startswith("DEFER-"):
            self.deferred[int(item_id[6:])]["resolve"] = dict(
                action=action, ledger_ids=ledger_ids, adjustments=adjustments, escalate_to=escalate_to, note=note,
                easy=easy, category=category, alternatives=list(alternatives))
            for le in ledger_ids:
                self._record("ledger", le, "carry_forward", note="o/s at month end", easy=True, category="outstanding")
            return
        self._record(item_kind, item_id, action, ledger_ids, adjustments, escalate_to, note, easy, category, alternatives)
        if item_kind == "bank" and ledger_ids:
            period = self._period("bank", item_id)
            for le in ledger_ids:
                if self._period("ledger", le) < period and not self._has_record(le):
                    self._record("ledger", le, "carry_forward", note="o/s at month end, cleared next month", easy=True,
                                 category="outstanding")

    def _period(self, item_kind: str, item_id: str) -> str:
        table = "bank_line" if item_kind == "bank" else "ledger_entry"
        return self.con.execute(f"SELECT period FROM {table} WHERE id=?", (item_id,)).fetchone()[0]

    def _has_record(self, item_id: str) -> bool:
        return item_id in self.key or bool(
            self.con.execute("SELECT 1 FROM resolution WHERE item_id=?", (item_id,)).fetchone())

    def _record(self, item_kind, item_id, action, ledger_ids=(), adjustments=(), escalate_to=None, note="",
                easy=False, category="", alternatives=()):
        period = self._period(item_kind, item_id)
        if period == self.key_period:
            table = "bank_line" if item_kind == "bank" else "ledger_entry"
            when = self.con.execute(f"SELECT date FROM {table} WHERE id=?", (item_id,)).fetchone()[0]
            self.key[item_id] = {
                "item_kind": item_kind, "date": when, "action": action, "ledger_ids": list(ledger_ids),
                "adjustments": list(adjustments), "escalate_to": escalate_to, "note": note,
                "category": category or ("easy" if easy else "exception"), "easy": easy,
                "source": self.source, "alternatives": list(alternatives)}
        else:
            self.con.execute("INSERT INTO resolution VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (self._id("RES"), period, item_kind, item_id, "auto" if easy else "human", action,
                              json.dumps(list(ledger_ids)), json.dumps(list(adjustments)), escalate_to, note))

    # --- common shapes -------------------------------------------------------------------
    def easy_pair(self, on: date, amount: float, description: str, account: str, memo: str,
                  counterparty: str = "", ref: str = "", lag: int | None = None, category: str = "easy") -> tuple[str, str]:
        """A ledger entry and the bank line that clears it for the identical amount."""
        lag = self.rng.choice([0, 0, 1, 1, 2]) if lag is None else lag
        le = self.ledger(on, account, amount, memo, counterparty, ref)
        bl = self.bank(next_biz(on, lag), amount, description, counterparty, ref)
        self.resolve("bank", bl, "match", [le], easy=True, category=category)
        return le, bl

    def finish(self, key_path=None) -> dict:
        self.con.commit()
        counts = {t: self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ["bank_line", "ledger_entry", "invoice", "document", "resolution"]}
        self.con.close()
        path = db.db_path(self.client)
        if self.key_period:
            KEYS.mkdir(exist_ok=True)
            (key_path or KEYS / f"{self.client}_{self.key_period}.json").write_text(json.dumps(self.key, indent=1))
        else:
            shutil.copy(path, path.with_name("history.db"))
            path.with_name("sim_state.json").write_text(
                json.dumps({"n": self.n, "state": self.state, "deferred": self.deferred}))
        return counts | {"key_items": len(self.key), "deferred_bank_lines": len(self.deferred)}
