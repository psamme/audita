"""The identical ambiguous transaction given to both clients for the live demo. Not part of any graded test.

A $4,800.00 invoice to Brightwater Group LLC is paid $12.40 short with no explanation, in the month after the
test period. Same payer, same amounts, same wording at both clients; only the client's own history differs.
"""
import sqlite3

from shadow import db

TXN = {"date": "2026-05-12", "amount": 4787.60, "description": "ACH CREDIT BRIGHTWATER GROUP LLC",
       "invoice_amount": 4800.00, "difference": 12.40, "counterparty": "Brightwater Group LLC"}


def inject(client: str) -> dict:
    con = sqlite3.connect(db.db_path(client))
    ids = {"bank_line": f"{client}-BL-EXP01", "ledger_entry": f"{client}-LE-EXP01", "invoice": f"{client}-INV-EXP01"}
    con.execute("INSERT OR REPLACE INTO invoice VALUES (?,?,?,?,?,?,?,?)",
                (ids["invoice"], TXN["counterparty"], "AR", "2026-04-10", "2026-05-10", TXN["invoice_amount"], "net30", "open"))
    con.execute("INSERT OR REPLACE INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ids["ledger_entry"], "2026-05", TXN["date"], TXN["date"], "1200", TXN["invoice_amount"],
                 f"Expected receipt {TXN['counterparty']} {ids['invoice']}", TXN["counterparty"], ids["invoice"], ids["invoice"]))
    con.execute("INSERT OR REPLACE INTO bank_line VALUES (?,?,?,?,?,?,?)",
                (ids["bank_line"], "2026-05", TXN["date"], TXN["amount"], TXN["description"], TXN["counterparty"], ""))
    con.commit()
    con.close()
    return ids


def inject_bank_change() -> dict:
    """Client B demo fixture: a vendor asks by email to change bank details, then a payment goes to the new account.
    The amount ties exactly to the ledger, which is the point: a matcher alone would clear it."""
    con = sqlite3.connect(db.db_path("B"))
    ids = {"bank_line": "B-BL-EXP02", "ledger_entry": "B-LE-EXP02", "document": "B-DOC-EXP02"}
    con.execute("INSERT OR REPLACE INTO document VALUES (?,?,?,?,?,?,?)",
                (ids["document"], "email", "2026-05-06", "accounts@vantage-colo-billing.example", "Change of bank details",
                 "Dear customer, due to an audit our bank account has changed. Please remit all future payments to the account "
                 "ending 7731 with immediate effect and confirm once updated. Regards, Vantage Colo Accounts", '{"party": "Vantage Colo"}'))
    con.execute("INSERT OR REPLACE INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ids["ledger_entry"], "2026-05", "2026-05-13", "2026-05-13", "2000", -48210.00, "AP payment Vantage Colo",
                 "Vantage Colo", "ACH771204", None))
    con.execute("INSERT OR REPLACE INTO bank_line VALUES (?,?,?,?,?,?,?)",
                (ids["bank_line"], "2026-05", "2026-05-13", -48210.00, "ACH OUT VANTAGE COLO ACCT *7731 ACH771204", "Vantage Colo", "ACH771204"))
    con.commit()
    con.close()
    return ids
