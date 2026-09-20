"""Double-entry, append-only ledger with period locks. `post` is the only write path."""
import json

from .store import Store


class PostingRefused(Exception):
    pass


class Ledger:
    def __init__(self, store: Store):
        self.s = store

    def post(self, *, date: str, lines: list[dict], memo: str, evidence: list[str], source: str, card_id: str | None = None,
             prepared_by: str = "system", approved_by: str = "checker", erp_id: str | None = None,
             reverses: str | None = None, posted_on: str | None = None, hook=None) -> str:
        lines = [ln for ln in lines if ln.get("debit") or ln.get("credit")]
        if not lines or sum(ln.get("debit", 0) for ln in lines) != sum(ln.get("credit", 0) for ln in lines):
            raise PostingRefused("unbalanced entry")
        if not evidence:
            raise PostingRefused("no evidence")
        if self.locked(date[:7]):
            raise PostingRefused(f"period {date[:7]} is locked")
        if hook:
            failed = [c for c in hook() if not c["ok"]]
            if failed:
                raise PostingRefused("checker: " + ", ".join(c["rule"] for c in failed))
        entry_id = self.s.next_id("JE", "entries", "entry_id")
        self.s.x("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", entry_id, date, date[:7], card_id, source,
                 prepared_by, approved_by, memo, json.dumps(evidence), erp_id, reverses, posted_on or date)
        for ln in lines:
            self.s.x("INSERT INTO lines VALUES (?,?,?,?,?,?)", entry_id, ln["account"], ln.get("debit", 0),
                     ln.get("credit", 0), ln.get("customer_id"), ln.get("invoice_id"))
        return entry_id

    def reverse(self, entry_id: str, *, date: str, memo: str, evidence: list[str], card_id: str | None, source: str) -> str:
        lines = [{"account": r["account"], "debit": r["credit"], "credit": r["debit"], "customer_id": r["customer_id"],
                  "invoice_id": r["invoice_id"]} for r in self.s.q("SELECT * FROM lines WHERE entry_id=?", entry_id)]
        return self.post(date=date, lines=lines, memo=memo, evidence=evidence, source=source, card_id=card_id,
                         reverses=entry_id)

    def locked(self, period: str) -> bool:
        return bool(self.s.one("SELECT locked FROM periods WHERE period=?", period))

    def lock(self, period: str):
        self.s.x("INSERT OR REPLACE INTO periods VALUES (?, 1)", period)

    def balance(self, account: str, as_of: str = "9999-12-31") -> int:
        return self.s.one("SELECT COALESCE(SUM(debit - credit), 0) FROM lines JOIN entries USING (entry_id) "
                          "WHERE account=? AND date<=?", account, as_of)

    def balances(self, as_of: str = "9999-12-31") -> dict:
        return {r["account"]: r["net"] for r in self.s.q(
            "SELECT account, SUM(debit - credit) AS net FROM lines JOIN entries USING (entry_id) WHERE date<=? "
            "GROUP BY account", as_of)}

    def entry(self, entry_id: str) -> dict | None:
        rows = self.s.q("SELECT * FROM entries WHERE entry_id=?", entry_id)
        if not rows:
            return None
        e = dict(rows[0])
        e["evidence"] = json.loads(e["evidence"])
        e["lines"] = [dict(r) for r in self.s.q("SELECT account, debit, credit, customer_id, invoice_id FROM lines "
                                                 "WHERE entry_id=?", entry_id)]
        return e

    def card_entries(self, card_id: str) -> list[dict]:
        return [self.entry(r[0]) for r in self.s.q("SELECT entry_id FROM entries WHERE card_id=? ORDER BY entry_id", card_id)]

    def card_net(self, card_id: str) -> dict:
        """Net debit by account across every entry tied to a card, reversals included."""
        return {r["account"]: r["net"] for r in self.s.q(
            "SELECT account, SUM(debit - credit) AS net FROM lines JOIN entries USING (entry_id) WHERE card_id=? "
            "GROUP BY account HAVING net != 0", card_id)}
