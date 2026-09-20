# Audita

**HackMIT demo:** [Open the live review queue](https://audita-hackmit.vercel.app) · [Three-minute presentation guide](docs/PRESENTATION.md).

For the live queue, run `uv run python demo_stage.py --data-dir work/judging-submission --port 8795` and open `http://127.0.0.1:8795/`. The Vercel site runs the same review queue against isolated sample workspaces. Its live API is served from the judging laptop; keep it awake and online. A standalone recording remains available under More → Recorded fallback.


HackMIT 2026, Maximor track: "Agentic Systems for the Office of the CFO."

Bank-to-ledger reconciliation where the agent gets **no per-client configuration**. It reads how a client's finance team resolved past exceptions, writes that client's **playbook** (human-readable rules, each citing the precedents behind it), and a controller signs off the playbook once instead of reviewing every transaction.

Every client has a different system. The real difference is not schema, it is policy and convention: how each team resolves exceptions. That is what this learns.

## Company workspace

[Set up your company on the public site](https://audita-hackmit.vercel.app/company/index.html?track=main). Andrew's full onboarding flow is available alongside the sample review queue, with a separate workspace per visitor.

Start `uv run uvicorn shadow.app:app --host 127.0.0.1 --port 8787` and open http://127.0.0.1:8787/.
Create your company, upload historical decisions, learn its policies, then upload new receipt CSVs with no decisions attached. Shadow reconciles supported cases and sends unresolved items to your review queue. The workspace includes CSV templates and a labelled example pack. See [the company guide](docs/COMPANY_WORKSPACE.md).

The isolated hackathon server uses `uv run python demo_stage.py --data-dir work/judging-final --port 8795`. The original sample demo remains available through the navigation.

## How it works

1. **Deterministic matcher** clears the easy lines with zero model calls ($0). On our simulated clients that is about half of volume; compiled playbook rules clear roughly another third, also with no model calls.
2. **Investigator agent** works the residue using the playbook plus tools: remittance emails, invoices, prior periods, ledger queries.
3. **Escalation** when precedents are thin or conflict. Every human correction becomes a visible diff to the playbook.
4. **Evidence chain** on every resolution: what matched, which rule or precedent applied, which documents support it.

## The experiment

Two synthetic clients with opposite policies:

| | Client A | Client B |
|---|---|---|
| Business | Vending / laundromat roll-up | Invented frontier AI lab |
| Shape | Cash-heavy, tiny transactions, batch deposits, processor payouts net of fees | Batched subscription payouts, enterprise prepaid commits, single wires covering several invoices |
| Policy | Lenient (small differences written off) | Strict (investigate every difference, controller approval on vendor changes) |

Both clients receive the **identical ambiguous transaction**. The agent should write it off for A and open an investigation for B, each time citing that client's own history.

## Measurement

Months 1 to 3 carry the humans' resolutions (training history). **Month 4 is the hidden test.** The interim month 4 was built by a separate agent; the agent code cannot open its key or the simulator's truth, and a test enforces that. The headline that nobody on this team wrote comes from real data: see BenchRec below.

Three conditions on month 4:

1. Zero-shot frontier model
2. Model + induced playbook
3. Model + playbook after human corrections

Reported for each: resolution accuracy, escalation precision, wrong-match rate, LLM cost per run, share of volume cleared by the deterministic tier.

We also run a deterministic matcher on [BenchRec](https://www.kaggle.com/datasets/benchmarkteam/benchrec-real-world-cash-reconciliation-dataset) (~32k labelled lines from a production cash ledger, CC BY 4.0) so the headline comes from data we did not create. It is also a first-class client in the product (`uv run python -m benchrec.real`, page `real.html`): conventions are induced from the analysts' own resolutions in the train split and must pass the same execution floor before they run. That matcher (`benchrec/matcher.py`) is a separate implementation tuned on BenchRec's training split only; it never sees the solution file. We report both precision definitions: pair-level (a prediction counts if it is a subset of the labelled allocation) and strict (exact allocation).

## Model provider

New policy learning, exception investigation and audit calls use OpenAI Responses (`gpt-5.2`, medium reasoning). Add `OPENAI_API_KEY` to the ignored `.env` file and restart the server. Jev / TypeSafe remains a separate reviewer-suggestion integration. Missing OpenAI credentials produce a clear error; there is no silent Claude fallback. The prepared stage policies and recorded demo were generated before this migration with Claude and have not been regenerated or re-evaluated with OpenAI. Deterministic matching, policy previews, approvals, undo and BenchRec need no OpenAI calls.

Legacy backends require explicit `SHADOW_BACKEND=sdk` or `cli` and a matching `SHADOW_MODEL`.

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone git@github.com:psamme/audita.git
cd audita
uv sync
cp .env.example .env   # then add your OPENAI_API_KEY
```

BenchRec is not committed (116 MB). Pull it with:

```sh
mkdir -p data/benchrec && cd data/benchrec
curl -L -o benchrec.zip "https://www.kaggle.com/api/v1/datasets/download/benchmarkteam/benchrec-real-world-cash-reconciliation-dataset"
unzip -o benchrec.zip && rm benchrec.zip
```

## Rules of the repo

- **The agent code must never read the answer key.** Month-4 keys live in `keys/`, which is gitignored. The grader is a standalone script that takes resolutions plus the key and outputs metrics.
- Secrets go in `.env`, never in code.
- `runs/` and `*.db` are local output, not committed.
- Work in an isolated clone on a `codex/<topic>` branch and open a PR. Do not push to `main`.

## Cut list

Not building: ledger-as-git, self-play, computer-use ERP, forecasting and accruals, multi-entity onboarding, schema mapping as a headline.

Added on Sunday: an independent auditor agent with an exportable audit file (`shadow/auditor.py`, `audit.html`), a month-end close checklist (`shadow/close.py`, `close.html`), and the real ledger as a client (`benchrec/real.py`, `real.html`).

## Controller approval preview

See [the build and rehearsal guide](docs/BUILD_CODEX.md) for the no-model, isolated preview demo and safety fixes.

## Judging demo

Run `uv run python demo_stage.py --port 8795` and open `/demo.html?track=stage` for the learned-policy flow. Read [the three-minute script and backup instructions](docs/JUDGING_DEMO.md). It uses saved model induction, shows historical evidence, groups policy questions, and demonstrates approval plus selective undo without model calls on stage.
