# Runbook

Everything runs from the repo root with `uv run`. No API key is needed on a machine that is logged in to Claude Code:
`shadow/llm.py` uses the Anthropic SDK when `ANTHROPIC_API_KEY` (or an `ant auth login` profile) is present and falls
back to the local `claude` CLI otherwise. Put a key in `.env` to use the SDK. `SHADOW_MODEL` (default `claude-opus-5`)
and `SHADOW_EFFORT` (default `medium`) are read from the environment.

## Safe development first

For a free fresh-clone check, run `uv sync`, `uv run python -m sim.build`, then `uv run pytest -q`. Use the March development holdout. Stop before hidden-test scripts and paid experiment commands unless the freeze protocol and model budget authorize them. For the isolated controller preview, follow `docs/BUILD_CODEX.md`.

## Full experiment build order (includes hidden-test and paid steps)

```bash
uv run python -m sim.build                 # months 1-3 for both clients: ERP trail, documents, truth.db, March dev key
uv run python keys/<month4 script>.py      # whoever owns the hidden test appends April and writes keys/*_2026-04.json
uv run python experiments.py --label blind # induce, zero_shot / playbook / corrected, grade, write runs/results.json
uv run python -m shadow.experiment         # same transaction, two clients (writes runs/experiment.json)
uv run python -m benchrec.run              # BenchRec matcher number (runs/benchrec_results.json)
uv run uvicorn shadow.server:app --port 8787
uv run pytest -q
```

`keys/interim_m4.py` is an agent-built validation month 4 that nobody on the agent side has read. Label anything
produced from it `interim`. The teammate's blind script replaces it; it uses the same interface
(`sim.build.build_test(client, seed, traps=[...])`, World API in `sim/world.py`, truth policy in `sim/POLICIES.md`).

Note that `build_test` restores `client.db` from the month 1-3 snapshot, so re-running a month-4 script also removes
the demo transaction; `shadow.experiment` puts it back.

## Developing without touching the hidden month

```bash
uv run python -c "from shadow import playbook; playbook.induce('A', '2026-03', track='dev')"   # learn from Jan-Feb
uv run python -m shadow.pipeline A 2026-03 --condition playbook --track dev --run-id dev_A     # reconcile March
uv run python grade.py runs/dev_A --key runs/dev/key_A_2026-03.json                            # grade vs truth
uv run python -m shadow.pipeline A 2026-03 --track dev --no-llm                                # free tiers only
```

## What is where

| Path | What |
|---|---|
| `shadow/onboard/classify.py` | what each dropped file is, from its columns; the file name only breaks ties |
| `shadow/matcher.py` | tier 0, deterministic, abstains unless a match is mutually unique |
| `shadow/guardrails.py` | controls no playbook can switch off (changed payee bank details, duplicate payments) |
| `shadow/rules.py` | tier 1, the rule language and its executor; abstains when two items claim one entry |
| `shadow/history.py` | reads the ERP trail (links, adjustment entries, who, when, approvals) into observed outcomes |
| `shadow/playbook.py` | induction from the trail, back-test, versions, diffs |
| `shadow/investigator.py` | tier 2, tool-using model on the residue; code enforces arithmetic, controls, confidence floor |
| `shadow/correct.py` | correction or controller answer -> playbook patch -> code check -> back-test -> new version |
| `shadow/pipeline.py` | one run; writes `runs/<id>/resolutions.jsonl` and `run.json` |
| `grade.py` | standalone grader; agent code never reads a key |
| `sim/` | simulator, ground-truth policy, mess generator, simulated reviewer and controller |
| `docs/API.md` | UI data contract |

Isolation is tested: `tests/test_core.py` fails if anything under `shadow/` mentions the truth DB, the keys folder or
the policy file, or imports from `sim` (the demo fixture is the single exception).

## Demo script (3 minutes)

1. `POST /api/experiment/run` with `{"which":"same_transaction"}`: the same $4,787.60 receipt against a $4,800.00 invoice at both clients. B sends it to
   the AR lead and cites the small shorts its team never wrote off. A, before sign-off, asks the owner one question
   (its history only shows write-offs up to about $12-14); answer it in the playbook screen and re-run: A now writes
   $12.40 off to 6990 and B still escalates.
2. Queue: correct one escalated item, watch the playbook diff appear, see `reran` clear its siblings at $0.
3. Open any investigator item: case file -> tool calls -> code checks -> resolution with evidence ids.
4. The changed-bank-details payment: amount ties exactly, control flag holds it for the controller anyway.
5. Results: three conditions per client, tiers and cost per run, BenchRec number.

## Honest limits to say out loud

- A frontier model with tools and no playbook is already accurate on in-distribution items (March holdout: 94% at
  client A, 99% at client B). The playbook's measured advantage there is cost (about 5x fewer model calls), fewer
  silent errors, and handling lenient conventions a cautious model would escalate. Conventions that contradict
  professional priors are where learning the client matters most.
- The processor and count-sheet documents are structured. Emails are free text.
- Escalation precision is moderate by design: with two or three months of history many thresholds are guesses, and
  the system asks instead of guessing. The interview step is what removes those escalations.
- BenchRec validates the matcher tier only; it has no many-to-one batch structure or exception reasoning.
- Costs reported under the CLI backend include a fresh prompt-cache write on every call; the SDK backend with a
  shared cached system prompt is cheaper per item.

## Sunday additions

`runs/` is local output, so these pages need their files generated once after a fresh clone:

```sh
uv run python -m benchrec.real                                      # real ledger (needs data/benchrec), about 35 s, $0
uv run python -m shadow.auditor --client A --run runs/A_2026-04_corrected   # auditor, about $0.29 per client
uv run python -m shadow.auditor --client B --run runs/B_2026-04_corrected
uv run python -m shadow.close A 2026-04                             # close checklist, $0, also served live
```

Pages: `/close.html`, `/audit.html`, `/real.html`. For the stage, run the server without `--reload`.
