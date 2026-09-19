# Controller preview build

This builds the policy approval preview and resolves the ten findings in REVIEW_CODEX.md. All prior expected-failure markers have been removed. The review remains a historical record of how the defects were reproduced.

## What works

A controller answers a numeric policy question, sees the exact policy diff and changed cases, then applies it. Previewing is read-only and costs no model calls. Applying checks that the playbook and supporting data are unchanged, writes the approved answer, and reconciles the selected period again with deterministic tiers only. Undo replays the surviving inputs, preserves approval and effective-date metadata, and rechecks complete periods so shared ledger claims remain visible.

The screen shows exception counts, remaining bank review items, adjustment accounts and amounts, and ledger entries consumed by bank matches. It explicitly says that changed decisions are not a correctness score.

## Run the isolated demo

From the repository root:

```sh
uv sync
uv run python demo_preview.py --port 8791 --data-dir work/policy-demo
```

Open `http://127.0.0.1:8791/playbook.html?track=preview_demo&period=2026-03`.

This uses only the existing two client concepts in an isolated directory. Policies and transactions are hand-authored illustrative fixtures, clearly labelled on screen. They are not induced policies or benchmark results. No API key, model call, simulator truth file or hidden test is needed. Existing demo data is preserved between restarts; choose another directory for a fresh rehearsal.

1. Select Lucky Quarter Holdings. Preview “No, $15.00 is handled as usual”. The $12.40 receipt changes from escalation to a matched adjustment. Its invoice becomes consumed by that receipt. Two vendor payments remain held because a bank-change request is unverified.
2. Inspect the proposed rule sentence, band and affected cases. Apply the policy. The period is rerun at $0 model cost.
3. Undo that answer. In the browser rehearsal, five previously automatic items were checked and the $12.40 receipt was reopened. Counts depend on how many prior runs your rehearsal contains.
4. Select Meridian AI. Its illustrative policy still sends every customer shortfall to the controller. Changing A does not change B.
5. For the denied-authority path, add `&role=bookkeeper` to the URL. An answer cannot grant approval.

## Safety changes

- Late-posted ledger entries cannot bypass tier 0 through a rule or investigator match.
- Multi-entry matches require references or a remittance; shared batch claims abstain before consuming entries.
- All payments after an unverified bank-change request remain held until a senior approval explicitly references that document. The investigator cannot override this flag.
- Unanswered questions, pending senior approval, unsupported bands and conflicting approved rules block automatic rule execution. Effective dates constrain execution.
- Failed correction checks cannot publish a patch. Missing or junior roles cannot approve through interview, conflict or band routes.
- Stored patches preserve approval restrictions and effective dates. Legacy patches without approval metadata become proposed on replay. Dependent edits with no surviving rule are skipped and reported.
- Retractions are idempotent and preserve previous precedent exclusions. Empty observed history stays empty. Blast-radius checks use complete periods, including indirect claim dependencies.
- Evidence checks detect document changes, bank edits, undone links, reversals and newly added ledger entries. Harmless ledger memo edits do not invalidate a match.

## Verification and limits

Run `uv run python -m sim.build` before the full suite if the normal development databases are absent, then `uv run pytest -q`. The new safety and preview tests also work independently without simulator data. Model calls are forbidden by their fixtures. Browser rehearsal covered preview, apply, updated sentence/band, deterministic rerun and undo with a reopened item. JavaScript syntax and git whitespace checks pass.

Use one loopback server worker. Roles are explicit local caller assertions, not authenticated identities. Preview tokens and mutation serialization are process-local. Conservative matching and overlap checks may reduce coverage; no new accuracy claim is made. The paid investigator SDK path and blind April evaluation were not run. Legacy evidence snapshots cannot prove changes they never recorded.

Latest integration includes the team's stage improvements through `b8e2bc1`: direct undo on the applied card, conflict outcomes and reviewer selection, correct handling of held answers, and the newest standing resolution across partial re-runs. Safety checks still reconcile complete periods before filtering returned items, so partial queue re-runs cannot hide competing ledger claims.
