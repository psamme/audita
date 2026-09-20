# Codex brief: Shadow Onboarding (HackMIT 2026, Maximor track)

You are joining a hackathon project that is already mid-build. Three Claude Code sessions are working in the main checkout right now: one builds the agent, one owns the UI, one owns git and coordination. You are a fourth pair of hands and, just as important, a second opinion from a different model. Read this whole file, then `README.md`, then `docs/RUNBOOK.md` and `docs/API.md`. The original project brief that started all of this is reproduced in full in the appendix; treat it as the source of truth for scope and the cut list.

## 1. The project in five sentences

Bank-to-ledger reconciliation where the agent gets no per-client configuration. It reads how a client's finance team handled past exceptions in months 1 to 3 (as a realistic, messy ERP trail: reconcile links, terse journal entries, a sparse approvals log, emails) and writes that client's playbook: human-readable rules compiled to executable conditions, each citing the precedents behind it. A deterministic matcher clears the easy lines, compiled playbook rules clear known exception patterns, and an LLM investigator with tools works the residue and escalates to a named human role when precedents are thin, conflicting, or fraud-shaped. Human corrections and interview answers become visible playbook diffs. Two synthetic clients with opposite policies (a lenient vending and laundromat roll-up, a strict invented AI lab) receive the identical ambiguous transaction and should resolve it differently, each citing its own history.

What we added beyond the original brief, all built but only partly exercised end to end:

- **Ignorance bands.** Numeric rule conditions store a band from precedents (largest value where humans took the action, smallest where they did not) instead of one invented constant. Items inside the band escalate with reason `in_band`. The agent asks the one yes/no question that halves the widest band.
- **Questions to trust.** Headline metric: how many human answers until the agent auto-resolves most exceptions with zero wrong matches (`curve.py`, `GET /api/curve`).
- **Unlearning.** Playbook = induced v1 + ordered stored patches. `shadow/unlearn.py` retracts a correction or precedent, replays the rest deterministically, and reports the blast radius (past resolutions checked, re-opened).
- **Stale evidence.** `shadow/stale.py` re-verifies resolutions when ledger entries are edited, reversed, or unlinked.
- **Who may teach.** A correction that contradicts a well-supported rule raises a conflict with three outcomes: one-off exception, policy change with an effective date, or mistake.
- **Findings.** A precedent that violates an otherwise solid rule is reported as "a human broke their own pattern."

## 2. Hard rules (these protect the integrity of our numbers)

1. **Never read anything under `keys/`.** It holds hidden answer keys. Listing filenames is fine; opening files is not. Do not read `runs/*/grades*.json` either (item-level key data).
2. **Agent code never sees ground truth.** Nothing under `shadow/` may import from `sim/`, read `sim/POLICIES.md`, open `data/*/truth.db`, or contain client-specific knowledge (customer names, account numbers, dollar thresholds, role names). If you work on `shadow/`, you must not read `sim/POLICIES.md` or `sim/client_*.py` yourself, for the same reason.
3. **Do not tune against month 4.** All development uses the dev track (learn from January and February, grade on the March holdout: see "Developing without touching the hidden month" in `docs/RUNBOOK.md`). The hidden April test is run once, after a `freeze-1` git tag, by a teammate who has never seen the agent (`docs/TRUTH_LANE.md`).
4. **Model spend.** Anything that calls the LLM costs real money (a zero-shot month is roughly $10). Prefer `--no-llm` runs, unit tests, and replays. Ask Sam before any run that makes more than about 20 model calls. The product's LLM layer is `shadow/llm.py` (Anthropic SDK, with a `claude` CLI fallback). Do not swap providers or add an OpenAI dependency; this project is also being submitted to tracks where that matters.
5. **Respect the cut list** in the appendix (section 7). No third client, no multi-agent finance team, no forecasting or close, no computer use, no schema-mapping headline.
6. **Copy style.** No em dashes in any user-facing text or docs. Plain, concrete sentences. UI direction is restrained monochrome; do not touch `ui/` without asking.

## 3. How to work without colliding with three other agents

- **Use your own clone**, not Sam's working directory: `git clone git@github.com:psamme/audita.git shadow-codex`. Then `uv sync` and `uv run python -m sim.build` (deterministic, free, builds months 1 to 3). `uv run pytest -q` should pass before you change anything.
- **Branch and PR. Never push to `main`.** Branch names `codex/<topic>`. Small PRs, one topic each, opened with `gh pr create`. The control-panel Claude session reviews and merges. Rebase on `origin/main` often; `main` moves every 20 to 30 minutes.
- **File ownership.** Build session: `shadow/*` except `server.py`, plus `sim/*`, `experiments.py`, `curve.py`, `benchrec/*`. Control panel: `grade.py`, `shadow/server.py`, `README.md`, `docs/TRUTH_LANE.md`, git. Design session: `ui/*`. You may freely add NEW files. To change an owned file, keep the diff surgical and explain it in the PR; do not reformat or reorganise.
- **Every PR description states**: what changed, how you verified it (command and output), whether it needs model calls to verify, and any contract change to `docs/API.md`.

## 4. What we want from you, in priority order

### A. Independent review of the newest, least-tested code (read-only, highest value)
`shadow/rules.py` (bands), `shadow/playbook.py` (induction, back-test and repair loop, `band_questions`), `shadow/correct.py` (corrections, `answer_band`, conflicts), `shadow/unlearn.py`, `shadow/stale.py`, `shadow/guardrails.py`, `shadow/matcher.py`. An earlier audit found that every wrong match came from the deterministic tiers, not the LLM, so that is where to be most suspicious. Questions to answer:

- Can any path produce an automatic match or write-off that a careful controller would call wrong? Think: band edges (inclusive or exclusive?), a band computed from one precedent, bands after a retraction leaves zero precedents, a `policy_change` effective date that excludes all precedents, sign conventions on adjustments, rules firing in playbook order when two overlap.
- Is retraction really deterministic and idempotent? Retract twice, retract then re-apply, retract the first of several dependent patches. Does blast radius miss resolutions that depended on a rule only through a band value?
- Does stale detection catch: amount edit, date edit, account edit, reversal entry, link undone, new entry posted into a reconciled period? Does it false-positive on harmless changes?
- Who-may-teach: can a junior role widen auto-resolution through any route (corrections, band answers, interview answers)?
- Anything in `shadow/` that smells like client-specific knowledge.

Deliver `docs/REVIEW_CODEX.md`: ranked findings, each with file and line, a concrete failing scenario, and a one or two sentence fix. Where you can, add a failing test under `tests/test_codex_*.py` that pins the bug (new files only). Tests must run without model calls.

### B. A safety test suite for the deterministic tiers (new files only)
`tests/test_safety_*.py` with tiny in-memory SQLite fixtures using the schema in `shadow/db.py`. Pin at least: matcher never matches an entry posted after close; many-to-one only with references in the bank description or a remittance document; a payee with a bank-change document on file is held, including the second payment in the same period; duplicate payment detection without matching refs; an item inside an ignorance band escalates with reason `in_band`; a rule with an unanswered open question does not execute; an empty patch from an interview answer never approves a rule.

### C. An honest second BenchRec number (new file)
`benchrec/matcher.py` is a separate implementation tuned on BenchRec train. Write `benchrec/run_shipped.py` that adapts BenchRec rows to the shapes `shadow/matcher.py` expects and reports what the matcher we actually ship does on it, with both precision definitions from `benchrec/scoring.py` (pair-level and strict). It may well be worse. We want the true number. Dataset download is in `README.md`; never let matching code see the solution file.

### D. The SDK path (coordinate first)
Every run so far used the `claude` CLI fallback; the Anthropic SDK path in `shadow/llm.py` has never executed. Known suspect: the JSON schemas used for structured output (`RULE_SCHEMA`, `PATCH_SCHEMA`, `TURN_SCHEMA`) contain free-form objects without `additionalProperties: false`, and several `json.loads(reply.text)` calls are unguarded. The build session has this on its list, so comment on the tracking PR or ask Sam before starting. If you take it, verify with one cheap call only.

### E. Fresh-clone reproducibility check
From your clean clone, follow `docs/RUNBOOK.md` literally up to (not including) anything needing `keys/` or model calls. Report every step that fails or is ambiguous as a docs PR. A teammate has to do exactly this under time pressure after the freeze.

### F. Pitch material (new files)
`docs/PITCH.md`: a 3 minute script built around these beats, in this order. (1) Cold open: "What do you do about $12.40? Trick question. It depends who you work for." (2) Split screen, same transaction, two clients, two answers, cited precedents. (3) A judge plays controller: one yes/no band question, the band narrows on screen, the next case resolves their way. (4) Undo a bad correction: rule reverts, "N items checked, M re-opened." (5) Vendor bank-change email lands in review, never auto-paid. (6) Results: wrong-match rate over matches made, questions to trust, cost per run falling as rules graduate out of the LLM tier, BenchRec with both precision definitions. (7) Where it fails. Also `docs/JUDGE_QA.md`: the 15 hardest questions a finance-literate judge would ask, with honest answers. Known soft spots to address head on: zero-shot is nearly as accurate as the playbook on ordinary exceptions (the playbook's measured wins are routing to the right person, cost, and wrong-match rate); controller sign-off in the experiment is simulated by an LLM that holds the true policy; all client data is synthetic; BenchRec validates only the matcher tier; statistical drift detection is not built.

## 5. Ideas wanted

Separately from the tasks, write `docs/IDEAS_CODEX.md`. We suspect many teams in this track asked an LLM the same question and converged on "learn from history plus human in the loop." Give us your own thinking, not a survey. Constraints: must sharpen the single $12.40 story rather than widen scope, must respect the cut list, must be buildable by one agent in under three hours or be pure pitch. For each idea: one paragraph on what it is, why a Maximor judge (they sell reconciliation and close automation to mid-market finance teams; their stated pain is that every client is different) would care, the cheapest demo of it, and what could make it a bad idea. Specifically useful:

- A sharper or more defensible "measure of better" than the ones in section 4F.
- Failure modes of ignorance bands we have not thought of (multi-dimensional conditions, categorical rules, adversarial precedents, seasonality) and the cheapest mitigation.
- What a real controller would refuse to trust about this system, and what artefact would change their mind (we are considering rendering the playbook as a signable one-page policy memo, on the theory that auditors ask for documented reconciliation and write-off policies and many mid-size companies lack them; tell us if that theory is wrong).
- Ways the evaluation could still be fooling us.
- Anything in the codebase that would embarrass us if a judge opened the repo.

Disagree with us where you think we are wrong. That is the main reason we are asking a different model.

## 6. Orientation

```
shadow/      the agent: db, matcher (tier 0), rules (tier 1, compiled playbook), playbook (induction), history,
             investigator (tier 2, LLM + tools), guardrails, pipeline, correct, unlearn, stale, experiment, llm, server
sim/         simulator and ground truth (off limits to shadow/): world, client_a, client_b, enact (truth -> messy ERP trail),
             reviewer (LLM controller that holds the true policy), build, POLICIES.md
grade.py     standalone grader        experiments.py  headline experiment      curve.py  questions-to-trust curve
benchrec/    separate matcher + scorer for the real Kaggle dataset
ui/          static HTML/CSS/JS, served by FastAPI at :8787     docs/  API.md, RUNBOOK.md, TRUTH_LANE.md
data/<c>/    client.db (agent-visible), truth.db (not), playbook/<track>/vN.json, corrections.jsonl
keys/        hidden answer keys. Do not open.
```

Python 3.13+, `uv`, SQLite, FastAPI, no build step for the UI. `uv run pytest -q` is free and fast; keep it green.

---

# Appendix: the original project brief (verbatim)

This is the document the project started from. Scope, judging criteria, what Maximor told us in person, the cut list, and the suggested build order all come from here.

### HackMIT 2026 — Maximor Track: Project Brief

You are helping a team build a hackathon project in ~24 hours (realistically 8–12 hours of build time). Read this whole brief before writing code. Priorities: a working end-to-end demo, honest measured numbers, and depth in ONE workflow. Do not add scope that is on the cut list.

#### 1. The track

**Maximor — "Agentic Systems for the Office of the CFO."** Prizes: $4,000 / $2,000 / $1,000. Top 5 teams get fast-tracked interviews.

Judging: ambition and creativity, technical difficulty, closeness to the frontier of agentic systems, and the demo. They look for:

- Multi-step reasoning over real documents and data
- Coordination or memory that changes what the system does next
- Consistency (the same transaction means the same thing everywhere)
- Human review when the system is uncertain
- No fixed benchmark: "show us your own measure of better"

Explicitly NOT enough: a one-shot chatbot, an extraction pipeline, a hard-coded accounting workflow, or a personal-finance app.

Same project can also be submitted to:

- **Ramp** ("save time, save money")
- **The Token Company** (most creative LLM cost savings inside the product, $500)

#### 2. What Maximor told us in person

1. Their biggest problem: **every client has a different system**, and it is hard to build something consistent across them.
2. Context used to be a problem, but frontier models are better now. Do not pitch context handling as the innovation.
3. **Go deep in one area** rather than broad.

#### 3. Key insights that shape the design

- Reconciliation *matching* is table stakes. BenchRec (the real dataset Maximor links) is overwhelmingly one-to-one, and plain deterministic matchers already do well on it. A better matcher impresses nobody.
- Schema/column mapping is also easy for frontier models. If the wow moment is "it figured out the CSV," we have built an extraction pipeline, which is disqualifying.
- The real difference between clients is **policy and convention**, not schema: how each finance team resolves exceptions.
- The hard, valuable part is the **residue of exceptions**: the payment covering several invoices, the unexplained $12.40, conflicting evidence, and knowing when to escalate.
- Synthetic data we build ourselves means grading our own homework. We need a blind-built test set and at least one number from real external data.

#### 4. The idea: Shadow Onboarding

**One workflow, deep:** bank-to-ledger reconciliation plus exception resolution.

**Core concept:** the agent gets no per-client configuration. It reads how the client's humans resolved past exceptions and writes that client's **playbook**: a human-readable set of rules, each citing the precedents behind it. Example rules:

- "Bank fees under $25 go to account 6110 with no review."
- "Customer X always takes a 2% early-pay discount, so don't flag the short-pay."
- "Any vendor bank-detail change goes to the controller."

A controller reviews and signs off the playbook once, instead of reviewing every transaction. This directly targets Maximor's "every client is different" pain.

##### Pipeline

1. **Deterministic matcher** clears the easy ~90% with zero model calls. ($0 cost; this is the Token Company story.)
2. **Investigator agent** works the residue using the playbook plus tools: remittance emails, invoices, prior periods, ledger queries.
3. **Escalation** when precedents are thin or conflict. Every human correction becomes a **visible diff to the playbook**.
4. **Evidence chain** on every resolution: what was matched, which rule/precedent applied, which documents support it.

##### Two clients with opposite policies

- **Client A — vending/laundromat roll-up.** Cash-heavy, thousands of tiny transactions, batch deposits, card processor payouts net of fees, driver cash counts vs machine telemetry, sloppy chart of accounts. Lenient policies (e.g., small differences written off).
- **Client B — invented frontier AI lab (e.g., "Meridian AI").** Consumer subscriptions via Stripe-style batched payouts net of fees/refunds/chargebacks; enterprise prepaid commits; single wires covering several invoices; strict policies (e.g., investigate every difference, controller approval on vendor changes). Use plausible synthetic numbers. Do NOT name or imply a real lab.

The lab is a skin for Client B. It should cost hours, not a teammate's whole night. Stay inside bank reconciliation: no GPU depreciation, no training-vs-inference cost allocation, no GPU-hours-vs-invoice reconciliation.

##### The experiment that proves it works

Give both clients the **identical ambiguous transaction**. The agent should write it off for Client A and open an investigation for Client B, each time citing that client's own history. This shows the system learned the client rather than falling back on generic model priors.

#### 5. Data and measurement

- Simulate only what reconciliation touches: bank feed, general ledger, invoices/remittances, a few emails, planted traps, and a hidden answer key.
- For each client: months 1–3 include the humans' resolutions (the training history). **Month 4 is the hidden test.**
- **One teammate builds the month-4 traps blind**, without showing the rest of the team or the agent's developers.
- Planted trap ideas: duplicate invoice, refund posted twice, one payment covering three invoices, wire net of bank fees, unexplained $12.40 difference, vendor "we changed our bank account" email (must escalate, never auto-pay), entries posted after period close, a driver whose cash shortages follow a pattern.

##### Metrics to report

Compare three conditions on month 4:

1. Zero-shot frontier model
2. Model + induced playbook
3. Model + playbook after human corrections

Report for each:

- Resolution accuracy vs the answer key
- **Escalation precision** (share of escalated items that really were ambiguous)
- Wrong-match rate (a wrong match is worse than leaving it for review)
- LLM cost per reconciliation run
- Share of volume cleared by the deterministic tier

##### Real external data

Run the matcher tier on **BenchRec** so at least one number comes from data we did not create.

- Kaggle: https://www.kaggle.com/datasets/benchmarkteam/benchrec-real-world-cash-reconciliation-dataset
- ~32k labelled bank statement lines from a production corporate cash ledger, CC BY 4.0.
- Their framing matches our escalation story: better to leave a transaction unmatched than match it wrong; required match precision ~99.8%. Optimize match rate subject to that precision.
- Note: BenchRec is mostly one-to-one with no many-to-one batch structure, so it validates the matcher tier only, not the exception reasoning.

#### 6. Demo (3 minutes)

1. Split screen: the same transaction goes to both clients and gets two different, correct resolutions with cited precedents.
2. One live human correction -> the playbook diff appears -> the next similar case resolves on its own.
3. Trace one ugly exception end to end (ingest -> match attempt -> investigation -> resolution/journal entry -> review -> evidence).
4. The vendor bank-change email lands in the human review queue instead of being auto-paid.
5. Results chart: the three conditions, plus the BenchRec number.
6. **Record a backup video.** Live runs fail on stage.

Be honest in the pitch about where it fails and escalates. Mention close/accruals/forecasting/compute accounting only as "what's next."

#### 7. Cut list (do NOT build)

- A third client / live onboarding of a held-out client
- Ledger-as-git
- Fraudster-vs-auditor self-play
- UI-only ERP / computer use
- Multi-agent finance team (AP clerk, FP&A, auditor, etc.)
- Cash forecasting, board pack, month-end close, accruals
- Acquisition / multi-entity onboarding
- Schema-mapping adapters as a headline feature (keep ingestion simple)
- Context graph as the pitch (evidence chain is just the audit trail)

#### 8. Build order

1. **Hour 0–1:** pull BenchRec and confirm it loads. Run the playbook idea past the Maximor mentor. Fix the canonical data model.
2. **Simulator + answer key** for Client A (months 1–4), then Client B. One person owns the simulator and grader for the whole event.
3. **Deterministic matcher** + grader harness. Get a baseline number on both clients and BenchRec.
4. **Playbook induction** from months 1–3 history (rules with cited precedents, human-readable, editable).
5. **Investigator agent** with tools over the ledger, bank feed, documents, and playbook. Escalation logic.
6. **Correction loop:** human correction -> playbook diff -> re-run.
7. **Minimal UI:** exception queue, evidence trace, playbook view with diffs, results chart.
8. **Same-transaction-two-clients experiment**, metrics across the three conditions, backup video.

Target: Client A reconciling end to end by roughly hour 10–12. Widen only after that works.

#### 9. Suggested technical shape (change freely)

- Python, SQLite (or Postgres) for the ledger/bank/doc stores, agent tools exposed as plain functions or MCP.
- Canonical records: `bank_line`, `ledger_entry`, `invoice`, `remittance`, `document`, `match`, `exception`, `resolution` (with `rule_id`, `precedent_ids`, `evidence_ids`, `confidence`), `playbook_rule` (text, conditions, cited precedents, status: proposed/approved), `correction`.
- Playbook stored as versioned structured data and rendered as readable text so diffs are easy to show.
- Grader is a standalone script: takes resolutions + hidden answer key, outputs the metrics above. The agent code must never read the answer key.
- Log token usage per run for the cost metric.
