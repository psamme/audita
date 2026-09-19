"""The contracts every lane codes against: reason codes, treatments, accounts, run configurations.

All money is integer cents. A claim's `settles` is the receivable credited per invoice. A residual is the part of a
payment the invoices do not explain; `side` says whether the bank paid short of the invoices or over them.
"""
from dataclasses import dataclass

REASONS = ["BANK_FEE", "DISCOUNT_TAKEN", "SHORT_PAY", "DUPLICATE_PAYMENT", "PREPAID_CREDITS", "TIMING",
           "PROCESSOR_FEE", "CHARGEBACK", "UNKNOWN"]
SITUATIONS = REASONS + ["AMBIGUOUS_ALLOCATION", "UNKNOWN_PAYER"]

# treatment -> how it reaches the books. bound: which way a learned amount limit runs. kind: which materiality cap applies.
TREATMENTS = {
    "absorb_bank_fee": {"account": "bank_fees", "dr": True, "reason": "BANK_FEE", "bound": "max", "kind": "pnl",
                        "label": "Absorb as a bank fee"},
    "write_off_discount": {"account": "sales_discounts", "dr": True, "reason": "DISCOUNT_TAKEN", "bound": "max",
                           "kind": "pnl", "label": "Write off to sales discounts"},
    "contract_discount": {"account": "sales_discounts", "dr": True, "reason": "DISCOUNT_TAKEN", "bound": "max",
                          "kind": "pnl", "label": "Discount the contract allows"},
    "leave_open_chase": {"account": None, "dr": True, "reason": None, "bound": "min", "kind": "none",
                         "label": "Leave the difference open and chase the customer"},
    "customer_credit": {"account": "customer_credits", "dr": False, "reason": "DUPLICATE_PAYMENT", "bound": "max",
                        "kind": "bs", "label": "Hold as customer credit"},
    "deferred_revenue": {"account": "deferred_revenue", "dr": False, "reason": "PREPAID_CREDITS", "bound": "max",
                         "kind": "bs", "label": "Book as deferred revenue (prepaid credits)"},
    "unapplied_cash": {"account": "unapplied_cash", "dr": False, "reason": "UNKNOWN", "bound": "max", "kind": "bs",
                       "label": "Hold as unapplied cash"},
    "oldest_first": {"account": None, "dr": True, "reason": None, "bound": "none", "kind": "none",
                     "label": "Apply to the oldest invoices first"},
}
POLICY_CODES = {("BANK_FEE", "absorb_bank_fee"): "FEE-WIRE-01", ("BANK_FEE", "leave_open_chase"): "FEE-WIRE-02",
                ("DISCOUNT_TAKEN", "write_off_discount"): "SHORTPAY-01", ("DISCOUNT_TAKEN", "leave_open_chase"): "SHORTPAY-02",
                ("DISCOUNT_TAKEN", "contract_discount"): "CONTRACT-DISC-01",
                ("DUPLICATE_PAYMENT", "customer_credit"): "DUPPAY-01", ("PREPAID_CREDITS", "deferred_revenue"): "PREPAID-01",
                ("AMBIGUOUS_ALLOCATION", "oldest_first"): "ALLOC-01"}

ACCOUNTS = ["cash", "ar", "stripe_clearing", "unapplied_cash", "customer_credits", "deferred_revenue",
            "accrued_liabilities", "opening_equity", "revenue", "sales_discounts", "refunds", "bank_fees",
            "processing_fees", "chargeback_losses", "dispute_fees", "payroll", "payroll_taxes", "cloud_compute", "rent",
            "vendors_opex"]
KEY_BALANCES = ["cash", "ar", "customer_credits", "deferred_revenue", "stripe_clearing"]

NEAR_TOL_CENTS, NEAR_TOL_PCT = 15000, 0.03   # a gap this small between invoices and receipt can be explained as a residual
MATERIALITY = {"pnl": 50000, "bs": 2500000, "none": 10 ** 12}
PROBATION_USES = 5
MAX_ROUNDS = 2


@dataclass(frozen=True)
class Config:
    name: str
    cards: bool       # desks share one card and the checker gates the ledger
    memory: bool      # policies, aliases and allocation habits persist across months
    policies: bool    # human answers become rules at all

    @staticmethod
    def get(name: str) -> "Config":
        return {"A": Config("A", False, False, False), "B": Config("B", True, False, True),
                "C": Config("C", True, True, True)}[name]


def short_total(residuals) -> int:
    """Cents the books must explain on the debit side: short residuals that are absorbed or written off."""
    return sum(r["amount"] for r in residuals if r["side"] == "short" and r.get("treatment") != "leave_open_chase")


def over_total(residuals) -> int:
    return sum(r["amount"] for r in residuals if r["side"] == "over")


def entry_lines(amount: int, claim: dict) -> list[dict]:
    """The one mapping from a cash application claim to journal lines. Balanced exactly when the claim's amounts tie."""
    cid = claim.get("customer_id")
    lines = [{"account": "cash", "debit": amount, "credit": 0}]
    for inv, cents in claim["settles"].items():
        lines.append({"account": "ar", "debit": 0, "credit": cents, "customer_id": cid, "invoice_id": inv})
    for r in claim["residuals"]:
        t = TREATMENTS.get(r.get("treatment") or "")
        if not t or not t["account"]:
            continue
        lines.append({"account": t["account"], "debit": r["amount"] if t["dr"] else 0,
                      "credit": 0 if t["dr"] else r["amount"], "customer_id": cid})
    for extra in claim.get("extra_lines", []):
        lines.append(dict(extra))
    return lines


def usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"
