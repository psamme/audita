"""Can this company's history actually teach a playbook, and what will it cost?

Runs the same `cases` and `clusters` computation induction runs, so the numbers reported here are
the numbers that will be sent to the model. Costs nothing and takes seconds.

Blocking checks are the ones where induction either raises or produces a playbook of rules that
cite nothing. Warnings are the ones where it produces something, but less than the company
expects, and the reason is worth saying out loud before they pay for it.
"""
import json

from shadow import db, history, llm, playbook as pbmod
from shadow.onboard import company
from shadow.onboard.coldstart import AUTO_PREFIX as _AUTO

OK, WARN, BLOCK = "ok", "warn", "block"


def _c(check: str, level: str, detail: str, fix: str = "") -> dict:
    return {"check": check, "level": level, "detail": detail, "fix": fix}


def run(client: str, before: str | None = None) -> dict:
    checks: list[dict] = []
    if not company.valid_id(client):
        return {"ready": False, "checks": [_c("company id", BLOCK,
                "the id must be 1 to 8 letters, digits or underscores",
                "recreate the company with a simpler id")], "blocking": 1}
    if not company.exists(client):
        return {"ready": False, "checks": [_c("company", BLOCK, "no company database yet",
                "finish the setup step first")], "blocking": 1}

    con = db.connect(client, readonly=True)
    try:
        info = db.q(con, "SELECT * FROM client")
        users = db.q(con, "SELECT * FROM user")
        ps = [r["period"] for r in db.q(con, "SELECT DISTINCT period FROM bank_line ORDER BY 1")]
        before = before or _default_before(ps)

        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("bank_line", "ledger_entry", "reconcile_link", "journal_entry",
                            "approval", "document")}
        n_bank_hist = con.execute("SELECT COUNT(*) FROM bank_line WHERE period < ?", (before,)).fetchone()[0]
        n_links = con.execute("SELECT COUNT(*) FROM reconcile_link WHERE period < ?", (before,)).fetchone()[0]
        null_posted = con.execute("SELECT COUNT(*) FROM ledger_entry WHERE posted_at IS NULL OR posted_at=''").fetchone()[0]
        null_desc = con.execute("SELECT COUNT(*) FROM bank_line WHERE description IS NULL").fetchone()[0]
        je_linked = con.execute("SELECT COUNT(*) FROM journal_entry WHERE bank_id IS NOT NULL").fetchone()[0]

        # --- blocking -----------------------------------------------------------------
        if not info:
            checks.append(_c("company row", BLOCK, "the client record is missing", "redo setup"))
        else:
            chart = info[0].get("chart") or {}
            checks.append(_c("chart of accounts", OK if chart else BLOCK,
                             f"{len(chart)} accounts" if chart else "no chart of accounts",
                             "" if chart else "import or paste the chart: rules name accounts, and "
                                              "the model is shown the chart when it writes them"))
            blurb = (info[0].get("blurb") or "").strip()
            checks.append(_c("business description", OK if blurb else WARN,
                             blurb[:80] or "empty",
                             "" if blurb else "a sentence about what the company does goes to the "
                                              "model verbatim and measurably improves the rules"))
        seniors = [u for u in users if u["senior"]]
        checks.append(_c("senior roles", OK if seniors else BLOCK,
                         ", ".join(sorted({u["role"] for u in seniors})) or "nobody is marked senior",
                         "" if seniors else "mark at least one person senior. Escalations are "
                                            "addressed to a senior role, and with none the agent "
                                            "rejects every escalation it tries to make"))
        checks.append(_c("ledger posting dates", OK if not null_posted else BLOCK,
                         f"{null_posted} entries have no posting date" if null_posted else "all present",
                         "" if not null_posted else "re-import the ledger; the importer fills this "
                                                    "from the transaction date when the column is absent"))
        checks.append(_c("bank descriptions", OK if not null_desc else BLOCK,
                         f"{null_desc} bank lines have no description" if null_desc else "all present",
                         "" if not null_desc else "re-import the statement"))
        checks.append(_c("history to learn from", OK if ps and before > (ps[0] if ps else "") else BLOCK,
                         f"{len(ps)} periods: {', '.join(ps)}" if ps else "no bank lines imported",
                         "" if ps else "import a bank statement first"))
        checks.append(_c("reconciliation trail", OK if n_links else BLOCK,
                         f"{n_links} links across {n_bank_hist} bank lines before {before}"
                         if n_links else "nothing records who cleared what",
                         "" if n_links else "import a reconciliation report, or use the guided "
                                            "labelling step to build the trail from your own decisions"))
        # Derived links are triage, not evidence: they reproduce exactly the routine matches that
        # induction discards. A trail made only of them looks full and teaches nothing.
        derived = con.execute("SELECT COUNT(*) FROM reconcile_link WHERE id LIKE ?",
                              (f"{client}-{_AUTO}-%",)).fetchone()[0]
        human = n_links - derived
        if n_links:
            checks.append(_c("decisions of your own", OK if human or je_linked else BLOCK,
                             f"{human} links and {je_linked} adjustments came from your records; "
                             f"{derived} links were derived by matching",
                             "" if human or je_linked else
                             "every link here was derived by the matcher, and those are the easy "
                             "one-to-one clears the agent already makes on its own. Nothing yet "
                             "records a judgement call, so there is nothing to learn. Work through "
                             "the labelling step, or import your reconciliation history."))

        # --- warnings -----------------------------------------------------------------
        if n_links and n_bank_hist:
            density = n_links / n_bank_hist
            checks.append(_c("trail coverage", OK if density >= 0.4 else WARN,
                             f"{density:.0%} of historical bank lines have a link",
                             "" if density >= 0.4 else "the playbook will be induced from a "
                                                       "minority of your history"))
        whens = [r["reconciled_at"][:10] for r in db.q(
            con, "SELECT reconciled_at FROM reconcile_link WHERE reconciled_at IS NOT NULL")]
        if whens:
            distinct = len(set(whens))
            top = max(whens.count(w) for w in set(whens)) / len(whens)
            backfilled = distinct < 5 or top > 0.6
            checks.append(_c("clearing timestamps", WARN if backfilled else OK,
                             f"{distinct} distinct dates, most common covers {top:.0%}",
                             "" if not backfilled else
                             "these look backfilled to a migration date. The agent uses how long "
                             "an item took to tell routine work from a judgement call, so every "
                             "item will read as unusual and almost everything will be sent for "
                             "review. Real clearing dates are worth the trouble."))
        checks.append(_c("adjusting entries", OK if je_linked else WARN,
                         f"{je_linked} adjustments tied to a bank line",
                         "" if je_linked else "without these only matching and escalation can be "
                                              "learned; no rule will ever name an account"))
        senior_ids = {u["id"] for u in seniors}
        in_trail = senior_ids & ({r["reconciled_by"] for r in db.q(con, "SELECT reconciled_by FROM reconcile_link")}
                                 | {r["posted_by"] for r in db.q(con, "SELECT posted_by FROM journal_entry")}
                                 | {r["approver"] for r in db.q(con, "SELECT approver FROM approval")})
        checks.append(_c("seniors in the trail", OK if in_trail or counts["approval"] else WARN,
                         f"{len(in_trail)} senior people appear in past work" if in_trail
                         else "no senior appears anywhere in the history",
                         "" if in_trail or counts["approval"] else
                         "the agent learns who to escalate to by seeing who handled things. "
                         "With no senior in the trail it cannot learn any escalation rule."))
        unknown = _unknown_actors(con, {u["id"] for u in users})
        if unknown:
            checks.append(_c("people in the trail", WARN,
                             f"{len(unknown)} ids in the history are not on the roster, "
                             f"for example {sorted(unknown)[:3]}",
                             "add them in setup, or re-import with ids that match"))

        cross = con.execute(
            "SELECT COUNT(*) FROM reconcile_link l JOIN ledger_entry e ON e.id=l.ledger_id "
            "WHERE l.period <> e.period").fetchone()[0]
        checks.append(_c("items carried between months", OK if cross else WARN,
                         f"{cross} links clear a ledger entry from an earlier month",
                         "" if cross else "nothing in the history shows an item waiting for a "
                                           "later month, so no carry-forward rule can be learned"))

        # --- the real prompt, measured ------------------------------------------------
        cases = pbmod.cases(con, before)
        clusters = pbmod.clusters(cases)
        observed = history.observe(con, before)
        non_routine = sum(1 for o in observed if not o["routine"])
        payload = len(json.dumps(clusters, default=str))
    finally:
        con.close()

    checks.append(_c("exceptions to learn from", OK if len(cases) >= 20 else WARN,
                     f"{len(cases)} non-routine items in {len(clusters)} recurring shapes",
                     "" if len(cases) >= 20 else
                     "thin history. A rule needs three comparable past items before it runs on "
                     "its own, so expect questions rather than finished rules."))
    big = len(clusters) > 120 or payload > 400_000
    checks.append(_c("variety of shapes", BLOCK if len(clusters) > 300 else (WARN if big else OK),
                     f"{len(clusters)} shapes, {payload // 1024} KB of examples",
                     "" if not big else
                     "free-text bank descriptions split into many one-off shapes, which makes the "
                     "prompt mostly noise. Import a shorter window of history."))
    if observed:
        share = non_routine / len(observed)
        checks.append(_c("share needing judgement", OK if share <= 0.5 else WARN,
                         f"{share:.0%} of past items were not routine",
                         "" if share <= 0.5 else
                         "unusually high. Usually this means clearing timestamps are wrong, or "
                         "the whole general ledger was imported instead of the cash accounts."))
    ledger_per_bank = (db_count(client, "ledger_entry") / max(1, db_count(client, "bank_line")))
    checks.append(_c("ledger size", OK if ledger_per_bank <= 5 else WARN,
                     f"{ledger_per_bank:.1f} ledger entries per bank line",
                     "" if ledger_per_bank <= 5 else
                     "that looks like the whole general ledger. The matcher needs the cash-clearing "
                     "subset, or it finds several equally good candidates and sends everything to "
                     "review."))

    est = estimate(payload)
    blocking = [c for c in checks if c["level"] == BLOCK]
    return {"ready": not blocking, "before": before, "checks": checks,
            "blocking": len(blocking), "warnings": sum(c["level"] == WARN for c in checks),
            "clusters": len(clusters), "cases": len(cases), "payload_bytes": payload,
            "counts": counts, "estimate": est}


def db_count(client: str, table: str) -> int:
    con = db.connect(client, readonly=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def _unknown_actors(con, known: set[str]) -> set[str]:
    seen = set()
    for sql, col in (("SELECT DISTINCT reconciled_by FROM reconcile_link", "reconciled_by"),
                     ("SELECT DISTINCT posted_by FROM journal_entry", "posted_by"),
                     ("SELECT DISTINCT approver FROM approval", "approver")):
        seen |= {r[col] for r in db.q(con, sql) if r[col]}
    return seen - known


def estimate(payload_bytes: int) -> dict:
    """Induction is one call plus up to three repair rounds; in practice it uses all of them."""
    pin, pout = llm.PRICES.get(llm.MODEL, llm.PRICES["claude-opus-5"])
    in_tokens = payload_bytes / 3.6 + 2000          # cluster JSON plus the system prompt
    first = (in_tokens * pin + 12000 * pout) / 1e6
    repair = 3 * (in_tokens * 0.35 * pin + 6000 * pout) / 1e6
    return {"llm_calls": "1 to 4", "usd": round(first + repair, 2),
            "seconds": int(60 + payload_bytes / 900), "model": llm.MODEL,
            "note": "a first pass plus up to three rounds of the model fixing rules that "
                    "disagreed with your own history"}


def _default_before(periods: list[str]) -> str:
    """Learn from everything imported, by default."""
    if not periods:
        return "9999-99"
    y, m = map(int, periods[-1].split("-"))
    return f"{y + (m == 12)}-{(m % 12) + 1:02d}"
