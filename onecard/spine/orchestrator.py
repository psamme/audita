"""The orchestrator: simulated calendar, intake, routing of reopened cards, posting, month-end audit and close.

One day: book the ERP feed, open a card per bank line, let the desks pin claims, run the checker, post or reopen or
escalate, then let bank rec look for ledger entries no bank line supports. The last day of a month runs audit and close.

`human` is whoever answers the review queue: the scripted controller in batch runs, or None to leave cards waiting
for the review screen. The orchestrator is never handed the answer key; it only sees what the human says.
"""
import calendar
import json
import re
from datetime import date, timedelta
from pathlib import Path

from desks import audit, bank_rec, cash_app, close, forecast, policy_writer
from desks.llm import Model

from . import review
from .checker import REOPEN, check_card, failed
from .contract import MAX_ROUNDS, TREATMENTS, Config, entry_lines
from .ledger import PostingRefused
from .policy import PolicyBook
from .store import Store
from .tools import Tools

START, END = "2026-01-01", "2026-03-31"
MAX_QUESTIONS = 6           # per card, so a confused card cannot spam the human
SUBLEDGER = {"customer_credits", "deferred_revenue", "unapplied_cash"}


class Ctx:
    """What every desk is handed: the tool layer, the store, the policy book, the model, the configuration, today."""

    def __init__(self, public_dir: str | Path, db_path: str | Path, cfg: Config, model: Model | None = None, seed: int = 7):
        self.store = Store(db_path)
        self.tools = Tools(public_dir, self.store)
        self.book = PolicyBook(self.store)
        self.cfg, self.model, self.seed = cfg, model or Model("off"), seed
        self.day = self.store.note("run", "day", START)
        self.tools.today = self.day


def days(a: str, b: str):
    d, stop = date.fromisoformat(a), date.fromisoformat(b)
    while d <= stop:
        yield d.isoformat()
        d += timedelta(days=1)


def month_end(day: str) -> str:
    y, m = int(day[:4]), int(day[5:7])
    return f"{day[:8]}{calendar.monthrange(y, m)[1]:02d}"


# ---- intake ----------------------------------------------------------------------------------------------------
def open_card(ctx: Ctx, line: dict) -> dict:
    """One card per bank line, with candidate evidence attached by deterministic search."""
    kind = "payout" if line["text"].startswith("STRIPE") else ("outflow" if line["amount"] < 0 else "receipt")
    payer = {"customer_id": None, "how": None, "printed": ""}
    evidence, terms, domain = [f"bank:{line['line_id']}"], [], None
    if kind == "receipt":
        payer = ctx.tools.identify_payer(line["text"], use_aliases=ctx.cfg.policies)
        terms = [f"{line['amount'] / 100:,.2f}"]
        if payer["customer_id"]:
            domain = ctx.tools.customer(payer["customer_id"])["email_domain"]
            evidence.append(f"contract:{payer['customer_id']}")
    elif kind == "outflow":
        terms = re.findall(r"\bRF-\d+\b", line["text"])
    elif ctx.tools.payout_for(line["text"]):
        evidence.append(f"payout:{ctx.tools.payout_for(line['text'])['id']}")
    since = (date.fromisoformat(line["date"]) - timedelta(days=14)).isoformat()
    if domain or terms:
        evidence += [f"email:{e['email_id']}" for e in ctx.tools.search_emails(domain=domain, terms=terms, since=since)]
    return {"card_id": "C-" + line["line_id"][2:], "bank_line": line, "kind": kind, "payer": payer, "evidence": evidence,
            "claims": [], "checks": [], "status": "open", "rounds": 0, "human_touch": False, "history": []}


def book_erp_feed(ctx: Ctx, day: str):
    for e in ctx.tools.erp_entries(day):
        if ctx.store.one("SELECT COUNT(*) FROM entries WHERE erp_id=?", e["erp_id"]):
            continue
        try:
            ctx.tools.ledger.post(date=e["date"], lines=e["lines"], memo=e["memo"], evidence=[f"erp:{e['erp_id']}"],
                                  source=e["source"], prepared_by=e["prepared_by"], approved_by=e["approved_by"],
                                  erp_id=e["erp_id"])
        except PostingRefused as why:
            ctx.store.event(day, None, "erp_entry_refused", {"erp_id": e["erp_id"], "why": str(why)})


def book_opening(ctx: Ctx):
    if ctx.store.one("SELECT COUNT(*) FROM entries WHERE source='opening'"):
        return
    ob = ctx.tools.opening_balances()
    lines = [{"account": a, "debit": max(v, 0), "credit": max(-v, 0)} for a, v in ob["balances"].items()]
    eid = ctx.tools.ledger.post(date=ob["as_of"], lines=lines, memo="Opening balances", evidence=["opening_balances"],
                                source="opening")
    ctx.store.x("INSERT OR REPLACE INTO bank_matches VALUES (?,?,?)", eid, "opening", None)  # reconciled by definition


# ---- one card --------------------------------------------------------------------------------------------------
def pin(card: dict, claim: dict):
    card["claims"] = [c for c in card["claims"] if c["desk"] != claim["desk"]] + [claim]


def desks_claim(ctx: Ctx, card: dict):
    """Each desk reads the raw records on its own and pins its claim."""
    if card["kind"] != "outflow":
        pin(card, cash_app.run(ctx, card))
    pin(card, bank_rec.pre_post(ctx, card))
    pin(card, forecast.run(ctx, card))


def work(ctx: Ctx, card: dict, human=None) -> dict:
    """Drive one card as far as it will go: posted, or waiting on a human."""
    for _ in range(MAX_QUESTIONS):
        desks_claim(ctx, card)
        card["checks"] = check_card(ctx, card)
        if card["kind"] == "outflow":
            return settle_outflow(ctx, card)
        ca = checker_claim(card, "cash_app")
        if ctx.cfg.cards:
            reopen(ctx, card)
            ca = checker_claim(card, "cash_app")
        forced = bool(card.get("resolutions", {}).get("forced"))  # a human overrode the desks: only the ledger's own rules still apply
        blocked = ca.get("needs_human") or (ctx.cfg.cards and failed(card["checks"]) and not forced)
        if not blocked:
            return post(ctx, card, gate=ctx.cfg.cards and not forced)
        if not ca.get("needs_human"):
            ca["needs_human"] = {"type": "desks_disagree", "why": "desks_disagree"}
        item = review.build(ctx, card)
        card.update(status="escalated", review=item)
        ctx.store.save_card(card)
        ctx.store.event(ctx.day, card["card_id"], "escalated", {"why": item["why"], "question": item["question"]})
        answer = human(item, card) if human else None
        if answer is None:
            return card
        decide(ctx, card, *answer)
    card["status"] = "stuck"
    ctx.store.save_card(card)
    return card


def checker_claim(card: dict, desk: str) -> dict:
    return next(c for c in card["claims"] if c["desk"] == desk)


def reopen(ctx: Ctx, card: dict):
    """Send a failed check back to the desk that owns it, with the card in hand. Two rounds at most."""
    for _ in range(MAX_ROUNDS):
        owners = {REOPEN[c["rule"]] for c in failed(card["checks"]) if c["rule"] in REOPEN}
        if not owners or checker_claim(card, "cash_app").get("needs_human"):
            return
        card["rounds"] += 1
        card["history"].append({"round": card["rounds"], "reopened": sorted(owners),
                                "failed": [c["rule"] for c in failed(card["checks"])]})
        ctx.store.event(ctx.day, card["card_id"], "reopened", card["history"][-1])
        if "cash_app" in owners:
            pin(card, cash_app.run(ctx, card))
        if "forecast" in owners:
            pin(card, forecast.run(ctx, card, peer=checker_claim(card, "cash_app")))
        card["checks"] = check_card(ctx, card)


def decide(ctx: Ctx, card: dict, option_id: str, reason_text: str, approver: str, approve: bool = True) -> dict:
    """Save the human's answer, draft the rule it implies, and (in batch) approve it."""
    decision = review.resolve(ctx, card, card["review"], option_id, reason_text, approver)
    card.pop("review", None)
    drafts = []
    if ctx.cfg.policies:
        drafts = [policy_writer.tighten(ctx, decision, d) for d in ctx.book.draft(decision)]
    ctx.store.set_note("review", f"drafts:{decision['decision_id']}", drafts)
    ctx.store.event(ctx.day, card["card_id"], "decided", {"decision_id": decision["decision_id"], "treatment": decision["treatment"],
                                                           "drafts": [f"{d['code']}@v{d['version']}" for d in drafts]})
    if approve:
        approve_drafts(ctx, decision["decision_id"], approver)
    ctx.store.save_card(card)
    return {"decision": decision, "drafts": drafts}


def approve_drafts(ctx: Ctx, decision_id: str, approver: str) -> list[dict]:
    live = []
    for d in ctx.store.note("review", f"drafts:{decision_id}", []):
        try:
            ctx.book.approve(d, approver)
            live.append(d)
            ctx.store.event(ctx.day, None, "policy_live", {"policy": f"{d['code']}@v{d['version']}", "scope": d["scope"],
                                                           "condition": d["condition"], "backtest": d["backtest"]})
        except ValueError as why:
            ctx.store.event(ctx.day, None, "policy_rejected", {"policy": f"{d['code']}@v{d['version']}", "why": str(why)})
    ctx.store.set_note("review", f"drafts:{decision_id}", [])
    return live


def post(ctx: Ctx, card: dict, gate: bool) -> dict:
    """The only way a receipt reaches the books: the posting tool, with the checker as its pre-posting hook."""
    line, ca = card["bank_line"], checker_claim(card, "cash_app")
    lines = entry_lines(line["amount"], ca)
    decisions = card.get("decisions", [])
    try:
        entry_id = ctx.tools.ledger.post(
            date=line["date"], lines=lines, memo=f"{card['card_id']} {line['text']}"[:120], evidence=ca["evidence"],
            source="cash_app", card_id=card["card_id"], prepared_by="desk:cash_app",
            approved_by=f"human:{decisions[-1]}" if decisions else "checker", posted_on=ctx.day,
            hook=(lambda: check_card(ctx, card, lines)) if gate else None)
    except PostingRefused as why:
        card["status"] = "refused"
        card["history"].append({"refused": str(why)})
        ctx.store.event(ctx.day, card["card_id"], "posting_refused", {"why": str(why)})
        ctx.store.save_card(card)
        return card
    cid = ca.get("customer_id")
    for inv, cents in ca["settles"].items():
        ctx.store.x("INSERT INTO applications VALUES (?,?,?,?)", card["card_id"], inv, cents, line["date"])
    for r in ca["residuals"]:
        acct = (TREATMENTS.get(r.get("treatment") or "") or {}).get("account")
        if acct in SUBLEDGER:
            ctx.store.x("INSERT INTO subledger VALUES (?,?,?,?,?)", acct, cid, card["card_id"], r["amount"], line["date"])
        if r.get("policy") and ctx.book.get(r["policy"]):
            ctx.book.record_use(ctx.book.get(r["policy"]), card["card_id"], cid, line["date"], r["amount"])
    if ca.get("allocation_policy") and ctx.book.get(ca["allocation_policy"]):
        ctx.book.record_use(ctx.book.get(ca["allocation_policy"]), card["card_id"], cid, line["date"], line["amount"])
    if ca.get("alias") and ctx.cfg.policies:
        a = ca["alias"]
        ctx.store.x("INSERT OR REPLACE INTO aliases VALUES (?,?,?,?)", a["payer_key"], a["customer_id"], a["source"], ctx.day)
    pin(card, bank_rec.post_post(ctx, card, checker_claim(card, "bank_rec"), entry_id))
    forecast.commit(ctx, card, checker_claim(card, "forecast"))
    card.update(status="posted", entry_id=entry_id)
    card["checks"] = check_card(ctx, card, final=True) if not gate else card["checks"]
    ctx.store.save_card(card)
    return card


def settle_outflow(ctx: Ctx, card: dict) -> dict:
    br = checker_claim(card, "bank_rec")
    if not failed(card["checks"]):
        pin(card, bank_rec.post_post(ctx, card, br, None))
        card["status"] = "posted"
    ctx.store.save_card(card)
    return card


# ---- bank rec's daily sweep --------------------------------------------------------------------------------------
def sweep_orphans(ctx: Ctx, at_month_end: bool = False):
    """Ledger cash entries no bank line supports. With shared cards the hand-keyed duplicate is reversed against the
    card that holds the real payment. Independent desks only write it in bank rec's own notebook."""
    orphans = bank_rec.find_orphans(ctx, month_end=at_month_end)
    ctx.store.set_note("bank_rec", f"orphans:{ctx.day}", orphans)
    if not ctx.cfg.cards:
        return
    for o in orphans:
        if not (o["duplicate_of"] and o["card_id"]):
            continue
        card = ctx.store.card(o["card_id"])
        evidence = [f"entry:{o['entry_id']}", f"entry:{o['duplicate_of']}", f"bank:{card['bank_line']['line_id']}"]
        ctx.store.x("UPDATE entries SET card_id=? WHERE entry_id=?", card["card_id"], o["entry_id"])
        rev = ctx.tools.ledger.reverse(o["entry_id"], date=ctx.day, memo=f"Reverse duplicate of {o['duplicate_of']}: {o['memo']}"[:120],
                                       evidence=evidence, card_id=card["card_id"], source="bank_rec")
        br = checker_claim(card, "bank_rec")
        br.setdefault("reversed_duplicates", []).append({"entry_id": o["entry_id"], "reversal": rev, "duplicate_of": o["duplicate_of"]})
        br["evidence"] = sorted(set(br["evidence"] + evidence))
        card["history"].append({"day": ctx.day, "reversed_duplicate": o["entry_id"], "reversal": rev})
        ctx.store.save_card(card)
        ctx.store.event(ctx.day, card["card_id"], "duplicate_reversed", {"entry_id": o["entry_id"], "reversal": rev})


# ---- the calendar ------------------------------------------------------------------------------------------------
class Run:
    def __init__(self, ctx: Ctx, human=None, hold=(), audit_model: bool = False, log=print):
        self.ctx, self.human, self.hold, self.audit_model, self.log = ctx, human, set(hold), audit_model, log

    def _human(self, item, card):
        if card["card_id"] in self.hold or self.human is None:
            return None
        return self.human(item, card)

    def advance(self, until: str = END):
        ctx = self.ctx
        book_opening(ctx)
        done = ctx.store.note("run", "done")  # last fully processed day
        first = (date.fromisoformat(done) + timedelta(days=1)).isoformat() if done else START
        for day in days(first, min(until, END)):
            self.one_day(day)
            ctx.store.set_note("run", "done", day)
            ctx.store.commit()
        return self

    def one_day(self, day: str):
        ctx = self.ctx
        ctx.day = ctx.tools.today = day
        ctx.store.set_note("run", "day", day)
        period = day[:7]
        ctx.model.period = period
        if day[8:] == "01" and ctx.cfg.policies and not ctx.cfg.memory:
            ctx.book.wipe(day)
        book_erp_feed(ctx, day)
        if date.fromisoformat(day).weekday() == 0:
            ctx.store.set_note("forecast_snap", day, {"learned": forecast.weekly(ctx, day, weeks=5, learn=True),
                                                      "fixed": forecast.weekly(ctx, day, weeks=5, learn=False)})
        for card in ctx.store.cards(status="open"):  # outflows still waiting for their ledger entry
            work(ctx, card, self._human)
        for line in ctx.tools.bank_lines(day=day):
            work(ctx, open_card(ctx, line), self._human)
        sweep_orphans(ctx)
        if day == month_end(day):
            self.close_month(period, day)

    def close_month(self, period: str, day: str):
        ctx = self.ctx
        sweep_orphans(ctx, at_month_end=True)
        report = audit.run(ctx, period, ctx.seed, use_model=self.audit_model)
        for f in report["findings"]:
            if f["control"] == "reperformance":  # invariant 9: the audit desk disagrees with a posted entry
                card = ctx.store.card(f["card_id"])
                card.update(human_touch=True, audit_flag=f["detail"])
                ctx.store.save_card(card)
                ctx.store.event(day, f["card_id"], "audit_disagrees", f)
        ctx.store.set_note("scoreboard", f"policies:{period}", ctx.book.live())
        ctx.store.set_note("scoreboard", f"audit:{period}", report)
        result = close.run(ctx, period, day, report)
        ctx.store.set_note("scoreboard", "usage", ctx.model.usage)
        self.log(f"  [{ctx.cfg.name}] {period} {'locked' if result['locked'] else 'NOT LOCKED'}: "
                 f"{sum(c['ok'] for c in result['checklist'])}/{len(result['checklist'])} checklist, "
                 f"{len(result['contradictions'])} contradictions, {len(report['findings'])} audit findings")


def rework(ctx: Ctx, card_id: str, human=None) -> dict:
    """After a human answer arrives from the review screen: run the card again with the answer on it."""
    return work(ctx, ctx.store.card(card_id), human)


def summary(ctx: Ctx) -> dict:
    rows = ctx.store.q("SELECT period, status, COUNT(*) AS n FROM cards GROUP BY period, status")
    return {"day": ctx.store.note("run", "done"), "cards": [dict(r) for r in rows],
            "policies": [f"{p['code']}@v{p['version']}" for p in ctx.book.live()],
            "decisions": ctx.store.one("SELECT COUNT(*) FROM decisions")}


def dumps(o) -> str:
    return json.dumps(o, indent=1, default=str)
