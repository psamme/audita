"""Turn ground truth into what an ERP and an inbox would actually retain: reconcile links, terse adjustment
entries, the occasional approval row or email. Reasons are not recorded. Some of it is wrong.

The mess is deliberate:
  - memos are terse and often empty
  - one clerk per client handles a recurring case inconsistently
  - a few outright mistakes per client, some reversed later, some never noticed
  - some links are missing because the work was done in a spreadsheet that month
  - escalations mostly leave no direct trace: the item is just finished days later by someone senior,
    or is still open
"""
from datetime import date, timedelta

from sim.world import next_biz

MEMOS = {
    "fee": ["bank fee", "fee", "svc chg", "", "", "bk chg"],
    "writeoff": ["adj", "w/o", "small diff", "", "rounding", "over/short", ""],
    "processor": ["card fees", "fees per rpt", "", "merchant fees", "net settlement"],
    "other": ["", "per stmt", "adj", "misc", "see email", ""],
}


def memo_for(rng, account_name: str, senior: str | None) -> str:
    if senior and rng.random() < 0.5:
        return rng.choice([f"per {senior}", f"ok'd {senior}", "approved", ""])
    n = account_name.lower()
    kind = "fee" if "fee" in n or "charge" in n else "processor" if "merchant" in n or "processing" in n \
        else "writeoff" if "short" in n or "discount" in n else "other"
    return rng.choice(MEMOS[kind])


class Enactor:
    def __init__(self, world, cfg: dict):
        """cfg: users [(id, name, role, senior)], clerk, sloppy_clerk, sloppy_share, confusion {account: wrong},
        seniors {escalate_role: user_id}, approval_roles {role: probability}, excel_period, excel_share"""
        self.w, self.cfg = world, cfg
        for u in cfg["users"]:
            world.con.execute("INSERT OR IGNORE INTO user VALUES (?,?,?,?)", u)
        self.n = world.n
        for k in ("LNK", "JE", "APR"):
            self.n.setdefault(k, 0)
        self.mistakes = world.state.setdefault("mistakes", {"overlimit": 0, "wrong_account": 0, "wrong_link": 0})

    def _id(self, kind):
        self.n[kind] += 1
        return f"{self.w.client}-{kind}-{self.n[kind]:05d}"

    def link(self, period, bank_id, ledger_id, amount, who, when, undone=None):
        self.w.con.execute("INSERT INTO reconcile_link VALUES (?,?,?,?,?,?,?,?)",
                           (self._id("LNK"), period, bank_id, ledger_id, amount, who, when.isoformat(),
                            undone.isoformat() if undone else None))

    def je(self, period, on, posted_at, who, account, amount, memo, bank_id, reverses=None):
        i = self._id("JE")
        self.w.con.execute("INSERT INTO journal_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (i, period, on.isoformat(), posted_at.isoformat(), who, account, round(amount, 2), memo,
                            bank_id, reverses))
        return i

    def enact(self, item_kind, item_id, action, ledger_ids, adjustments, escalate_to, easy, eventual):
        w, cfg, rng = self.w, self.cfg, self.w.rng_mess
        if item_kind != "bank" or action == "carry_forward":
            if action == "escalate" and eventual is not False:
                self._ledger_escalation(item_id, escalate_to)
            return
        b = w.con.execute("SELECT * FROM bank_line WHERE id=?", (item_id,)).fetchone()
        period, on = b["period"], date.fromisoformat(b["date"])
        chart = w.chart
        who = cfg["sloppy_clerk"] if rng.random() < cfg["sloppy_share"] else cfg["clerk"]
        when = next_biz(on, rng.choice([0, 0, 1, 1, 2, 3]))
        senior = None
        if action == "escalate":
            if (eventual and eventual["action"] == "match_adjust" and len(eventual["adjustments"]) == 1
                    and abs(eventual["adjustments"][0]["amount"]) < 120 and self.mistakes["overlimit"] < 1 and rng.random() < 0.15):
                self.mistakes["overlimit"] += 1
                writeoff = next(a for a, n in chart.items() if "short" in n.lower() or "discount" in n.lower())
                return self.overlimit_writeoff(item_id, eventual["ledger_ids"], writeoff, eventual["adjustments"][0]["amount"])
            if not eventual or rng.random() < 0.22:
                self._trace(b, escalate_to, who, when, resolved=False)
                return  # still sitting unreconciled
            senior = cfg["seniors"].get(escalate_to, cfg["clerk"])
            finisher = senior if rng.random() < 0.7 else cfg["clerk"]   # sometimes the clerk keys it in afterwards
            self._trace(b, escalate_to, who, when, resolved=True)
            who, when = finisher, next_biz(on, rng.randint(3, 9))
            action, ledger_ids, adjustments = eventual["action"], eventual.get("ledger_ids", []), eventual.get("adjustments", [])
        elif easy and period == cfg.get("excel_period") and rng.random() < cfg.get("excel_share", 0):
            return  # reconciled in a spreadsheet that month; the ERP never heard about it

        # deliberate mistakes, a couple per client
        if action == "match_adjust" and not senior and self.mistakes["wrong_account"] < 2 and rng.random() < 0.02 and len(adjustments) == 1:
            self.mistakes["wrong_account"] += 1
            wrong = rng.choice([a for a in chart if a not in ("1010", adjustments[0]["account"]) and a[0] in "456"])
            bad = self.je(period, on, when, who, wrong, adjustments[0]["amount"], "", item_id)
            if self.mistakes["wrong_account"] == 1:   # the first one gets caught at review, the second never does
                fix = next_biz(when, rng.randint(6, 15))
                boss = list(cfg["seniors"].values())[0]
                self.je(period, on, fix, boss, wrong, -adjustments[0]["amount"], "reverse - wrong acct", item_id, reverses=bad)
                self.je(period, on, fix, boss, adjustments[0]["account"], adjustments[0]["amount"], "correct acct", item_id)
            for le in ledger_ids:
                self.link(period, item_id, le, self._amt(le), who, when)
            return

        for le in ledger_ids:
            self.link(period, item_id, le, self._amt(le), who, when)
        name = lambda a: chart.get(a, "")
        for a in adjustments:
            account = a["account"]
            if who == cfg["sloppy_clerk"] and account in cfg["confusion"] and rng.random() < 0.45:
                account = cfg["confusion"][account]
            self.je(period, on, when, who, account, a["amount"],
                    memo_for(rng, name(a["account"]), cfg["names"].get(senior) if senior else None), item_id)

    def overlimit_writeoff(self, bank_id, ledger_ids, account, amount):
        """A clerk quietly writes off something that should have gone to someone. Nobody notices."""
        b = self.w.con.execute("SELECT * FROM bank_line WHERE id=?", (bank_id,)).fetchone()
        on = date.fromisoformat(b["date"])
        for le in ledger_ids:
            self.link(b["period"], bank_id, le, self._amt(le), self.cfg["sloppy_clerk"], on)
        self.je(b["period"], on, on, self.cfg["sloppy_clerk"], account, amount, "", bank_id)

    def _amt(self, ledger_id):
        return self.w.con.execute("SELECT amount FROM ledger_entry WHERE id=?", (ledger_id,)).fetchone()[0]

    def _trace(self, b, role, clerk, when, resolved: bool):
        """Most escalations happen in person or in chat. Some leave an approval row or an email."""
        w, cfg, rng = self.w, self.cfg, self.w.rng_mess
        senior = cfg["seniors"].get(role)
        if senior and rng.random() < cfg["approval_roles"].get(role, 0):
            self.n["APR"] += 1
            decided = next_biz(when, rng.randint(2, 6))
            w.con.execute("INSERT INTO approval VALUES (?,?,?,?,?,?,?,?,?)",
                          (f"{w.client}-APR-{self.n['APR']:05d}", b["period"], decided.isoformat(), "bank_line", b["id"], clerk,
                           senior, "approved" if resolved else "pending",
                           rng.choice(["", "", "ok", "see notes", "verified by phone", "pls follow up"])))
        elif senior and rng.random() < cfg.get("email_share", 0.25):
            names = cfg["names"]
            w.doc("internal_email", when, names.get(clerk, clerk), f"Q: {b['description'][:40]} {abs(b['amount']):,.2f}",
                  rng.choice([
                      f"{names.get(senior, senior)} - this one on {b['date']} for {abs(b['amount']):,.2f} doesn't tie out. How do you want it handled?",
                      f"Hi {names.get(senior, senior)}, not sure about the {abs(b['amount']):,.2f} on the {b['date']} statement ({b['description'][:30]}). Can you take a look before I touch it?",
                      f"{names.get(senior, senior)}, flagging {b['description'][:30]} {abs(b['amount']):,.2f}. Leaving it open until I hear from you."]),
                  {"period": b["period"], "to": names.get(senior, senior)})

    def _ledger_escalation(self, ledger_id, role):
        w, cfg, rng = self.w, self.cfg, self.w.rng_mess
        senior = cfg["seniors"].get(role)
        e = w.con.execute("SELECT * FROM ledger_entry WHERE id=?", (ledger_id,)).fetchone()
        if senior and rng.random() < cfg["approval_roles"].get(role, 0) + 0.3:
            self.n["APR"] += 1
            w.con.execute("INSERT INTO approval VALUES (?,?,?,?,?,?,?,?,?)",
                          (f"{w.client}-APR-{self.n['APR']:05d}", e["period"],
                           (date.fromisoformat(e["posted_at"]) + timedelta(days=rng.randint(1, 5))).isoformat(),
                           "ledger_entry", ledger_id, cfg["clerk"], senior, rng.choice(["approved", "approved", "rejected"]),
                           rng.choice(["", "late entry", "ok this once"])))
