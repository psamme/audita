# Shadow Onboarding

HackMIT 2026, Maximor track: "Agentic Systems for the Office of the CFO."

Bank-to-ledger reconciliation where the agent gets **no per-client configuration**. It reads how a client's finance team resolved past exceptions, writes that client's **playbook** (human-readable rules, each citing the precedents behind it), and a controller signs off the playbook once instead of reviewing every transaction.

Every client has a different system. The real difference is not schema, it is policy and convention: how each team resolves exceptions. That is what this learns.

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

Months 1 to 3 carry the humans' resolutions (training history). **Month 4 is the hidden test**, with traps built blind by one teammate.

Three conditions on month 4:

1. Zero-shot frontier model
2. Model + induced playbook
3. Model + playbook after human corrections

Reported for each: resolution accuracy, escalation precision, wrong-match rate, LLM cost per run, share of volume cleared by the deterministic tier.

We also run a deterministic matcher on [BenchRec](https://www.kaggle.com/datasets/benchmarkteam/benchrec-real-world-cash-reconciliation-dataset) (~32k labelled lines from a production cash ledger, CC BY 4.0) so at least one number comes from data we did not create. That matcher (`benchrec/matcher.py`) is a separate implementation tuned on BenchRec's training split only; it never sees the solution file. We report both precision definitions: pair-level (a prediction counts if it is a subset of the labelled allocation) and strict (exact allocation).

## Setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone git@github.com:psamme/shadow-onboarding.git
cd shadow-onboarding
uv sync
cp .env.example .env   # then add your ANTHROPIC_API_KEY
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
- Commit small and often to `main`; pull with `git pull --rebase` before pushing.

## Cut list

Not building: a third client, ledger-as-git, self-play, computer-use ERP, multi-agent finance team, forecasting / close / accruals, multi-entity onboarding, schema mapping as a headline.
