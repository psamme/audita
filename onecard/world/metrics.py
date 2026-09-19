"""The scoreboard: every metric in the build doc, measured against the answer key. World side of the wall: this module
reads truth/ and a finished run's database. Nothing here is visible to a desk.
"""
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spine.contract import KEY_BALANCES, usd  # noqa: E402
from spine.ledger import Ledger  # noqa: E402
from spine.policy import PolicyBook  # noqa: E402
from spine.store import Store  # noqa: E402

PERIODS = ["2026-01", "2026-02", "2026-03"]
GRID = {"BANK_FEE": range(500, 10001, 500), "DISCOUNT_TAKEN": range(250, 5001, 250),
        "DUPLICATE_PAYMENT": (100000, 1000000), "PREPAID_CREDITS": (500000, 2500000), "AMBIGUOUS_ALLOCATION": (1000000,)}


def _net(d: dict) -> dict:
    return {k: v for k, v in d.items() if v}


def card_verdict(store: Store, ledger: Ledger, card: dict, truth: dict) -> dict | None:
    """None when the card's final entry and applications equal the answer key, else what differs."""
    if card["status"] != "posted":
        return {"cause": f"never posted (status {card['status']})", "dollars": abs(truth["amount"])}
    got, want = _net(ledger.card_net(card["card_id"])), _net(truth["entry"])
    diff = {a: (got.get(a, 0), want.get(a, 0)) for a in set(got) | set(want) if got.get(a, 0) != want.get(a, 0)}
    if diff:
        return {"cause": "entry differs: " + ", ".join(f"{a} posted {usd(g)} key {usd(w)}" for a, (g, w) in sorted(diff.items())),
                "dollars": sum(abs(g - w) for g, w in diff.values()) // 2 or max(abs(g - w) for g, w in diff.values())}
    if truth["kind"] == "receipt":
        applied = {r["invoice_id"]: r["n"] for r in store.q(
            "SELECT invoice_id, SUM(amount) AS n FROM applications WHERE card_id=? GROUP BY invoice_id", card["card_id"])}
        if _net(applied) != _net(truth["settles"]):
            moved = sum(abs(applied.get(i, 0) - truth["settles"].get(i, 0)) for i in set(applied) | set(truth["settles"])) // 2
            return {"cause": f"right accounts, wrong invoices: applied {sorted(applied)} key {sorted(truth['settles'])}", "dollars": moved}
    return None


def authority(card: dict) -> str:
    """How a judgement call on this card was authorised: a human, a learned policy, or nothing (a guess)."""
    if card.get("human_touch"):
        return "human"
    ca = next((c for c in card["claims"] if c["desk"] == "cash_app"), None) or {}
    judged = [r for r in ca.get("residuals", []) if r.get("treatment")]
    if ca.get("allocation_policy") or (judged and all(r.get("policy") or r.get("decision") for r in judged)):
        return "policy"
    return "none"


def policy_accuracy(live: list[dict], hidden: dict) -> dict:
    """Ask the learned book and the hidden policy the same questions about a customer neither has seen."""
    out = {}
    for reason, grid in GRID.items():
        rule, agree, wrong, silent = hidden[reason], 0, 0, 0
        for amount in grid:
            want = rule["treatment"] if amount <= rule.get("max_amount", amount) else rule["else"]
            fires = {p["action"]["treatment"] for p in live if p["scope"]["reason"] == reason
                     and PolicyBook.in_scope(p, "CUST-UNSEEN", amount, 0) is None}
            agree, wrong, silent = agree + (fires == {want}), wrong + bool(fires and fires != {want}), silent + (not fires)
        mine = [p for p in live if p["scope"]["reason"] == reason and p["action"]["treatment"] == rule["treatment"]]
        learned = {"scope": mine[-1]["scope"]["customers"], **mine[-1]["condition"]} if mine else None
        out[reason] = {"agree": agree, "wrong": wrong, "silent": silent, "questions": len(grid), "learned": learned,
                       "hidden": {k: v for k, v in rule.items() if k.startswith("max")}}
    total = sum(v["questions"] for v in out.values())
    return {"by_reason": out, "agree_rate": sum(v["agree"] for v in out.values()) / total,
            "wrong_rate": sum(v["wrong"] for v in out.values()) / total}


def forecast_error(store: Store, public: Path) -> dict:
    """Four-week-ahead receipts forecast against what the bank then showed, learned lags against fixed due dates."""
    receipts = {}
    for f in sorted(public.glob("bank_*.csv")):
        for r in csv.DictReader(open(f)):
            if int(r["amount"]) > 0 and not r["text"].startswith("STRIPE"):
                receipts[r["date"]] = receipts.get(r["date"], 0) + int(r["amount"])
    weeks = []
    for row in store.q("SELECT key, doc FROM notebook WHERE desk='forecast_snap' ORDER BY key"):
        snap, made = json.loads(row["doc"]), date.fromisoformat(row["key"])
        a, b = made + timedelta(days=28), made + timedelta(days=34)
        if b.isoformat() > "2026-03-31":
            continue
        actual = sum(v for d, v in receipts.items() if a.isoformat() <= d <= b.isoformat())
        weeks.append({"forecast_made": row["key"], "week": a.isoformat(), "actual": actual,
                      "learned_error": abs(snap["learned"][4]["receipts"] - actual),
                      "fixed_error": abs(snap["fixed"][4]["receipts"] - actual)})
    n = max(len(weeks), 1)
    return {"weeks": weeks, "learned_mean_abs_error": sum(w["learned_error"] for w in weeks) // n,
            "fixed_mean_abs_error": sum(w["fixed_error"] for w in weeks) // n}


def measure(db: Path, world: Path) -> dict:
    """Everything about one finished run."""
    store = Store(db)
    ledger = Ledger(store)
    truth = json.loads((world / "truth" / "truth.json").read_text())
    hidden = json.loads((world / "truth" / "hidden_policy.json").read_text())
    months, misses = {}, []
    usage = store.note("scoreboard", "usage", {})
    for period in PERIODS:
        cards = store.cards(period)
        if not cards:
            continue
        lines = {k: v for k, v in truth["lines"].items() if v["date"].startswith(period)}
        wrong = []
        for c in cards:
            v = card_verdict(store, ledger, c, truth["lines"][c["bank_line"]["line_id"]])
            if v:
                wrong.append({"card_id": c["card_id"], "period": period, "traps": truth["lines"][c["bank_line"]["line_id"]]["traps"], **v})
        for d in truth["duplicate_entries"]:  # a hand-keyed duplicate left standing is a wrong posting with no card
            row = store.q("SELECT entry_id, date FROM entries WHERE erp_id=?", d["erp_id"])
            if row and row[0]["date"].startswith(period) and not store.one("SELECT COUNT(*) FROM entries WHERE reverses=?", row[0]["entry_id"]):
                cents = store.one("SELECT SUM(credit) FROM lines WHERE entry_id=? AND account='cash'", row[0]["entry_id"])
                wrong.append({"card_id": None, "period": period, "traps": [4], "dollars": cents,
                              "cause": f"{row[0]['entry_id']} ({d['erp_id']}) duplicates a refund and was never reversed"})
        misses += wrong
        receipts = [c for c in cards if c["kind"] == "receipt"]
        needs = [c for c in cards if lines[c["bank_line"]["line_id"]]["needs_human"]]
        escalated = [c for c in cards if c.get("human_touch")]
        how = [authority(c) for c in needs]
        report = store.note("close", period) or {}
        audit = store.note("scoreboard", f"audit:{period}", {"findings": [], "sampled": 0, "agreed": 0})
        planted = {b["erp_id"] for b in truth["control_breaches"]
                   if any(e["erp_id"] == b["erp_id"] and e["date"].startswith(period) for e in _erp(world))}
        found = {f.get("erp_id") for f in audit["findings"] if f["control"] == "preparer_is_not_approver"}
        wrong_ids = {w["card_id"] for w in wrong}
        false_findings = [f for f in audit["findings"] if not (
            (f["control"] == "preparer_is_not_approver" and f.get("erp_id") in planted)
            or (f["control"] == "reperformance" and f.get("card_id") in wrong_ids))]
        balances = report.get("balances", {})
        gaps = {a: abs(balances.get(a, 0) - truth["balances"][period].get(a, 0)) for a in KEY_BALANCES}
        months[period] = {
            "cards": len(cards), "touchless": sum(1 for c in cards if not c.get("human_touch") and c["status"] == "posted"),
            "touchless_rate": sum(1 for c in cards if not c.get("human_touch") and c["status"] == "posted") / len(cards),
            "receipts": len(receipts),
            "touchless_rate_receipts": sum(1 for c in receipts if not c.get("human_touch") and c["status"] == "posted") / max(len(receipts), 1),
            "human_decisions": store.one("SELECT COUNT(*) FROM decisions WHERE day LIKE ?", period + "%"),
            "wrong_postings": len(wrong), "wrong_dollars": sum(w["dollars"] for w in wrong),
            "contradictions": len(report.get("contradictions", [])), "locked": bool(report.get("locked")),
            "checklist_passed": bool(report.get("passed")),
            "balance_error": gaps, "balance_error_total": sum(gaps.values()),
            "escalated": len(escalated), "needs_human": len(needs),
            "escalation_precision": (sum(1 for c in escalated if c in needs) / len(escalated)) if escalated else None,
            "escalation_recall": (sum(1 for h in how if h != "none") / len(needs)) if needs else None,
            "needs_human_by_authority": {k: how.count(k) for k in ("human", "policy", "none")},
            "policy_accuracy": policy_accuracy(store.note("scoreboard", f"policies:{period}", []), hidden),
            "audit": {"sampled": audit["sampled"], "agreed": audit["agreed"], "planted": len(planted),
                      "caught": len(planted & found), "false_findings": len(false_findings),
                      "reperformance_disagreements": sum(1 for f in audit["findings"] if f["control"] == "reperformance")},
            "model_calls": usage.get(period, {}).get("calls", 0), "model_cost_usd": usage.get(period, {}).get("cost_usd", 0.0)}
    return {"meta": store.note("run", "meta", {}), "months": months, "misses": misses,
            "forecast": forecast_error(store, world / "public"),
            "policies": [f"{p['code']}@v{p['version']}" for p in PolicyBook(store).live()]}


_ERP = {}


def _erp(world: Path) -> list[dict]:
    if world not in _ERP:
        _ERP[world] = json.loads((world / "public" / "erp_entries.json").read_text())
    return _ERP[world]


def scoreboard(seed: int, configs: list[str], root: Path) -> dict:
    out = root / "runs" / f"seed-{seed}"
    board = load(seed, root) or {"seed": seed, "configs": {}}
    for c in configs:
        board["configs"][c] = measure(out / f"{c}.db", root / "data" / f"seed-{seed}")
    (out / "scoreboard.json").write_text(json.dumps(board, indent=1))
    from . import chart
    (out / "touchless.svg").write_text(chart.touchless_svg(board))
    return board


def load(seed: int, root: Path) -> dict | None:
    f = root / "runs" / f"seed-{seed}" / "scoreboard.json"
    return json.loads(f.read_text()) if f.exists() else None


# ---- text output ---------------------------------------------------------------------------------------------------
def pct(x) -> str:
    return "   -  " if x is None else f"{100 * x:5.1f}%"


def render(board: dict) -> str:
    rows = [f"\nScoreboard, seed {board['seed']} (model {next(iter(board['configs'].values()))['meta'].get('model', '?')})",
            f"{'':14}" + "".join(f"{p:>30}" for p in PERIODS)]
    names = {"A": "A independent", "B": "B no memory", "C": "C full"}
    for c, run in sorted(board["configs"].items()):
        m = run["months"]
        cell = lambda f: "".join(f"{f(m[p]):>30}" if p in m else f"{'':>30}" for p in PERIODS)
        rows += [f"{names[c]:<14}" + cell(lambda x: f"touchless {pct(x['touchless_rate'])} (receipts {pct(x['touchless_rate_receipts'])})"),
                 f"{'':14}" + cell(lambda x: f"wrong {x['wrong_postings']} ({usd(x['wrong_dollars'])})"),
                 f"{'':14}" + cell(lambda x: f"contradictions {x['contradictions']}, humans asked {x['human_decisions']}"),
                 f"{'':14}" + cell(lambda x: f"balance error {usd(x['balance_error_total'])}"),
                 f"{'':14}" + cell(lambda x: f"esc. precision {pct(x['escalation_precision'])} recall {pct(x['escalation_recall'])}"),
                 f"{'':14}" + cell(lambda x: f"policy agree {pct(x['policy_accuracy']['agree_rate'])} wrong {pct(x['policy_accuracy']['wrong_rate'])}"),
                 f"{'':14}" + cell(lambda x: f"audit caught {x['audit']['caught']}/{x['audit']['planted']}, false {x['audit']['false_findings']}"),
                 f"{'':14}" + cell(lambda x: f"model calls {x['model_calls']} (${x['model_cost_usd']:.2f})"),
                 f"{'':14}forecast, 4 weeks ahead: learned lags {usd(run['forecast']['learned_mean_abs_error'])} mean abs error, "
                 f"fixed due dates {usd(run['forecast']['fixed_mean_abs_error'])} ({len(run['forecast']['weeks'])} weeks)", ""]
    for c, run in sorted(board["configs"].items()):
        for w in run["misses"]:
            rows.append(f"  miss [{c}] {w['period']} {w['card_id'] or 'no card':<16} traps {w['traps']} {usd(w['dollars'])}: {w['cause']}")
    return "\n".join(rows)


def render_range(boards: list[dict]) -> str:
    rows = [f"\nRange across seeds {[b['seed'] for b in boards]}: touchless rate min to max, wrong postings summed"]
    for c in sorted(boards[0]["configs"]):
        cells = []
        for p in PERIODS:
            rates = [b["configs"][c]["months"][p]["touchless_rate"] for b in boards if p in b["configs"][c]["months"]]
            wrong = sum(b["configs"][c]["months"][p]["wrong_postings"] for b in boards if p in b["configs"][c]["months"])
            cells.append(f"{100 * min(rates):.1f} to {100 * max(rates):.1f}%, wrong {wrong}")
        rows.append(f"  {c}  " + "   ".join(f"{p}: {x}" for p, x in zip(PERIODS, cells)))
    return "\n".join(rows)
