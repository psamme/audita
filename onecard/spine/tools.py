"""The tool layer: the only way desks read the world. It is handed the public folder and nothing else, and it never
returns a record dated after `today`, so a desk cannot see tomorrow's email or the answer key.
"""
import csv
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .ledger import Ledger
from .store import Store

HEADS = ("WIRE IN", "ACH CREDIT", "WIRE OUT", "ACH DEBIT")
SUFFIXES = {"INC", "LLC", "CORP", "LTD", "CO", "LLP"}


def norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9 ]", "", name.upper()).strip()


def payer_text(text: str) -> str:
    """The counterparty name as the bank printed it: text minus the rail prefix and any reference."""
    t = text
    for h in HEADS:
        if t.startswith(h):
            t = t[len(h):]
    t = re.split(r"\s(?:INV-\d+|/CHGS|PREPAID|REFUND|REF\b)", t)[0]
    return norm(t)


def day_of(unix: int) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).date().isoformat()


class Tools:
    def __init__(self, public_dir: str | Path, store: Store):
        self.dir = Path(public_dir).resolve()
        if "truth" in self.dir.parts:
            raise ValueError("the tool layer is never pointed at the answer key")
        self.s, self.ledger, self.today = store, Ledger(store), "2026-01-01"
        read = lambda name: list(csv.DictReader(open(self.dir / name)))
        self._customers = read("customers.csv")
        self._invoices = [{**r, "amount": int(r["amount"])} for r in read("invoices.csv")]
        self._inv = {i["invoice_id"]: i for i in self._invoices}
        self._bank = [{**r, "amount": int(r["amount"]), "balance": int(r["balance"])}
                      for f in sorted(self.dir.glob("bank_*.csv")) for r in csv.DictReader(open(f))]
        self._payouts = json.loads((self.dir / "stripe_payouts.json").read_text())
        self._erp = json.loads((self.dir / "erp_entries.json").read_text())
        self._history = read("history/receipts_2025.csv")
        self._schedule = [{**r, "amount": int(r["amount"])} for r in read("outflow_schedule.csv")]
        self._emails = []
        for f in sorted((self.dir / "emails").glob("*.txt")):
            head, body = f.read_text().split("\n\n", 1)
            h = dict(ln.split(": ", 1) for ln in head.splitlines())
            self._emails.append({"email_id": f.stem, "date": h["Date"], "from": h["From"], "subject": h["Subject"], "body": body})

    # ---- reference data ----
    def customers(self) -> list[dict]:
        return self._customers

    def customer(self, customer_id: str) -> dict | None:
        return next((c for c in self._customers if c["customer_id"] == customer_id), None)

    def invoice(self, invoice_id: str) -> dict | None:
        i = self._inv.get(invoice_id)
        return i if i and i["issue_date"] <= self.today else None

    def contract(self, customer_id: str) -> str:
        f = self.dir / "contracts" / f"{customer_id}.txt"
        return f.read_text() if f.exists() else ""

    def opening_balances(self) -> dict:
        return json.loads((self.dir / "opening_balances.json").read_text())

    def history(self) -> list[dict]:
        return self._history

    def outflow_schedule(self) -> list[dict]:
        return self._schedule

    # ---- get_open_invoices ----
    def applied(self, invoice_id: str) -> int:
        return self.s.one("SELECT COALESCE(SUM(amount), 0) FROM applications WHERE invoice_id=?", invoice_id)

    def open_invoices(self, customer_id: str | None = None) -> list[dict]:
        out = []
        for i in self._invoices:
            if i["issue_date"] <= self.today and (customer_id is None or i["customer_id"] == customer_id):
                left = i["amount"] - self.applied(i["invoice_id"])
                if left > 0:
                    out.append({**i, "open": left})
        return sorted(out, key=lambda i: (i["due_date"], i["invoice_id"]))

    def settled_invoices(self, customer_id: str) -> list[dict]:
        rows = self.s.q("SELECT invoice_id, card_id, MAX(date) AS date FROM applications GROUP BY invoice_id")
        return [{**self._inv[r["invoice_id"]], "card_id": r["card_id"], "settled_on": r["date"]} for r in rows
                if self._inv[r["invoice_id"]]["customer_id"] == customer_id]

    # ---- get_bank_lines / payouts / ERP feed ----
    def bank_lines(self, day: str | None = None, period: str | None = None) -> list[dict]:
        return [b for b in self._bank if b["date"] <= self.today and (day is None or b["date"] == day)
                and (period is None or b["date"][:7] == period)]

    def payout_for(self, text: str) -> dict | None:
        m = re.search(r"PO_\w+", text)
        return next((p for p in self._payouts if m and p["id"].upper() == m.group(0)
                     and day_of(p["created"]) <= self.today), None)

    def payouts(self) -> list[dict]:
        return [p for p in self._payouts if day_of(p["created"]) <= self.today]

    def stripe_balance(self, as_of: str) -> int | None:
        for b in json.loads((self.dir / "stripe_balance.json").read_text()):
            if b["as_of"] == as_of and as_of <= self.today:
                return sum(x["amount"] for x in b["available"] + b["pending"])
        return None

    def erp_entries(self, day: str) -> list[dict]:
        return [e for e in self._erp if e["date"] == day and day <= self.today]

    # ---- search_emails ----
    def search_emails(self, *, domain: str | None = None, terms: list[str] = (), since: str = "2000-01-01") -> list[dict]:
        out = []
        for e in self._emails:
            if not (since <= e["date"] <= self.today):
                continue
            text = f"{e['subject']}\n{e['body']}".upper()
            if (domain and e["from"].endswith("@" + domain)) or any(t and t.upper() in text for t in terms):
                out.append(e)
        return out

    def email(self, email_id: str) -> dict | None:
        return next((e for e in self._emails if e["email_id"] == email_id and e["date"] <= self.today), None)

    # ---- payer identity ----
    def identify_payer(self, text: str, use_aliases: bool = True) -> dict:
        name = payer_text(text)
        hits = [c for c in self._customers if norm(c["name"]) == name
                or (len(name) >= 12 and norm(c["name"]).startswith(name))]
        if len(hits) == 1:
            return {"customer_id": hits[0]["customer_id"], "how": "name", "printed": name}
        if use_aliases:
            cid = self.s.one("SELECT customer_id FROM aliases WHERE payer_key=?", name)
            if cid:
                return {"customer_id": cid, "how": "alias", "printed": name}
        return {"customer_id": None, "how": None, "printed": name}

    # ---- search_precedents ----
    def precedents(self, customer_id: str | None, reason: str | None, limit: int = 5) -> list[dict]:
        out = []
        for r in self.s.q("SELECT doc FROM decisions ORDER BY decision_id DESC"):
            d = json.loads(r[0])
            f = d.get("features", {})
            if (reason and f.get("reason") == reason) or (customer_id and f.get("customer_id") == customer_id):
                out.append(d)
        return out[:limit]

    def get_balance(self, account: str, as_of: str | None = None) -> int:
        return self.ledger.balance(account, as_of or self.today)


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
