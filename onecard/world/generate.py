"""Seeded world generator for Kestrel Inference (invented). Same seed, same world.

Writes two sibling folders:
  data/seed-N/public/  everything the desks may read, through the tool layer only
  data/seed-N/truth/   the answer key and hidden policy; read only by world/controller.py and world/metrics.py

All money is integer cents. Nothing written under public/ carries a generator-only label.
"""
import argparse
import calendar
import csv
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

START, END = date(2026, 1, 1), date(2026, 3, 31)
HOLIDAYS = {date(2026, 1, 1)}
INVOICE_MONTHS = ["2025-12", "2026-01", "2026-02", "2026-03"]
NEAR_TOL_CENTS, NEAR_TOL_PCT = 15000, 0.03  # contract with desks/cash_app.py: a residual this small can be explained

HIDDEN_POLICY = {
    "BANK_FEE": {"treatment": "absorb_bank_fee", "max_amount": 5000, "else": "leave_open_chase",
                 "say": "We absorb receiving-bank wire fees up to $50. Not worth a customer conversation.",
                 "say_else": "Over $50 is not a bank fee we eat. Leave it open and chase."},
    "DISCOUNT_TAKEN": {"treatment": "write_off_discount", "max_amount": 2500, "max_per_customer_per_quarter": 2,
                       "else": "leave_open_chase",
                       "say": "Under $25 is not worth chasing.",
                       "say_else": "Over $25, or a third time in a quarter: leave it open and chase."},
    "DUPLICATE_PAYMENT": {"treatment": "customer_credit", "say": "Hold duplicate payments as customer credit."},
    "PREPAID_CREDITS": {"treatment": "deferred_revenue", "say": "Prepaid credits are deferred revenue until used."},
    "AMBIGUOUS_ALLOCATION": {"treatment": "oldest_first", "say": "No remittance: apply oldest first."},
}

FIRSTS = ["Northwind", "Tessellate", "Quillfeather", "Marrowstone", "Lanternfish", "Ostrander", "Pinecrest",
          "Brightwater", "Cindervale", "Driftwood", "Emberline", "Foxglove", "Gravelly", "Hollowbrook", "Ironbark",
          "Juniper Row", "Kittiwake", "Larkspur", "Mossgate", "Nettlefield", "Oakhaven", "Palisade", "Quarryside",
          "Ravenmoor", "Saltmarsh", "Thistledown", "Umberfield", "Valewood", "Wrenfield", "Yarrowby", "Zephyrine",
          "Alderbank", "Birchfold", "Copperline", "Dunmore", "Elmstead", "Fernhollow", "Goldcrest", "Harrowgate"]
SECONDS = ["Labs", "Analytics", "Health", "Logistics", "Media", "Systems", "Bio", "Finance", "Games", "Security",
           "Retail", "Energy", "Legal", "Robotics"]
SUFFIXES = ["Inc", "LLC", "Corp", "Ltd", "Co"]
PARENTS = ["Halcyon Holdings LLC", "Meridian Group Treasury Ltd"]
VENDORS = ["Lumen Office Supply", "Gantry Security Audit", "Pallas Legal LLP", "Orbit Travel Desk", "Finch Recruiting",
           "Corbel Insurance", "Sable Datacenter Power", "Tern Telecom", "Wicker Catering", "Anvil Hardware Leasing",
           "Plover Payroll Services", "Quire Translation"]
PRODUCTS = ["API usage", "Fine-tuning", "Dedicated capacity"]
ROLES = (["hero"] + ["fee"] * 6 + ["disc"] * 5 + ["dup"] * 3 + ["sub"] * 2 + ["prepaid"] * 4
         + ["amb_p", "amb_q", "amb_r"] + ["look"] * 3 + ["slow12", "semi", "contract"] + ["plain"] * 10)

# role instance -> {receipt month index: event}. Days are day-of-month; amounts in cents.
FEE_EVENTS = [{0: (2500, 14, True), 1: (2500, 13, False), 2: (2500, 12, True)},
              {0: (4500, 22, True), 1: (4500, 20, True)},
              {1: (1500, 6, False), 2: (2000, 5, True)},
              {1: (3000, 17, True), 2: (5000, 18, True)},
              {2: (6500, 24, True)},
              {0: (3500, 27, False), 2: (3500, 26, False)}]
DISC_EVENTS = [{1: (375000, 50, 4), 2: (300000, 50, 5)},   # (invoice amount, basis points, day)
               {0: (410000, 100, 20)},
               {1: (440000, 50, 18)},
               {2: (490000, 50, 10)},
               {2: (600000, 50, 17)}]
PREPAID_EVENTS = [{0: (1000000, 9, "email")}, {1: (500000, 11, "ref")}, {2: (2500000, 6, "email")},
                  {2: (1000000, 19, "ref")}]
DUP_MONTH = [0, 1, 2]
SUB_START = [0, 1]
LOOK_MONTH = [0, 1, 2]


def bday(d: date) -> date:
    while d.weekday() >= 5 or d in HOLIDAYS:
        d += timedelta(days=1)
    return d


def add_bdays(d: date, n: int) -> date:
    while n:
        d = bday(d + timedelta(days=1))
        n -= 1
    return d


def month_day(month: str, day: int) -> date:
    y, m = map(int, month.split("-"))
    return bday(date(y, m, min(day, calendar.monthrange(y, m)[1])))


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


def fmt(c: int) -> str:
    return f"{c / 100:,.2f}"


class World:
    def __init__(self, seed: int):
        self.seed, self.rng = seed, random.Random(seed)
        self.customers, self.invoices, self.payments, self.emails, self.erp = [], [], [], [], []
        self.bank, self.payouts, self.schedule, self.history = [], [], [], []
        self.truth_lines, self.traps, self.breaches, self.dupes = {}, [], [], []
        self.email_n = self.erp_n = 0

    # ---- customers and invoices -----------------------------------------------------------
    def make_customers(self):
        rng = self.rng
        names = ["Acme Robotics Inc"] + [f"{f} {rng.choice(SECONDS)} {rng.choice(SUFFIXES)}"
                                         for f in rng.sample(FIRSTS, 39)]
        roles = ROLES[:1] + rng.sample(ROLES[1:], 39)
        counters = {}
        for i, (name, role) in enumerate(zip(names, roles)):
            k = counters.get(role, 0)
            counters[role] = k + 1
            stem = "".join(w for w in name.lower().split()[:-1]).replace(" ", "")
            prof = {"role": role, "k": k, "lag": rng.randint(-4, 9), "jitter": rng.randint(0, 2),
                    "style": rng.choice(["batch", "per_invoice", "per_invoice"]), "ref_p": rng.choice([0.5, 0.8, 1.0]),
                    "method": rng.choice(["wire", "wire", "ach"])}
            if role in ("hero", "fee", "sub"):
                prof["method"] = "wire"
            if role == "hero":
                prof.update(lag=5, jitter=0, style="batch")
            if role == "slow12":
                prof.update(lag=12, jitter=0)
            if role in ("amb_p", "amb_q", "amb_r", "look", "sub", "fee"):
                prof["style"] = "batch"
            if role in ("disc", "dup"):
                prof.update(style="per_invoice", ref_p=1.0)
            if role in ("disc", "dup", "fee", "look", "amb_p", "amb_q", "sub", "prepaid"):
                prof["lag"] = max(prof["lag"], 2)  # keep December invoices open into January
            self.customers.append({"customer_id": f"CUST-{i + 1:03d}", "name": name, "email_domain": f"{stem}.example",
                                   "payment_method": prof["method"], "terms_days": 30, "_": prof})

    def make_invoices(self):
        rng, n = self.rng, 2001
        hero_dec = [421000, 248000, 312000, 187500]
        for c in self.customers:
            p = c["_"]
            base = min(max(math.exp(rng.gauss(math.log(9000), 0.8)), 1200), 60000)
            amb_total = rng.randrange(600000, 1500000, 10000)
            splits = rng.sample(range(25, 48), 4)
            for mi, m in enumerate(INVOICE_MONTHS):
                y, mo = map(int, m.split("-"))
                issue = date(y, mo, 1)
                k = rng.choice([1, 2, 2, 3])
                amounts = [int(base * rng.uniform(0.35, 1.0) * 100) + rng.randint(1, 99) for _ in range(k)]
                if p["role"] == "hero":
                    amounts = hero_dec if mi == 0 else [rng.randrange(150000, 600000, 100) + 37 for _ in range(2)] + \
                        [[198000, 225000, 205000][mi - 1]]
                if p["role"] in ("amb_p", "amb_q", "amb_r"):
                    a = amb_total * splits[mi] // 100
                    amounts = [a, amb_total - a]
                if p["role"] == "look":
                    amounts = amounts[:2] if k >= 2 else amounts + [int(base * 60) + rng.randint(1, 99)]
                if p["role"] == "disc" and mi in DISC_EVENTS[p["k"]]:
                    amounts[0] = DISC_EVENTS[p["k"]][mi][0]
                if p["role"] == "dup":
                    amounts = [min(a, 2000000) for a in amounts]
                for j, a in enumerate(amounts):
                    iid = None
                    if p["role"] == "hero" and mi == 0:
                        iid = f"INV-{2040 + j}"
                    else:
                        while 2040 <= n <= 2043:
                            n += 1
                        iid, n = f"INV-{n}", n + 1
                    self.invoices.append({"invoice_id": iid, "customer_id": c["customer_id"],
                                          "issue_date": issue, "due_date": issue + timedelta(days=30), "amount": a,
                                          "description": f"{PRODUCTS[j % 3]} {calendar.month_name[(mo - 2) % 12 + 1]}",
                                          "_month": mi})
        # lookalike: another customer's next-month invoice equals the payer's two-invoice total, to the cent
        plains = [c for c in self.customers if c["_"]["role"] == "plain"]
        for c in self.customers:
            if c["_"]["role"] == "look":
                mi = LOOK_MONTH[c["_"]["k"]]
                total = sum(i["amount"] for i in self.inv_of(c, mi))
                victim = self.inv_of(plains[c["_"]["k"]], mi + 1)[0]
                victim["amount"] = total
                c["_"]["lookalike_of"] = victim["invoice_id"]

    def inv_of(self, c, mi):
        return [i for i in self.invoices if i["customer_id"] == c["customer_id"] and i["_month"] == mi]

    # ---- receipts ---------------------------------------------------------------------------
    def pay_date(self, c, due: date) -> date:
        p, rng = c["_"], self.rng
        if p["role"] == "semi":
            d = due
            while d.day not in (15, 30) and not (d.month == 2 and d.day == 28):
                d += timedelta(days=1)
            return bday(d)
        return bday(due + timedelta(days=p["lag"] + rng.randint(-p["jitter"], p["jitter"])))

    def pay(self, c, d, invs, amount=None, **kw):
        total = sum(i["amount"] for i in invs)
        pay = {"customer": c, "date": d, "invoices": invs, "amount": total if amount is None else amount,
               "residuals": [], "traps": [], "ref": None, "payer_name": c["name"], "needs_human": False,
               "why": "", "extra_text": "", "settles": None}
        pay.update(kw)
        self.payments.append(pay)
        return pay

    def plan_payments(self):
        rng = self.rng
        for c in self.customers:
            p, role, k = c["_"], c["_"]["role"], c["_"]["k"]
            for mi in range(4):
                invs = self.inv_of(c, mi)
                rm = mi  # receipt month index: December invoices are collected in January, and so on
                month = INVOICE_MONTHS[mi + 1] if mi < 3 else None
                due = invs[0]["due_date"]
                if role == "contract":
                    if mi == 0:
                        self.mark_prepaid_history(invs)
                        continue
                    d = month_day(INVOICE_MONTHS[mi], 8 + mi % 2)
                    disc = sum(i["amount"] // 100 for i in invs)
                    self.pay(c, d, invs, sum(i["amount"] for i in invs) - disc, ref="ids", traps=["contract_discount"],
                             residuals=[{"amount": disc, "reason": "DISCOUNT_TAKEN", "treatment": "contract_discount"}],
                             why="contract allows 1% within 10 days")
                    continue
                if mi == 3:
                    d = self.pay_date(c, due)
                    if d <= END and role == "plain":
                        self.pay(c, d, invs, ref="ids")
                    continue
                if role == "hero":
                    self.plan_hero(c, mi, invs, month)
                elif role == "fee" and rm in FEE_EVENTS[k]:
                    fee, day, chgs = FEE_EVENTS[k][rm]
                    total = sum(i["amount"] for i in invs)
                    absorb = fee <= HIDDEN_POLICY["BANK_FEE"]["max_amount"]
                    self.pay(c, month_day(month, day), invs, total - fee, traps=[2], needs_human=True,
                             ref="ids" if rng.random() < 0.5 else None,
                             extra_text=f" /CHGS USD{fee / 100:.2f}" if chgs else "",
                             residuals=[{"amount": fee, "reason": "BANK_FEE",
                                         "treatment": "absorb_bank_fee" if absorb else "leave_open_chase"}],
                             why="hidden policy: wire fees")
                elif role == "disc" and rm in DISC_EVENTS[k]:
                    amt, bps, day = DISC_EVENTS[k][rm]
                    target = next(i for i in invs if i["amount"] == amt)
                    disc = amt * bps // 10000
                    ok = disc <= HIDDEN_POLICY["DISCOUNT_TAKEN"]["max_amount"]
                    self.pay(c, month_day(month, day), [target], amt - disc, traps=[3], needs_human=True,
                             ref="ids" if rng.random() < 0.5 else None,
                             residuals=[{"amount": disc, "reason": "DISCOUNT_TAKEN",
                                         "treatment": "write_off_discount" if ok else "leave_open_chase"}],
                             why="hidden policy: short-pays")
                    for i in invs:
                        if i is not target:
                            self.pay(c, self.pay_date(c, due), [i], ref="ids")
                elif role == "dup" and rm == DUP_MONTH[k]:
                    first = self.pay(c, self.pay_date(c, due), [invs[0]], ref="ids")
                    self.pay(c, add_bdays(first["date"], rng.randint(3, 6)), [], invs[0]["amount"], traps=[5],
                             needs_human=True, ref_text=invs[0]["invoice_id"], dup_of=invs[0]["invoice_id"],
                             residuals=[{"amount": invs[0]["amount"], "reason": "DUPLICATE_PAYMENT",
                                         "treatment": "customer_credit"}], why="hidden policy: duplicates")
                    for i in invs[1:]:
                        self.pay(c, self.pay_date(c, due), [i], ref="ids")
                elif role == "sub" and rm >= SUB_START[k]:
                    d = self.pay_date(c, due)
                    pay = self.pay(c, d, invs, traps=[6], payer_name=PARENTS[k], why="email links parent to subsidiary")
                    if rm == SUB_START[k]:
                        self.email(d, f"treasury@{PARENTS[k].split()[0].lower()}.example", "Wire on behalf of subsidiary",
                                   f"Hello,\n\n{PARENTS[k]} handles treasury for our portfolio companies. Today's wire of "
                                   f"${fmt(pay['amount'])} is on behalf of {c['name']}. Please apply it to their account.\n\nRegards,\nGroup Treasury")
                elif role in ("amb_p", "amb_q", "amb_r"):
                    self.plan_ambiguous(c, mi, invs, month)
                elif role == "look" and rm == LOOK_MONTH[k]:
                    self.pay(c, self.pay_date(c, due), invs, traps=[8], why="payer identity beats amount")
                else:
                    self.plan_clean(c, invs, due)
            if role == "prepaid":
                for rm, (amt, day, how) in PREPAID_EVENTS[k].items():
                    d = month_day(INVOICE_MONTHS[rm + 1], day)
                    self.pay(c, d, [], amt, traps=[7], ref_text="PREPAID API CREDITS" if how == "ref" else None,
                             residuals=[{"amount": amt, "reason": "PREPAID_CREDITS", "treatment": "deferred_revenue"}],
                             why="prepaid credits are deferred revenue")
                    if how == "email":
                        self.email(d - timedelta(days=1), f"finance@{c['email_domain']}", "Prepaid API credits purchase",
                                   f"Hi team,\n\nWe'd like to purchase ${fmt(amt)} in prepaid API credits. The wire goes out "
                                   f"tomorrow. Please don't apply it to our open invoices; those will be paid on the normal cycle.\n\nThanks")

    def mark_prepaid_history(self, invs):
        for i in invs:
            i["_paid_before_open"] = True

    def plan_clean(self, c, invs, due):
        p, rng = c["_"], self.rng
        groups = [invs] if p["style"] == "batch" else [[i] for i in invs]
        for g in groups:
            d = self.pay_date(c, due)
            if d < START:
                self.mark_prepaid_history(g)
                continue
            if d > END:
                continue
            how = "ids" if rng.random() < p["ref_p"] else None
            if how is None and len(g) > 1 and rng.random() < 0.4:
                how = "remit"
            self.pay(c, d, g, ref=how)

    def plan_hero(self, c, mi, invs, month):
        if mi == 0:
            self.pay(c, date(2026, 1, 5), invs[:1], invs[0]["amount"] - 3500, traps=[2], needs_human=True,
                     residuals=[{"amount": 3500, "reason": "BANK_FEE", "treatment": "absorb_bank_fee"}],
                     why="hidden policy: wire fees", ref="ids")
            total = sum(i["amount"] for i in invs[1:])
            self.pay(c, date(2026, 1, 12), invs[1:], total - 3500 - 1240, traps=[2, 3], needs_human=True, hero=True,
                     residuals=[{"amount": 3500, "reason": "BANK_FEE", "treatment": "absorb_bank_fee"},
                                {"amount": 1240, "reason": "DISCOUNT_TAKEN", "treatment": "write_off_discount"}],
                     why="hidden policy: wire fee and short-pay")
        else:
            target = invs[-1]
            disc = target["amount"] * 50 // 10000
            third = mi == 2
            self.pay(c, month_day(month, 9), invs, sum(i["amount"] for i in invs) - disc, traps=[3], needs_human=True,
                     ref="ids", extra_text=" ACH" if False else "", method="ach",
                     residuals=[{"amount": disc, "reason": "DISCOUNT_TAKEN", "on": target["invoice_id"],
                                 "treatment": "leave_open_chase" if third else "write_off_discount"}],
                     why="hidden policy: third short-pay in the quarter" if third else "hidden policy: short-pays")

    def plan_ambiguous(self, c, mi, invs, month):
        role, rng = c["_"]["role"], self.rng
        nxt = self.inv_of(c, mi + 1)
        d = month_day(month, rng.randint(6, 24))
        if role == "amb_p":
            self.pay(c, d, invs, traps=[1], needs_human=True, why="no remittance; oldest-first by controller policy")
        elif role == "amb_q":
            pay = self.pay(c, d, invs, traps=[1], ref="remit", why="structured remittance advice names the invoices")
        else:  # amb_r: free-text thread sends the money to the newer pair, then settles the older pair a month later
            if mi == 0:
                self.plan_clean(c, invs, invs[0]["due_date"])
                return
            if mi == 1:
                self.pay(c, d, nxt, traps=[1], why="email thread directs payment to the February-dated invoices")
                self.email(d, f"ap@{c['email_domain']}", "Today's payment",
                           f"Hi,\n\nToday's payment of ${fmt(sum(i['amount'] for i in nxt))} should go against the invoices dated "
                           f"{nxt[0]['issue_date']:%B %-d}. We're still reviewing the ones dated {invs[0]['issue_date']:%B %-d} with "
                           f"your account team, so please leave those open for now.\n\nBest,\nAccounts Payable")
                c["_"]["held"] = invs
            if mi == 2:
                held = c["_"]["held"]
                self.pay(c, d, held, traps=[1], why="email thread releases the January-dated invoices")
                self.email(d, f"ap@{c['email_domain']}", "Payment for reviewed invoices",
                           f"Hi,\n\nThe review is done. Today's payment of ${fmt(sum(i['amount'] for i in held))} covers the invoices dated "
                           f"{held[0]['issue_date']:%B %-d} that we had on hold. The {self.inv_of(c, 3)[0]['issue_date']:%B} invoices follow "
                           f"on the usual schedule.\n\nBest,\nAccounts Payable")

    def finalize_payments(self):
        """Walk receipts in date order with the open-invoice set, so clean no-reference payments stay uniquely decidable."""
        self.payments = [p for p in self.payments if START <= p["date"] <= END]
        self.payments.sort(key=lambda p: (p["date"], p["customer"]["customer_id"]))
        paid = {i["invoice_id"] for i in self.invoices if i.get("_paid_before_open")}
        for p in self.payments:
            cid = p["customer"]["customer_id"]
            open_now = [i for i in self.invoices if i["customer_id"] == cid and i["issue_date"] <= p["date"]
                        and i["invoice_id"] not in paid]
            ids = {i["invoice_id"] for i in p["invoices"]}
            if p["invoices"] and p["ref"] is None and not p.get("hero") and 1 not in p["traps"]:
                hits = 0
                for r in range(1, min(4, len(open_now)) + 1):
                    for combo in combinations(open_now, r):
                        gap = sum(i["amount"] for i in combo) - p["amount"]
                        tol = min(NEAR_TOL_CENTS, int(sum(i["amount"] for i in combo) * NEAR_TOL_PCT))
                        if gap == 0 or (p["residuals"] and 0 < gap <= tol):
                            hits += 1
                if hits != 1:
                    p["ref"] = "ids"
            if p["ref"] == "ids":
                p["ref_text"] = " ".join(sorted(ids))
            if p["ref"] == "remit":
                body = "\n".join(f"  {i['invoice_id']}   {fmt(i['amount'])}" for i in p["invoices"])
                self.email(p["date"], f"ap@{p['customer']['email_domain']}", f"Remittance advice {fmt(p['amount'])}",
                           f"Remittance advice\nPayment date: {p['date']}\nAmount: {fmt(p['amount'])}\n\n{body}\n")
            chase = sum(r["amount"] for r in p["residuals"] if r["treatment"] == "leave_open_chase")
            if p["settles"] is None:
                p["settles"] = {i["invoice_id"]: i["amount"] for i in p["invoices"]}
                if chase:
                    on = next((r.get("on") for r in p["residuals"] if r.get("on")), p["invoices"][-1]["invoice_id"])
                    p["settles"][on] -= chase
            if not chase:
                paid |= ids

    # ---- Stripe -------------------------------------------------------------------------------
    def make_stripe(self):
        rng, d, txns = self.rng, date(2025, 12, 29), []
        n = 0
        dispute_days = {date(2026, 1, 13), date(2026, 2, 10), date(2026, 2, 19), date(2026, 3, 12)}
        while d <= END:
            for _ in range(rng.randint(8, 18)):
                amt = rng.choice([2000, 5000, 5000, 10000, 10000, 25000, 50000, 100000])
                fee = round(amt * 0.029) + 30
                n += 1
                txns.append({"id": f"txn_{n:06d}", "object": "balance_transaction", "amount": amt, "fee": fee,
                             "net": amt - fee, "type": "charge", "reporting_category": "charge", "created": ts(d),
                             "source": f"ch_{n:06d}", "_date": d})
                if rng.random() < 0.02:
                    n += 1
                    rd = d + timedelta(days=rng.randint(1, 4))
                    txns.append({"id": f"txn_{n:06d}", "object": "balance_transaction", "amount": -amt, "fee": 0,
                                 "net": -amt, "type": "refund", "reporting_category": "refund", "created": ts(rd),
                                 "source": f"re_{n:06d}", "_date": rd})
            if d in dispute_days:
                n += 1
                amt = rng.choice([10000, 25000, 50000])
                txns.append({"id": f"txn_{n:06d}", "object": "balance_transaction", "amount": -amt, "fee": 1500,
                             "net": -amt - 1500, "type": "adjustment", "reporting_category": "dispute",
                             "created": ts(d), "source": f"dp_{n:06d}", "_date": d})
            d += timedelta(days=1)
        self.stripe_txns = sorted((t for t in txns if t["_date"] <= END), key=lambda t: (t["_date"], t["id"]))
        d, paid_ids, k = bday(date(2025, 12, 30)), set(), 0
        while d <= END:
            batch = [t for t in self.stripe_txns if t["_date"] < d and t["id"] not in paid_ids]
            if batch:
                k += 1
                paid_ids |= {t["id"] for t in batch}
                arrival = add_bdays(d, 2)
                self.payouts.append({"id": f"po_{self.seed:02d}{k:05d}", "object": "payout",
                                     "amount": sum(t["net"] for t in batch), "arrival_date": ts(arrival),
                                     "created": ts(d), "currency": "usd", "status": "paid" if arrival <= END else "in_transit",
                                     "automatic": True, "method": "standard", "statement_descriptor": "KESTREL INFERENCE",
                                     "balance_transactions": batch, "_arrival": arrival, "_created": d})
            d = add_bdays(d, 1)
        # the billing system books card sales, fees and refunds daily; disputes are left for the payout card
        by_day = {}
        for t in self.stripe_txns:
            by_day.setdefault(t["_date"], []).append(t)
        self.stripe_opening = sum(t["net"] for t in self.stripe_txns if t["_date"] < START)
        for day, ts_ in sorted(by_day.items()):
            if day < START:
                continue
            gross = sum(t["amount"] for t in ts_ if t["type"] == "charge")
            fees = sum(t["fee"] for t in ts_ if t["type"] == "charge")
            refunds = -sum(t["amount"] for t in ts_ if t["type"] == "refund")
            lines = [("stripe_clearing", gross, 0), ("revenue", 0, gross), ("processing_fees", fees, 0),
                     ("stripe_clearing", 0, fees)]
            if refunds:
                lines += [("refunds", refunds, 0), ("stripe_clearing", 0, refunds)]
            self.erp_entry(day, "billing", "system:billing", "auto", f"Stripe daily activity {day}", lines,
                           ref=f"stripe:{day}")

    # ---- outflows and the ERP feed ----------------------------------------------------------------
    def erp_entry(self, d, source, prepared, approved, memo, lines, ref="", customer_id=None, invoice_id=None):
        self.erp_n += 1
        e = {"erp_id": f"ERP-{self.erp_n:05d}", "date": d, "source": source, "prepared_by": prepared,
             "approved_by": approved, "memo": memo, "ref": ref,
             "lines": [{"account": a, "debit": dr, "credit": cr, "customer_id": customer_id, "invoice_id": invoice_id}
                       for a, dr, cr in lines if dr or cr]}
        self.erp.append(e)
        return e

    def outflow(self, d, text, amount, account, memo, split=None, **kw):
        line = {"date": d, "amount": -amount, "text": text, "_kind": "outflow"}
        self.bank.append(line)
        parts = split or [(account, amount)]
        ids = [self.erp_entry(d, kw.get("source", "ap"), kw.get("prepared", "rpatel"), kw.get("approved", "mchen"),
                              f"{memo}{' ' + str(j + 1) if split else ''}", [(a, amt, 0), ("cash", 0, amt)],
                              ref=kw.get("ref", ""), customer_id=kw.get("customer_id"))["erp_id"]
               for j, (a, amt) in enumerate(parts)]
        line["_erp"] = ids
        line["_entry"] = {"cash": -amount, **{a: sum(x for b, x in parts if b == a) for a, _ in parts}}
        return line

    def make_outflows(self):
        rng = self.rng
        plains = [c for c in self.customers if c["_"]["role"] == "plain"]
        for mi, month in enumerate(INVOICE_MONTHS[1:]):
            y, m = map(int, month.split("-"))
            last = calendar.monthrange(y, m)[1]
            self.outflow(month_day(month, 1), "ACH DEBIT HARBORVIEW PROPERTIES RENT", 3850000, "rent", f"Rent {month}")
            cloud = rng.randrange(21000000, 27000000, 100)
            self.outflow(month_day(month, 5), f"WIRE OUT STRATUS CLOUD SERVICES INV SC-{m:02d}26", cloud, "cloud_compute",
                         f"Stratus Cloud {month}")
            for day in (15, last):
                d = month_day(month, day)
                while d.month != m:
                    d -= timedelta(days=1)
                net, tax = rng.randrange(9500000, 10500000, 100), rng.randrange(3300000, 3700000, 100)
                self.outflow(d, "ACH DEBIT PLOVER PAYROLL SERVICES", net + tax, "payroll", f"Payroll {d}",
                             split=[("payroll", net), ("payroll_taxes", tax)], source="payroll", prepared="system:payroll",
                             approved="mchen")
                self.schedule.append({"date": d, "payee": "Payroll", "amount": 13500000})
            self.schedule += [{"date": month_day(month, 1), "payee": "Rent", "amount": 3850000},
                              {"date": month_day(month, 5), "payee": "Stratus Cloud", "amount": 24000000}]
            for v in rng.sample(VENDORS, rng.randint(9, 12)):
                amt = rng.randrange(50000, 4000000, 1) if v != "Sable Datacenter Power" else rng.randrange(2500000, 3500000)
                self.outflow(month_day(month, rng.randint(2, 27)), f"ACH DEBIT {v.upper()}", amt, "vendors_opex", f"{v} {month}")
            # trap 4: one refund wire, booked by the billing system and again by hand from the email
            c, amt = plains[3 + mi], rng.randrange(80000, 300000, 100)
            d = month_day(month, rng.randint(8, 18))
            rf = f"RF-{1020 + mi}"
            line = self.outflow(d, f"WIRE OUT {c['name'].upper()} REFUND {rf}", amt, "refunds", f"Refund {rf} {c['name']}",
                                source="billing", prepared="system:billing", approved="auto", ref=rf,
                                customer_id=c["customer_id"])
            line["_traps"] = [4]
            self_approved = mi == 1
            dupe = self.erp_entry(add_bdays(d, 1), "manual", "jlin", "jlin" if self_approved else "mchen",
                                  f"Refund to {c['name']} per email, SLA credit {rf}", [("refunds", amt, 0), ("cash", 0, amt)],
                                  ref=rf, customer_id=c["customer_id"])
            self.dupes.append({"erp_id": dupe["erp_id"], "duplicate_of": line["_erp"][0]})
            self.email(d - timedelta(days=2), f"ops@{c['email_domain']}", f"SLA credit refund {rf}",
                       f"Hi,\n\nFollowing the December outage we agreed a refund of ${fmt(amt)} under the SLA. "
                       f"Reference {rf}. Please wire it to the account on file.\n\nThanks")
            if self_approved:
                self.breaches.append({"erp_id": dupe["erp_id"], "breach": "preparer_is_approver"})
            # trap 13: a standalone manual accrual prepared and approved by the same user
            if mi >= 1:
                e = self.erp_entry(month_day(month, 20 + mi), "manual", "mchen", "mchen", f"Accrue consulting fees {month}",
                                   [("vendors_opex", 480000 + mi * 35000, 0), ("accrued_liabilities", 0, 480000 + mi * 35000)])
                self.breaches.append({"erp_id": e["erp_id"], "breach": "preparer_is_approver"})

    # ---- assembly -------------------------------------------------------------------------------------
    def email(self, d, sender, subject, body):
        self.email_n += 1
        self.emails.append({"email_id": f"E-{self.email_n:04d}", "date": d, "from": sender, "subject": subject, "body": body})

    def noise_emails(self):
        rng = self.rng
        for month in INVOICE_MONTHS[1:]:
            for _ in range(8):
                c = rng.choice(self.customers)
                subject, body = rng.choice([
                    ("Updated W-9 request", "Hi,\n\nCould you send an updated W-9 for our vendor file?\n\nThanks"),
                    ("Invoice copy", "Hello,\n\nPlease resend last month's invoice PDF; our AP inbox bounced it.\n\nRegards"),
                    ("New AP contact", "Hi,\n\nPlease note our accounts payable contact has changed. Invoices go to ap@ from now on.\n\nThanks"),
                    ("Usage question", "Hi,\n\nCan you break down the fine-tuning line by project? Not disputing, just for our cost centre.\n\nThanks")])
                self.email(month_day(month, rng.randint(1, 27)), f"ap@{c['email_domain']}", subject, body)

    def assemble_bank(self):
        for p in self.payments:
            method = p.get("method") or p["customer"]["_"]["method"]
            head = "WIRE IN" if method == "wire" else "ACH CREDIT"
            name = p["payer_name"].upper().replace(",", "")
            if self.rng.random() < 0.2 and p["payer_name"] == p["customer"]["name"] and len(name) > 18:
                name = name[:max(18, len(name) - 4)].strip()
            ref = f" {p['ref_text']}" if p.get("ref_text") else ""
            self.bank.append({"date": p["date"], "amount": p["amount"], "text": f"{head} {name}{ref}{p['extra_text']}",
                              "_kind": "receipt", "_pay": p})
        for po in self.payouts:
            if START <= po["_arrival"] <= END:
                self.bank.append({"date": po["_arrival"], "amount": po["amount"],
                                  "text": f"STRIPE TRANSFER {po['id'].upper()}", "_kind": "payout", "_po": po})
        self.bank.sort(key=lambda b: (b["date"], b["_kind"], b["text"]))
        self.opening_cash = 240000000
        bal, counters = self.opening_cash, {}
        for b in self.bank:
            m = f"{b['date']:%Y-%m}"
            counters[m] = counters.get(m, 0) + 1
            bal += b["amount"]
            b["line_id"], b["balance"] = f"B-{m}-{counters[m]:04d}", bal

    def build_truth(self):
        open_inv = [i for i in self.invoices if not i.get("_paid_before_open")]
        opening_ar = sum(i["amount"] for i in open_inv if i["_month"] == 0)
        opening = {"cash": self.opening_cash, "ar": opening_ar, "stripe_clearing": self.stripe_opening,
                   "opening_equity": -(self.opening_cash + opening_ar + self.stripe_opening)}
        self.opening = opening
        movements = []  # (date, {account: net debit})
        dupes = {d["erp_id"] for d in self.dupes}
        for e in self.erp:
            if e["erp_id"] not in dupes:
                net = {}
                for ln in e["lines"]:
                    net[ln["account"]] = net.get(ln["account"], 0) + ln["debit"] - ln["credit"]
                movements.append((e["date"], net))
        for b in self.bank:
            t = {"kind": b["_kind"], "date": b["date"].isoformat(), "amount": b["amount"], "traps": [], "needs_human": False}
            if b["_kind"] == "receipt":
                p = b["_pay"]
                entry = {"cash": p["amount"], "ar": -sum(p["settles"].values())}
                for r in p["residuals"]:
                    acct, sign = {"absorb_bank_fee": ("bank_fees", 1), "write_off_discount": ("sales_discounts", 1),
                                  "contract_discount": ("sales_discounts", 1), "customer_credit": ("customer_credits", -1),
                                  "deferred_revenue": ("deferred_revenue", -1), "leave_open_chase": (None, 0)}[r["treatment"]]
                    if acct:
                        entry[acct] = entry.get(acct, 0) + sign * r["amount"]
                t.update(customer_id=p["customer"]["customer_id"], settles=p["settles"], residuals=p["residuals"],
                         entry={k: v for k, v in entry.items() if v}, traps=p["traps"], needs_human=p["needs_human"],
                         why=p["why"], hero=bool(p.get("hero")))
            elif b["_kind"] == "payout":
                po = b["_po"]
                disputes = [x for x in po["balance_transactions"] if x["reporting_category"] == "dispute"]
                loss, dfee = -sum(x["amount"] for x in disputes), sum(x["fee"] for x in disputes)
                entry = {"cash": po["amount"], "stripe_clearing": -(po["amount"] + loss + dfee)}
                if disputes:
                    entry.update(chargeback_losses=loss, dispute_fees=dfee)
                    t["traps"].append(11)
                if po["_created"].month != po["_arrival"].month:
                    t["traps"].append(10)
                t.update(entry=entry, payout_id=po["id"])
            else:
                t.update(entry=b["_entry"], erp_ids=b["_erp"], traps=b.get("_traps", []))
            if b["_kind"] != "outflow":
                movements.append((b["date"], t["entry"]))
            for n in t["traps"]:
                self.traps.append({"trap": n, "line_id": b["line_id"]})
            self.truth_lines[b["line_id"]] = t
        self.balances = {}
        for month in INVOICE_MONTHS[1:]:
            y, m = map(int, month.split("-"))
            cutoff, bal = date(y, m, calendar.monthrange(y, m)[1]), dict(opening)
            for d, net in movements:
                if d <= cutoff:
                    for a, v in net.items():
                        bal[a] = bal.get(a, 0) + v
            for i in self.invoices:  # invoices issued in the window are booked by the ERP feed, already in movements
                pass
            self.balances[month] = bal

    def make_invoice_entries(self):
        for i in self.invoices:
            if i["_month"] >= 1:
                self.erp_entry(i["issue_date"], "billing", "system:billing", "auto", f"Invoice {i['invoice_id']}",
                               [("ar", i["amount"], 0), ("revenue", 0, i["amount"])], ref=i["invoice_id"],
                               customer_id=i["customer_id"], invoice_id=i["invoice_id"])

    def make_history(self):
        for c in self.customers:
            for m in (9, 10, 11):
                due = date(2025, m, 1) + timedelta(days=30)
                self.history.append({"customer_id": c["customer_id"], "due_date": due, "paid_date": self.pay_date(c, due)})

    def make_contracts(self):
        out = {}
        for c in self.customers:
            clause = "Payment terms: net 30 days from invoice date, by wire or ACH in US dollars."
            if c["_"]["role"] == "contract":
                clause += "\nEarly payment: Customer may deduct 1% from any invoice paid within 10 days of the invoice date."
            if c["_"]["role"] == "plain" and c["_"]["k"] % 4 == 0:
                clause += "\nBank charges: Customer shall bear all bank charges on its remittances."
            out[c["customer_id"]] = (f"MASTER SERVICES AGREEMENT (extract)\nKestrel Inference, Inc. and {c['name']}\n\n{clause}\n"
                                     "Disputes: Customer shall notify Kestrel of a disputed amount within 15 days of invoice date.\n")
        return out

    def build(self):
        self.make_customers()
        self.make_invoices()
        self.plan_payments()
        self.finalize_payments()
        self.make_invoice_entries()
        self.make_stripe()
        self.make_outflows()
        self.noise_emails()
        self.assemble_bank()
        self.make_history()
        self.build_truth()
        return self

    # ---- output -----------------------------------------------------------------------------------------
    def write(self, root: Path):
        pub, truth = root / "public", root / "truth"
        for d in (pub / "emails", pub / "contracts", pub / "history", truth):
            d.mkdir(parents=True, exist_ok=True)

        def table(path, rows, cols):
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for r in rows:
                    w.writerow([r[c] for c in cols])

        table(pub / "customers.csv", self.customers, ["customer_id", "name", "email_domain", "payment_method", "terms_days"])
        table(pub / "invoices.csv", [i for i in self.invoices if not i.get("_paid_before_open")],
              ["invoice_id", "customer_id", "issue_date", "due_date", "amount", "description"])
        table(pub / "history" / "receipts_2025.csv", self.history, ["customer_id", "due_date", "paid_date"])
        table(pub / "outflow_schedule.csv", sorted(self.schedule, key=lambda s: s["date"]), ["date", "payee", "amount"])
        for month in INVOICE_MONTHS[1:]:
            table(pub / f"bank_{month}.csv", [b for b in self.bank if f"{b['date']:%Y-%m}" == month],
                  ["line_id", "date", "amount", "text", "balance"])
        strip = lambda o: {k: ([strip(x) for x in v] if isinstance(v, list) else v) for k, v in o.items() if not k.startswith("_")}
        (pub / "stripe_payouts.json").write_text(json.dumps([strip(p) for p in self.payouts], indent=1))
        erp = [{**e, "date": e["date"].isoformat()} for e in sorted(self.erp, key=lambda e: (e["date"], e["erp_id"]))]
        balances = []
        for month in INVOICE_MONTHS[1:]:
            y, m = map(int, month.split("-"))
            cutoff = date(y, m, calendar.monthrange(y, m)[1])
            swept = {t["id"] for po in self.payouts if po["_created"] <= cutoff for t in po["balance_transactions"]}
            held = sum(t["net"] for t in self.stripe_txns if t["_date"] <= cutoff and t["id"] not in swept)
            balances.append({"object": "balance", "as_of": cutoff.isoformat(), "available": [{"amount": 0, "currency": "usd"}],
                             "pending": [{"amount": held, "currency": "usd"}]})
        (pub / "stripe_balance.json").write_text(json.dumps(balances, indent=1))
        (pub / "erp_entries.json").write_text(json.dumps(erp, indent=1))
        (pub / "opening_balances.json").write_text(json.dumps({"as_of": "2025-12-31", "balances": self.opening}, indent=1))
        for e in self.emails:
            (pub / "emails" / f"{e['email_id']}.txt").write_text(
                f"From: {e['from']}\nTo: ar@kestrelinference.example\nDate: {e['date']}\nSubject: {e['subject']}\n\n{e['body']}\n")
        for cid, text in self.make_contracts().items():
            (pub / "contracts" / f"{cid}.txt").write_text(text)
        (truth / "truth.json").write_text(json.dumps({
            "seed": self.seed, "lines": self.truth_lines, "balances": self.balances, "traps": self.traps,
            "control_breaches": self.breaches, "duplicate_entries": self.dupes,
            "payer_profiles": {c["customer_id"]: {k: v for k, v in c["_"].items() if k in ("role", "lag", "style")}
                               for c in self.customers}}, indent=1, default=str))
        (truth / "hidden_policy.json").write_text(json.dumps(HIDDEN_POLICY, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = Path(a.out or Path(__file__).resolve().parent.parent / "data" / f"seed-{a.seed}")
    w = World(a.seed).build()
    w.write(root)
    inbound = [b for b in w.bank if b["_kind"] != "outflow"]
    trapped = [b for b in inbound if w.truth_lines[b["line_id"]]["traps"]]
    print(f"seed {a.seed}: {len(w.bank)} bank lines ({len(inbound)} inbound, {len(trapped)} with a trap), "
          f"{len(w.invoices)} invoices, {len(w.emails)} emails, {len(w.erp)} ERP entries -> {root}")


if __name__ == "__main__":
    main()
