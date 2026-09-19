"""Client B: Meridian AI, an invented frontier AI lab. Stripe-style subscription payouts, enterprise wires, strict controls.

The policy constants below are the ground truth that drives both the humans' history and the answer key.
Agent code never imports this module.
"""
from datetime import timedelta

from sim.world import World, bizdays, month_days, next_biz

NAME = "Meridian AI"
BLURB = "AI lab with consumer subscriptions and enterprise API contracts. Controller-led finance team, monthly close on business day 5."
CHART = {
    "1010": "Operating cash", "1200": "Accounts receivable", "1250": "Paystream clearing", "2000": "Accounts payable",
    "2450": "Customer deposits and prepaid commits", "4000": "Subscription revenue", "4010": "Enterprise API revenue",
    "4050": "Sales discounts", "4090": "Refunds and credits", "6000": "Payroll", "6310": "Payment processing fees",
    "6320": "Chargeback losses", "6500": "Cloud and compute", "6600": "Software and services",
    "7100": "Interest income", "7710": "Bank charges",
}

POLICY = {
    "writeoff_max": 0.00,                 # no write-offs without controller approval, any amount
    "early_pay_discount": {"Halcyon Robotics": 0.02},  # contractual 2/10 net 30
    "wire_fee_needs_evidence": True,      # "less charges" on the bank line or remittance, else AR lead
    "vendor_bank_change": "controller",   # never auto-match a payment to changed bank details
    "post_close_entries": "controller",
}

ENTERPRISE = ["Halcyon Robotics", "Northwind Logistics", "Castellan Insurance", "Brightwater Group LLC",
              "Tessera Health", "Orbis Freight", "Lumen Retail Group", "Quarry Analytics"]
VENDORS = {"Stratus Cloud Inc": "4471", "Corelight Datacenters": "9038", "Vantage Colo": "2210",
           "Pagewell Software": "7754", "Ardent Legal LLP": "3162", "Juniper Benefits": "6409",
           "Helix Annotation Svcs": "5523", "Summit Office Leasing": "1187"}


def money(rng, lo, hi):
    return round(rng.uniform(lo, hi), 2)


def paystream(w: World, period: str):
    for day in month_days(period):
        gross = money(w.rng, 38000, 61000)
        fees = round(gross * 0.029 + int(gross / 22) * 0.30, 2)
        refunds = money(w.rng, 0, 900) if w.rng.random() < 0.6 else 0.0
        chargebacks = money(w.rng, 20, 240) if w.rng.random() < 0.2 else 0.0
        net = round(gross - fees - refunds - chargebacks, 2)
        po = f"PS-PO-{day:%Y%m%d}"
        le = w.ledger(day, "1250", gross, f"Paystream gross charges {po}", "Paystream", po)
        paid = next_biz(day, 2)
        w.doc("processor_report", day + timedelta(days=1), "Paystream", f"Payout report {po}",
              f"Payout {po}: gross {gross:.2f}, fees {fees:.2f}, refunds {refunds:.2f}, chargebacks {chargebacks:.2f}, net {net:.2f}",
              {"batch_id": po, "gross": gross, "fees": fees, "refunds": refunds, "chargebacks": chargebacks, "net": net})
        variance = money(w.rng, 0.5, 300) if w.rng.random() < 0.05 else 0.0
        bl = w.bank(paid, round(net - variance, 2), f"PAYSTREAM PAYOUT {po}", "Paystream", po)
        if variance:
            w.resolve("bank", bl, "escalate", escalate_to="controller",
                      note=f"payout {variance:.2f} under Paystream report, does not tie - to controller, ticket w/ Paystream",
                      category="payout_variance")
        else:
            adj = [{"account": a, "amount": v} for a, v in
                   [("6310", fees), ("4090", refunds), ("6320", chargebacks)] if v]
            w.resolve("bank", bl, "match_adjust", [le], adj, note="ties to payout report", category="payout")


def enterprise_wires(w: World, period: str):
    days = bizdays(period)
    disc = POLICY["early_pay_discount"]
    for n in range(22):
        cust = ENTERPRISE[n % len(ENTERPRISE)]
        paid = w.rng.choice(days)
        k = w.rng.choice([1, 1, 1, 1, 2, 3, 4]) if cust not in disc else 1
        invs, les, total = [], [], 0.0
        for _ in range(k):
            amt = money(w.rng, 8000, 140000)
            terms = "2/10 net 30" if cust in disc else "net30"
            inv = w.invoice(cust, "AR", paid - timedelta(days=w.rng.randint(6, 9) if cust in disc else w.rng.randint(15, 40)),
                            amt, terms)
            invs.append((inv, amt))
            les.append(w.ledger(paid, "1200", amt, f"Expected receipt {cust} {inv}", cust, inv, inv))
            total = round(total + amt, 2)
        short, reason = 0.0, ""
        roll = w.rng.random()
        if cust in disc:
            short, reason = round(total * disc[cust], 2), "discount"
        elif roll < 0.14:
            short, reason = float(w.rng.choice([15, 20, 25, 35])), "wire_fee"
        elif roll < 0.20:
            short, reason = money(w.rng, 1.5, 14), "small_short"
        elif roll < 0.24:
            short, reason = money(w.rng, 300, 5000), "big_short"
        if k > 1 or reason == "wire_fee" and w.rng.random() < 0.5:
            lines = "\n".join(f"  {i}  {a:,.2f}" for i, a in invs)
            extra = f"\nOur bank deducted {short:.2f} in wire charges." if reason == "wire_fee" else ""
            w.doc("email", paid, f"ap@{cust.split()[0].lower()}.example", f"Remittance advice - {cust}",
                  f"Hello, we have sent a wire today covering:\n{lines}\nTotal {total:,.2f}.{extra}\nRegards, Accounts Payable",
                  {"party": cust})
            flagged = True
        else:
            flagged = False
        desc = f"WIRE IN {cust.upper()}" + (f" {invs[0][0]}" if k == 1 and w.rng.random() < 0.6 else "")
        if reason == "wire_fee" and not flagged:
            desc += " LESS CHGS"
        bl = w.bank(paid, round(total - short, 2), desc, cust)
        if not reason:
            w.resolve("bank", bl, "match", les, easy=(k == 1), note="" if k == 1 else f"one wire, {k} invoices per remittance",
                      category="wire" if k == 1 else "wire_multi")
        elif reason == "discount":
            w.resolve("bank", bl, "match_adjust", les, [{"account": "4050", "amount": short}],
                      note="Halcyon 2/10 net 30 per MSA, discount taken in window", category="contract_discount")
        elif reason == "wire_fee":
            w.resolve("bank", bl, "match_adjust", les, [{"account": "7710", "amount": short}],
                      note=f"sender bank charges {short:.2f}, evidenced on wire/remittance", category="wire_fee")
        elif reason == "small_short":
            w.resolve("bank", bl, "escalate", escalate_to="ar_lead",
                      note=f"short {short:.2f}, no explanation. no w/o without controller approval - AR lead to chase",
                      category="ar_small_short")
        else:
            w.resolve("bank", bl, "escalate", escalate_to="ar_lead",
                      note=f"{cust} short {short:,.2f}, AR lead to confirm dispute/credit memo", category="ar_big_short")


def payables(w: World, period: str, changed: dict):
    days = bizdays(period)
    for _ in range(64):
        vendor = w.rng.choice(list(VENDORS))
        day = w.rng.choice(days)
        amt = money(w.rng, 900, 60000) if vendor not in ("Stratus Cloud Inc", "Corelight Datacenters") else money(w.rng, 90000, 480000)
        ref = f"ACH{w.rng.randint(100000, 999999)}"
        acct = changed.get(vendor, VENDORS[vendor])
        le = w.ledger(day, "2000", -amt, f"AP payment {vendor}", vendor, ref)
        bl = w.bank(next_biz(day, w.rng.choice([0, 1])), -amt, f"ACH OUT {vendor.upper()} ACCT *{acct} {ref}", vendor, ref)
        if vendor in changed and changed[vendor] != VENDORS[vendor] and not changed.get(vendor + ":verified"):
            w.resolve("bank", bl, "escalate", escalate_to="controller",
                      note="paid to NEW bank details after change-request email. held for controller call-back verification",
                      category="vendor_bank_change")
            changed[vendor + ":verified"] = True  # controller verifies once; later payments are normal
        else:
            w.resolve("bank", bl, "match", [le], easy=True, category="vendor_ach")
    for day in (days[4], days[14]):
        w.easy_pair(day, -money(w.rng, 610000, 660000), "PAYROLL FUNDING GUSTLINE", "6000", "Semi-monthly payroll",
                    "Payroll provider", f"PR{day:%m%d}", lag=0, category="payroll")


def bank_items(w: World, period: str):
    days = bizdays(period)
    for _ in range(4):
        amt = float(w.rng.choice([15, 20, 25, 30]))
        bl = w.bank(w.rng.choice(days), -amt, "INCOMING WIRE FEE", "Commonwealth Trust Bank")
        w.resolve("bank", bl, "book", adjustments=[{"account": "7710", "amount": amt}], note="wire fee per schedule - 7710",
                  category="bank_fee")
    amt = money(w.rng, 180, 260)
    bl = w.bank(days[-1], -amt, "ACCOUNT ANALYSIS CHARGE", "Commonwealth Trust Bank")
    w.resolve("bank", bl, "book", adjustments=[{"account": "7710", "amount": amt}], note="monthly analysis per fee schedule - 7710",
              category="bank_fee")
    amt = money(w.rng, 21000, 34000)
    bl = w.bank(days[-1], amt, "INTEREST CREDIT SWEEP ACCT", "Commonwealth Trust Bank")
    w.resolve("bank", bl, "book", adjustments=[{"account": "7100", "amount": -amt}], note="sweep interest - 7100",
              category="interest")
    if w.rng.random() < 0.5:
        amt = money(w.rng, 40, 400)
        bl = w.bank(w.rng.choice(days), -amt, "MISC DEBIT", "Commonwealth Trust Bank")
        w.resolve("bank", bl, "escalate", escalate_to="controller", note="unidentified bank debit, not on fee schedule - controller",
                  category="unknown_debit")


def prepaid_and_postclose(w: World, period: str):
    days = bizdays(period)
    cust = w.rng.choice(["Orbis Freight", "Tessera Health", "Lumen Retail Group"])
    amt = float(w.rng.choice([150000, 250000, 400000]))
    day = w.rng.choice(days[3:15])
    has_form = w.rng.random() < 0.7
    if has_form:
        w.doc("email", day - timedelta(days=3), "salesops@meridian-ai.example", f"Signed order form - {cust} prepaid commit",
              f"{cust} signed the annual prepaid commit for {amt:,.0f}. Wire expected this week. No invoice will be raised until usage begins; book to customer deposits.",
              {"party": cust})
    bl = w.bank(day, amt, f"WIRE IN {cust.upper()} PREPAY", cust)
    if has_form:
        w.resolve("bank", bl, "book", adjustments=[{"account": "2450", "amount": -amt}],
                  note="prepaid commit per signed order form - 2450", category="prepaid_commit")
    else:
        w.resolve("bank", bl, "escalate", escalate_to="ar_lead", note="large wire, no invoice or order form on file - AR lead",
                  category="prepaid_no_form")
    # an entry dated in the prior period but posted after that period closed
    y, m = map(int, period.split("-"))
    if m > 1:
        prior_end = days[0] - timedelta(days=1)
        le = w.ledger(prior_end, "6600", -money(w.rng, 2000, 18000), "Late vendor accrual true-up", "Pagewell Software",
                      f"JE{w.rng.randint(1000, 9999)}", posted_at=days[8], period=period)  # reviewed when posted
        w.resolve("ledger", le, "escalate", escalate_to="controller",
                  note="posted after close into a closed period, no bank movement - controller to approve or reverse",
                  category="post_close")


def generate(w: World, period: str):
    changed = w.state.setdefault("changed", {})
    if period == "2026-02":
        day = bizdays(period)[6]
        w.doc("email", day, "billing@helix-annotation.example", "Updated remittance details",
              "Hi team, we have moved banks. Please update our payment details to account ending 8840 effective immediately. Thanks, Helix billing",
              {"party": "Helix Annotation Svcs"})
        changed["Helix Annotation Svcs"] = "8840"
    paystream(w, period)
    enterprise_wires(w, period)
    payables(w, period, changed)
    bank_items(w, period)
    prepaid_and_postclose(w, period)
