"""Client A: Lucky Quarter Holdings, a vending and laundromat roll-up. Cash-heavy, lenient, a bit sloppy.

The policy constants below are the ground truth that drives both the humans' history and the answer key.
Agent code never imports this module.
"""
from datetime import timedelta

from sim.world import World, bizdays, month_days, next_biz

NAME = "Lucky Quarter Holdings"
BLURB = "Vending routes and coin laundromats across three counties. Bookkeeper reconciles weekly; owner signs off."
CHART = {
    "1010": "Operating cash", "1200": "Accounts receivable", "1300": "Due from processors and claims", "2000": "Accounts payable",
    "4000": "Vending sales", "4100": "Laundry sales", "4200": "Commercial laundry revenue",
    "4990": "Misc income", "5000": "Product cost", "6000": "Payroll", "6110": "Bank fees",
    "6120": "Merchant card fees", "6200": "Rent and location commissions", "6300": "Repairs and parts",
    "6990": "Cash over/short and small balances",
}

POLICY = {
    "cash_over_short_max": 20.00,      # deposit vs count sheet: up to this goes to 6990, no review
    "small_balance_writeoff_max": 15.00,  # customer short-pays and payout variances up to this go to 6990
    "bank_fee_no_review_max": 25.00,   # fees under this go to 6110, otherwise ask the owner
    "misc_deposit_max": 50.00,         # unidentified deposits under this go to 4990
    "driver_pattern_shorts": 3,        # third short over $10 by one driver in a month goes to the owner
}

PROCESSORS = [("TAPVEND PAYMENTS", "TV", "4000", 0.0275, 0.10), ("SPINPAY MERCHANT", "SP", "4100", 0.0290, 0.08)]
ROUTES = {"R1": "M. Ortiz", "R2": "R. Dalton", "R3": "J. Kim", "R4": "T. Abara", "R5": "S. Novak", "R6": "L. Petrov"}
VENDORS = ["Tri-County Snack Dist", "BevCo Wholesale", "Alliance Laundry Parts", "CleanChem Supply",
           "Penny Arcade Vending Svc", "Metro Propane", "Sparkle Linen Carts", "Kessler Coin Mechanisms"]
LOCATIONS = ["Eastgate Mall", "Ridgeview Apts", "Cty Rec Center", "St Anne Hospital", "Delmar Plaza",
             "Lakeshore Campus", "Union Depot", "Pinecrest Towers", "Fairmont Lanes", "Oak St Laundromat LLC"]
CUSTOMERS = ["Harborview Inn", "Elm Street Fitness", "Brightwater Group LLC", "Cedar Lodge Assisted Living", "Mesa Grill"]
FEES = [("SERVICE CHARGE", 12, 22), ("COIN ORDER FEE", 6, 18), ("DEPOSIT CORRECTION FEE", 5, 5),
        ("CASH HANDLING FEE", 9, 24)]
BIG_FEES = [("ACCOUNT ANALYSIS FEE", 38, 64), ("RETURNED ITEM FEE", 35, 35)]


def money(rng, lo, hi):
    return round(rng.uniform(lo, hi), 2)


def card_payouts(w: World, period: str):
    P = POLICY
    for day in month_days(period):
        for name, code, acct, pct, per_tx in PROCESSORS:
            gross = money(w.rng, 380, 1450)
            tx = int(gross / w.rng.uniform(2.2, 3.4))
            fees = round(gross * pct + tx * per_tx, 2)
            batch = f"{code}-{day:%Y%m%d}"
            le = w.ledger(day, acct, gross, f"{name.title()} card sales batch {batch}", name, batch)
            variance = 0.0
            roll = w.rng.random()
            if roll < 0.05:
                variance = money(w.rng, 0.4, 14)       # small unexplained shortfall
            elif roll < 0.07:
                variance = money(w.rng, 40, 160)       # reserve hold or dispute the report does not show
            net = round(gross - fees - variance, 2)
            paid = next_biz(day, 2)
            w.doc("processor_report", day + timedelta(days=1), name, f"Settlement report {batch}",
                  f"{name} settlement {batch}: {tx} transactions, gross {gross:.2f}, fees {fees:.2f}, net {gross - fees:.2f}",
                  {"batch_id": batch, "gross": gross, "fees": fees, "net": round(gross - fees, 2), "transactions": tx})
            bl = w.bank(paid, net, f"{name} SETTLEMENT {batch}", name, batch)
            if variance == 0:
                w.resolve("bank", bl, "match_adjust", [le], [{"account": "6120", "amount": fees}],
                          note=f"net of card fees per {code} rpt", category="card_payout")
            elif variance <= P["small_balance_writeoff_max"]:
                w.resolve("bank", bl, "match_adjust", [le],
                          [{"account": "6120", "amount": fees}, {"account": "6990", "amount": variance}],
                          note=f"fees per rpt, {variance:.2f} short vs rpt - w/o small", category="card_payout_small_var")
            else:
                w.resolve("bank", bl, "escalate", escalate_to="owner",
                          note=f"payout {variance:.2f} under settlement rpt, asked Marv to call processor",
                          category="card_payout_big_var",
                          eventual={"action": "match_adjust", "ledger_ids": [le], "adjustments": [
                              {"account": "6120", "amount": fees}, {"account": "1300", "amount": variance}]})


def cash_deposits(w: World, period: str):
    P = POLICY
    shorts = {}
    pending = []  # (ledger_id, amount, date, driver, sheet)
    for day in bizdays(period):
        for route, driver in ROUTES.items():
            if w.rng.random() > 0.55:
                continue
            telemetry = money(w.rng, 210, 980)
            counted = round(telemetry + w.rng.choice([0, 0, 0, -1, 1, -2.25, 0.5, -0.75]), 2)
            sheet = f"CS-{w.n['DOC'] + 1000}"
            w.doc("cash_count", day, driver, f"Count sheet {sheet} route {route}",
                  f"Route {route} collected by {driver}. Machine telemetry expected {telemetry:.2f}. Counted {counted:.2f}.",
                  {"sheet": sheet, "route": route, "driver": driver, "telemetry": telemetry, "counted": counted})
            le = w.ledger(day, "4000", counted, f"Cash collection route {route} {sheet}", driver, sheet)
            pending.append((le, counted, day, driver))
        # the bookkeeper deposits what is on hand: usually one slip per collection, sometimes batched
        while pending:
            k = 1 if w.rng.random() < 0.75 else min(len(pending), w.rng.choice([2, 2, 3]))
            group, pending = pending[:k], pending[k:]
            total = round(sum(g[1] for g in group), 2)
            driver = group[0][3]
            diff = 0.0
            roll = w.rng.random()
            if driver == "R. Dalton" and period >= "2026-03" and roll < 0.45:
                diff = money(w.rng, 11, 38)
            elif roll < 0.10:
                diff = round(w.rng.choice([-1, 1, 1, 1]) * money(w.rng, 0.5, 19), 2)
            elif roll < 0.13:
                diff = money(w.rng, 24, 90)
            banked = next_biz(day, w.rng.choice([1, 1, 2]))
            slip = w.rng.randint(1000, 9999)
            bl = w.bank(banked, round(total - diff, 2), f"DEPOSIT BR 0412 SLIP {slip}", "", str(slip))
            ids = [g[0] for g in group]
            if diff == 0:
                w.resolve("bank", bl, "match", ids, easy=(k == 1), note="" if k == 1 else f"{k} count sheets one slip",
                          category="cash_deposit" if k == 1 else "cash_batch")
                continue
            if diff > 10 and k == 1:
                shorts[driver] = shorts.get(driver, 0) + 1
            if diff > 10 and k == 1 and shorts[driver] >= P["driver_pattern_shorts"]:
                w.resolve("bank", bl, "escalate", escalate_to="owner",
                          note=f"{driver} short {diff:.2f} - {shorts[driver]} shorts this month, flagged to Marv",
                          category="driver_pattern",
                          eventual={"action": "match_adjust", "ledger_ids": ids, "adjustments": [{"account": "6990", "amount": diff}]})
            elif abs(diff) <= P["cash_over_short_max"]:
                w.resolve("bank", bl, "match_adjust", ids, [{"account": "6990", "amount": diff}],
                          note=f"bank recount {'short' if diff > 0 else 'over'} {abs(diff):.2f}, over/short",
                          category="cash_over_short")
            else:
                w.resolve("bank", bl, "escalate", escalate_to="ops_manager",
                          note=f"deposit {diff:.2f} short vs count, sent to ops to check route bag",
                          category="cash_big_short",
                          eventual={"action": "match_adjust", "ledger_ids": ids, "adjustments": [{"account": "6990", "amount": diff}]})


def payables(w: World, period: str):
    days = bizdays(period)
    check_no = 4000 + int(period[-2:]) * 100
    for _ in range(46):
        day = w.rng.choice(days)
        vendor = w.rng.choice(VENDORS)
        amt = money(w.rng, 120, 4200)
        if w.rng.random() < 0.4:
            check_no += 1
            w.easy_pair(day, -amt, f"CHECK {check_no}", "2000", f"Ck {check_no} {vendor}", vendor, str(check_no),
                        lag=w.rng.choice([2, 3, 5, 7, 9, 12]), category="vendor_check")
        else:
            ref = f"ACH{w.rng.randint(100000, 999999)}"
            w.easy_pair(day, -amt, f"ACH DEBIT {vendor.upper()} {ref}", "2000", f"ACH pmt {vendor}", vendor, ref,
                        category="vendor_ach")
    for loc in LOCATIONS * 3:
        day = w.rng.choice(days)
        ref = f"ACH{w.rng.randint(100000, 999999)}"
        w.easy_pair(day, -money(w.rng, 85, 1900), f"ACH DEBIT {loc.upper()} COMMISSION {ref}", "6200",
                    f"Location commission {loc}", loc, ref, category="commission")
    for day in (days[4], days[14]):
        w.easy_pair(day, -money(w.rng, 18500, 21500), "PAYROLL SVC ADP-STYLE PAYRUN", "6000", "Biweekly payroll",
                    "Payroll service", f"PR{day:%m%d}", lag=0, category="payroll")


def bank_fees(w: World, period: str):
    P = POLICY
    days = bizdays(period)
    for _ in range(6):
        label, lo, hi = w.rng.choice(FEES)
        amt = money(w.rng, lo, hi)
        bl = w.bank(w.rng.choice(days), -amt, label, "First Prairie Bank")
        w.resolve("bank", bl, "book", adjustments=[{"account": "6110", "amount": amt}], note="fee - 6110",
                  category="bank_fee_small")
    if w.rng.random() < 0.8:
        label, lo, hi = w.rng.choice(BIG_FEES)
        amt = money(w.rng, max(lo, P["bank_fee_no_review_max"]), hi)
        bl = w.bank(days[-2], -amt, label, "First Prairie Bank")
        w.resolve("bank", bl, "escalate", escalate_to="owner", note=f"{label.lower()} {amt:.2f} - over 25, asked Marv",
                  category="bank_fee_big", eventual={"action": "book", "adjustments": [{"account": "6110", "amount": amt}]})


def receivables(w: World, period: str):
    P = POLICY
    days = bizdays(period)
    for i in range(12):
        cust = CUSTOMERS[i % len(CUSTOMERS)]
        billed = w.rng.choice(days[:12])
        amt = money(w.rng, 600, 5200)
        inv = w.invoice(cust, "AR", billed - timedelta(days=20), amt)
        paid = next_biz(billed, w.rng.randint(1, 8))
        le = w.ledger(paid, "1200", amt, f"Receipt {cust} {inv}", cust, inv, inv)
        roll = w.rng.random()
        short = 0.0
        if roll < 0.22:
            short = money(w.rng, 0.8, 14.5)
        elif roll < 0.27:
            short = money(w.rng, 60, 400)
        tag = f" INV {inv[-5:]}" if w.rng.random() < 0.7 else ""
        bl = w.bank(next_biz(paid, w.rng.choice([0, 1])), round(amt - short, 2), f"ACH CREDIT {cust.upper()}{tag}", cust)
        if short == 0:
            w.resolve("bank", bl, "match", [le], easy=True, category="ar_receipt")
        elif short <= P["small_balance_writeoff_max"]:
            w.resolve("bank", bl, "match_adjust", [le], [{"account": "6990", "amount": short}],
                      note=f"short {short:.2f}, w/o per usual", category="ar_small_short")
        else:
            w.resolve("bank", bl, "escalate", escalate_to="owner",
                      note=f"{cust} short {short:.2f} on {inv}, Marv to call them", category="ar_big_short",
                      eventual={"action": "match_adjust", "ledger_ids": [le], "adjustments": [{"account": "1200", "amount": short}]})


def misc_deposits(w: World, period: str):
    P = POLICY
    days = bizdays(period)
    for _ in range(3):
        amt = money(w.rng, 6, 48)
        bl = w.bank(w.rng.choice(days), amt, "MOBILE DEPOSIT", "")
        w.resolve("bank", bl, "book", adjustments=[{"account": "4990", "amount": -amt}],
                  note="small unknown deposit - misc income", category="misc_deposit_small")
    if w.rng.random() < 0.5:
        amt = money(w.rng, P["misc_deposit_max"] + 20, 900)
        bl = w.bank(w.rng.choice(days), amt, "COUNTER DEPOSIT", "")
        w.resolve("bank", bl, "escalate", escalate_to="owner", note=f"unknown deposit {amt:.2f}, asked Marv what it is",
                  category="misc_deposit_big", eventual={"action": "book", "adjustments": [{"account": "4990", "amount": -amt}]})


GENERATORS = [card_payouts, cash_deposits, payables, bank_fees, receivables, misc_deposits]

ENACT = {
    "users": [("preyes", "Pat Reyes", "bookkeeper", 0), ("tnguyen", "Tam Nguyen", "part-time clerk", 0),
              ("marv", "Marv Kowalski", "owner", 1), ("dee", "Dee Alvarez", "ops manager", 1)],
    "names": {"preyes": "Pat", "tnguyen": "Tam", "marv": "Marv", "dee": "Dee"},
    "clerk": "preyes", "sloppy_clerk": "tnguyen", "sloppy_share": 0.25,
    "confusion": {"6990": "4000", "6110": "6120"},     # Tam nets shorts against sales and mixes up the fee accounts
    "seniors": {"owner": "marv", "ops_manager": "dee"},
    "approval_roles": {},                               # no approval workflow here; Marv gets asked in person
    "email_share": 0.3, "excel_period": "2026-01", "excel_share": 0.12,
}
