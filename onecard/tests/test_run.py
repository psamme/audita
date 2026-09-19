"""End to end over three months, seed 7, model off: the claims the build doc makes, as assertions."""
import copy
import json

from spine.checker import check_card, failed
from spine.orchestrator import open_card

from conftest import finished

MONTHS = ["2026-01", "2026-02", "2026-03"]


def cash_claim(card):
    return next(c for c in card["claims"] if c["desk"] == "cash_app")


def hero_cards(ctx):
    """Acme's three short-pays: the January hero wire, its February twin, the March repeat."""
    return [c for c in ctx.store.cards() if c["kind"] == "receipt" and cash_claim(c).get("customer_id") == "CUST-001"
            and any(r.get("reason") == "DISCOUNT_TAKEN" or r.get("candidate") == "DISCOUNT_TAKEN" for r in cash_claim(c)["residuals"])]


def test_cards_and_memory_post_nothing_wrong_and_every_month_locks(runs):
    for config in "BC":
        months = runs[config]["score"]["months"]
        assert [months[m]["wrong_postings"] for m in MONTHS] == [0, 0, 0], runs[config]["score"]["misses"]
        assert all(months[m]["locked"] and months[m]["contradictions"] == 0 for m in MONTHS)
        assert all(months[m]["balance_error_total"] == 0 for m in MONTHS)
        assert all(months[m]["needs_human_by_authority"]["none"] == 0 for m in MONTHS)  # no lucky guesses


def test_independent_desks_leave_contradictions_and_a_drifting_balance(runs):
    months = runs["A"]["score"]["months"]
    assert all(months[m]["contradictions"] > 0 for m in MONTHS)
    assert months["2026-03"]["balance_error_total"] > months["2026-01"]["balance_error_total"] > 0
    assert any(w["traps"] == [4] for w in runs["A"]["score"]["misses"])  # the refund posted twice is never reversed


def test_memory_is_what_lowers_the_questions(runs):
    b, c = runs["B"]["score"]["months"], runs["C"]["score"]["months"]
    assert b["2026-01"]["human_decisions"] == c["2026-01"]["human_decisions"]
    for m in MONTHS[1:]:
        assert c[m]["human_decisions"] < b[m]["human_decisions"]
        assert c[m]["touchless_rate"] > b[m]["touchless_rate"]


def test_hero_loop_answer_becomes_rule_then_twin_resolves_itself_then_guardrail_trips(runs):
    ctx = runs["C"]["ctx"]
    jan, feb, mar = hero_cards(ctx)
    fee, short = cash_claim(jan)["residuals"]
    assert fee["policy"] == "FEE-WIRE-01@v1" and fee["amount"] == 3500        # cites the rule learned a week earlier
    assert short["amount"] == 1240 and short["decision"] and jan["human_touch"]  # the $12.40 went to a human
    v1 = ctx.book.versions("SHORTPAY-01")[0]
    assert v1["scope"]["customers"] == ["CUST-001"] and v1["condition"]["max_amount"] == 1240 and v1["backtest"]["conflicts"] == 0
    twin = cash_claim(feb)["residuals"][0]
    assert not feb["human_touch"] and twin["policy"].startswith("SHORTPAY-01@") and feb["status"] == "posted"
    asked = json.loads(ctx.store.q("SELECT doc FROM decisions WHERE card_id=?", mar["card_id"])[0][0])
    assert asked["saw"]["why"] == "repeat_guardrail" and asked["treatment"] == "leave_open_chase"
    assert ctx.book.live() and any(p["condition"].get("max_uses_per_customer_per_quarter") == 2 for p in ctx.book.versions("SHORTPAY-01"))


def test_every_live_rule_traces_to_a_human_decision_and_a_clean_backtest(runs):
    ctx = runs["C"]["ctx"]
    decided = {r[0] for r in ctx.store.q("SELECT decision_id FROM decisions")}
    for p in ctx.book.all():
        assert p["source_decisions"] and set(p["source_decisions"]) <= decided
        assert p["backtest"]["conflicts"] == 0 and p["approved_by"]


def test_duplicate_refund_is_reversed_against_its_card(runs):
    ctx = runs["C"]["ctx"]
    reversed_ = [c for c in ctx.store.cards() if any(x.get("reversed_duplicates") for x in c["claims"])]
    assert len(reversed_) == 3
    for c in reversed_:
        assert ctx.tools.ledger.card_net(c["card_id"]) == {"cash": c["bank_line"]["amount"], "refunds": -c["bank_line"]["amount"]}


def test_audit_finds_every_planted_control_breach_with_no_false_findings(runs):
    months = runs["C"]["score"]["months"]
    assert sum(months[m]["audit"]["planted"] for m in MONTHS) == 3
    assert all(months[m]["audit"]["caught"] == months[m]["audit"]["planted"] and months[m]["audit"]["false_findings"] == 0 for m in MONTHS)


def test_the_checker_catches_a_tampered_claim(runs):
    ctx = runs["C"]["ctx"]
    card = next(c for c in ctx.store.cards("2026-03") if c["kind"] == "receipt" and len(cash_claim(c)["settles"]) == 1 and not cash_claim(c)["residuals"])
    assert not failed(check_card(ctx, card, final=True))
    short = copy.deepcopy(card)
    inv = next(iter(cash_claim(short)["settles"]))
    cash_claim(short)["settles"][inv] -= 100
    assert "amounts_tie" in {c["rule"] for c in failed(check_card(ctx, short, final=True))}
    stolen = copy.deepcopy(card)
    other = next(i for i in ctx.tools._invoices if i["customer_id"] != cash_claim(card)["customer_id"])
    cash_claim(stolen)["settles"] = {other["invoice_id"]: card["bank_line"]["amount"]}
    assert "payer_owns_invoice" in {c["rule"] for c in failed(check_card(ctx, stolen, final=True))}
    unevidenced = copy.deepcopy(card)
    cash_claim(unevidenced)["evidence"] = []
    assert "evidence_complete" in {c["rule"] for c in failed(check_card(ctx, unevidenced, final=True))}


def test_same_seed_same_numbers(world, tmp_path, runs):
    again = finished(world, tmp_path / "again.db", "C")
    rows = lambda ctx: [tuple(r) for r in ctx.store.q("SELECT entry_id, date, card_id, memo FROM entries ORDER BY entry_id")]
    assert rows(again) == rows(runs["C"]["ctx"])


def test_intake_attaches_evidence_without_the_answer(runs):
    ctx = runs["C"]["ctx"]
    line = next(b for b in ctx.tools.bank_lines() if b["amount"] > 0 and not b["text"].startswith("STRIPE"))
    card = open_card(ctx, line)
    assert card["evidence"][0] == f"bank:{line['line_id']}" and set(card) >= {"card_id", "claims", "checks", "status"}
    assert not {"traps", "hero", "needs_human"} & set(card)
