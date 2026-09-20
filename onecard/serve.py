"""The review queue API and the screens.

  python run.py --demo && python serve.py --run demo     the hero card waits for a human; answer it, approve the rule, continue
  python serve.py --run C                                browse a finished run

The server reads one run database. It is handed the answer key only to let the scripted controller finish the calendar
after the live decision (POST /api/continue); no card, claim or review item it serves is built from truth.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from desks.llm import Model  # noqa: E402
from spine import orchestrator as orch  # noqa: E402
from spine.contract import ACCOUNTS, Config  # noqa: E402
from world import metrics  # noqa: E402
from world.controller import Controller  # noqa: E402


class Answer(BaseModel):
    option_id: str
    reason: str
    approver: str = "controller"


class Approval(BaseModel):
    decision_id: str
    approver: str = "controller"


def evidence_docs(ctx, card: dict) -> list[dict]:
    """Every evidence id on the card, resolved to the record it points at."""
    ids, out = list(card["evidence"]), []
    for c in card["claims"]:
        ids += [e for e in c.get("evidence", []) if e not in ids]
    for e in ids:
        kind, _, key = e.partition(":")
        doc = None
        if kind == "bank":
            doc = card["bank_line"]
        elif kind == "inv":
            doc = ctx.tools.invoice(key)
        elif kind == "email":
            doc = ctx.tools.email(key)
        elif kind == "contract":
            doc = {"text": ctx.tools.contract(key)}
        elif kind == "policy":
            doc = ctx.book.get(key)
        elif kind == "decision":
            row = ctx.store.one("SELECT doc FROM decisions WHERE decision_id=?", key)
            doc = json.loads(row) if row else None
        elif kind == "payout":
            po = next((p for p in ctx.tools.payouts() if p["id"] == key), None)
            doc = po and {"id": po["id"], "amount": po["amount"], "transactions": len(po["balance_transactions"]),
                          "disputes": [t for t in po["balance_transactions"] if t["reporting_category"] == "dispute"]}
        elif kind == "entry":
            doc = ctx.tools.ledger.entry(key)
        out.append({"id": e, "kind": kind, "doc": doc})
    return out


def card_view(ctx, card: dict) -> dict:
    decisions = [json.loads(r[0]) for r in ctx.store.q("SELECT doc FROM decisions WHERE card_id=? ORDER BY decision_id", card["card_id"])]
    pending = [{"decision_id": d["decision_id"], "drafts": ctx.store.note("review", f"drafts:{d['decision_id']}", [])} for d in decisions]
    return {"card": card, "entries": ctx.tools.ledger.card_entries(card["card_id"]), "evidence": evidence_docs(ctx, card),
            "decisions": decisions, "pending_drafts": [p for p in pending if p["drafts"]],
            "events": [{"day": r["day"], "kind": r["kind"], "doc": json.loads(r["doc"])} for r in
                       ctx.store.q("SELECT day, kind, doc FROM events WHERE card_id=? ORDER BY n", card["card_id"])],
            "policy_uses": [dict(r) for r in ctx.store.q("SELECT * FROM policy_uses WHERE card_id=?", card["card_id"])]}


def build(seed: int, run: str, db: Path | None = None) -> FastAPI:
    world, db = ROOT / "data" / f"seed-{seed}", db or ROOT / "runs" / f"seed-{seed}" / f"{run}.db"
    if not db.exists():
        raise SystemExit(f"{db} does not exist. Run `python run.py --demo` or `python run.py --all` first.")
    meta = orch.Store(db).note("run", "meta", {"config": "C", "model": "off"})
    ctx = orch.Ctx(world / "public", db, Config.get(meta["config"]),
                   Model("replay" if meta["model"] != "off" else "off", db.parent / "model_calls.jsonl"), seed)
    app = FastAPI(title="One Payment, Every Desk")

    def card_or_404(card_id: str) -> dict:
        card = ctx.store.card(card_id)
        if not card:
            raise HTTPException(404, f"no card {card_id}")
        return card

    @app.get("/api/run")
    def run_info():
        return {"seed": seed, "run": run, "meta": meta, **orch.summary(ctx), "fixture": "synthetic company, scripted controller",
                "runs": sorted(p.stem for p in db.parent.glob("*.db"))}

    @app.get("/api/cards")
    def cards(period: str | None = None, status: str | None = None, touched: bool | None = None):
        out = []
        for c in ctx.store.cards(period, status):
            if touched is not None and bool(c.get("human_touch")) != touched:
                continue
            ca = next((x for x in c["claims"] if x["desk"] == "cash_app"), {})
            out.append({"card_id": c["card_id"], "date": c["bank_line"]["date"], "amount": c["bank_line"]["amount"],
                        "text": c["bank_line"]["text"], "kind": c["kind"], "status": c["status"], "rung": ca.get("rung"),
                        "basis": ca.get("basis"), "human_touch": bool(c.get("human_touch")), "rounds": c.get("rounds", 0),
                        "policies": [r["policy"] for r in ca.get("residuals", []) if r.get("policy")],
                        "failed": [k["rule"] for k in c["checks"] if not k["ok"]]})
        return out

    @app.get("/api/cards/{card_id}")
    def one_card(card_id: str):
        return card_view(ctx, card_or_404(card_id))

    @app.get("/api/queue")
    def queue():
        return [{"card_id": c["card_id"], "date": c["bank_line"]["date"], "amount": c["bank_line"]["amount"],
                 "text": c["bank_line"]["text"], "item": c.get("review")} for c in ctx.store.cards(status="escalated")]

    @app.post("/api/cards/{card_id}/resolve")
    def resolve(card_id: str, a: Answer):
        """The human's answer. Saves the decision trace and returns the rule drafts with their backtests, unapproved."""
        card = card_or_404(card_id)
        if card["status"] != "escalated" or not card.get("review"):
            raise HTTPException(409, "this card is not waiting for a human")
        if not a.reason.strip():
            raise HTTPException(422, "a decision needs a reason")
        if a.option_id not in {o["id"] for o in card["review"]["options"]}:
            raise HTTPException(422, "no such option")
        out = orch.decide(ctx, card, a.option_id, a.reason.strip(), a.approver, approve=False)
        if not out["drafts"]:  # nothing to approve: the card can move straight away
            orch.rework(ctx, card_id)
        ctx.store.commit()
        return {**out, "card": card_view(ctx, ctx.store.card(card_id))}

    @app.post("/api/policies/approve")
    def approve(a: Approval):
        """Approve the drafts a decision produced, then run the card again with the answer and the new rule on it."""
        card_id = ctx.store.one("SELECT card_id FROM decisions WHERE decision_id=?", a.decision_id)
        if not card_id:
            raise HTTPException(404, "no such decision")
        live = orch.approve_drafts(ctx, a.decision_id, a.approver)
        orch.rework(ctx, card_id)
        ctx.store.commit()
        return {"live": live, "card": card_view(ctx, ctx.store.card(card_id))}

    @app.post("/api/continue")
    def carry_on():
        """Let the scripted controller answer everything else and run the calendar to the end of March."""
        human = Controller(world / "truth")
        for c in ctx.store.cards(status="escalated"):
            orch.work(ctx, c, human)
        orch.Run(ctx, human=human, log=lambda *_: None).advance(orch.END)
        ctx.store.commit()
        return {**orch.summary(ctx), "months": metrics.measure(db, world)["months"]}

    @app.get("/api/policies")
    def policies():
        out = {}
        for p in ctx.book.all():
            code = p["code"].split("~")[0]
            uses = [dict(r) for r in ctx.store.q("SELECT card_id, customer_id, day, amount FROM policy_uses WHERE code=? AND version=? ORDER BY day",
                                                 p["code"], p["version"])]
            out.setdefault(code, []).append({**p, "uses": uses})
        decisions = {json.loads(r[0])["decision_id"]: json.loads(r[0]) for r in ctx.store.q("SELECT doc FROM decisions")}
        return {"policies": out, "decisions": decisions}

    @app.get("/api/close")
    def closes():
        return {p: ctx.store.note("close", p) for p in metrics.PERIODS if ctx.store.note("close", p)}

    @app.get("/api/accounts/{account}")
    def account(account: str, period: str | None = None):
        """Drill-down: every ledger line behind a balance, with the card it came from."""
        if account not in ACCOUNTS:
            raise HTTPException(404, "no such account")
        rows = ctx.store.q("SELECT e.entry_id, e.date, e.card_id, e.source, e.memo, l.debit, l.credit, l.customer_id, l.invoice_id "
                           "FROM lines l JOIN entries e USING (entry_id) WHERE l.account=? AND (? IS NULL OR e.period<=?) "
                           "ORDER BY e.date, e.entry_id", account, period, period)
        return {"account": account, "balance": sum(r["debit"] - r["credit"] for r in rows), "lines": [dict(r) for r in rows]}

    @app.get("/api/findings")
    def findings():
        return [{"finding_id": r["finding_id"], "period": r["period"], **json.loads(r["doc"])} for r in
                ctx.store.q("SELECT * FROM findings ORDER BY finding_id")]

    @app.get("/api/scoreboard")
    def scoreboard():
        board = metrics.load(seed, ROOT)
        if not board:
            raise HTTPException(404, "no scoreboard yet: run `python run.py --all`")
        return board

    @app.get("/api/chart.svg")
    def chart():
        f = db.parent / "touchless.svg"
        if not f.exists():
            raise HTTPException(404, "no chart yet: run `python run.py --all`")
        return Response(f.read_text(), media_type="image/svg+xml")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "ui" / "index.html")

    app.mount("/", StaticFiles(directory=ROOT / "ui"), name="ui")
    app.state.ctx = ctx
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--run", default="demo", help="demo, A, B or C: a database under runs/seed-N/")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    uvicorn.run(build(a.seed, a.run), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
