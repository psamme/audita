# Truth Lane: handoff for the blind-test teammate

Paste this whole file into your Claude as the first message, along with: "Read this, then read the files it points to, then help me do the job it describes."

Repo: `git@github.com:psamme/audita.git` (private, ask Sam for collaborator access). The project pitch and cut list are in `README.md`. Read that first.

## 1. Your job in one paragraph

We are building a bank reconciliation agent that learns each client's exception-handling policy from that client's own history (months 1 to 3), then is tested on a hidden month 4. **You own the hidden test.** You write the month-4 traps, you hold the answer key, and you are the only person who runs the grader against it. Sam and his Claude sessions build the agent and must never see your traps or your key. If they do, our accuracy numbers mean nothing and the judges (who work in finance) will know it. The line in our pitch is "one of us built the test blind and only they ran the grader." Your job is to make that line true.

## 2. The firewall (non-negotiable)

You and your Claude:

- NEVER commit or push anything under `keys/`. It is gitignored. Keep it that way. Do not `git add -f` it.
- NEVER edit anything under `shadow/` (the agent), and never write or tune agent prompts. If you find an agent bug, describe the symptom to Sam, not the trap that exposed it.
- NEVER tell Sam, the team chat, or any shared doc what the traps are, which counterparties they involve, the amounts, or the seed. After judging is over you can show everything.
- When you report results, report **metrics and category-level counts only** ("3 of 5 escalation traps handled correctly"), never item-level detail about what the trap was.
- Do not paste this repo's `keys/` contents into any Claude session other than your own.

Sam's side has the mirror rule: agent code never reads `keys/`, `sim/`, or `sim/POLICIES.md`.

Things already burned, so avoid them: seeds **11 and 23**; and the Brightwater Group LLC short-pay (invoice 4,800.00, bank 4,787.60, short 12.40). That one is the live demo transaction, not a graded trap. Do not reuse it or a near-copy.

## 3. Finance in 60 seconds (skip if you know this)

A company has two records of its money: the **bank statement** (what actually moved) and the **ledger** (what the accountants say should have moved, and why). **Reconciliation** is proving every bank line is explained by ledger entries and vice versa. About 90% match trivially. The rest are **exceptions**: one wire paying three invoices, a processor payout net of fees, a customer paying $12.40 short, a duplicate refund, a "we changed our bank account" email (classic fraud). There is no universal right answer to an exception. A laundromat chain writes off anything small. An audited company investigates every dollar. That per-client policy is what the agent has to learn, and what your traps test. A confident wrong match is much worse than escalating to a human.

## 4. Setup

```sh
git clone git@github.com:psamme/audita.git
cd shadow-onboarding
uv sync
uv run python -m sim.build        # builds months 1-3 for both clients into data/A and data/B
```

Then read, in this order:

1. `sim/POLICIES.md` : the ground-truth policy for each client. **Your answer key must follow it.** Where it is silent, the right answer is what that team's temperament implies, and if that is unclear, `escalate`.
2. The docstring at the top of `sim/build.py` : how month 4 gets appended.
3. `sim/world.py` : the `World` API your traps use (`bank`, `ledger`, `invoice`, `doc`, `resolve`, `easy_pair`).
4. `sim/client_a.py` and `sim/client_b.py` : how the standard months are generated, so your traps look native (same counterparties, description formats, account codes).
5. `grade.py` : how resolutions are scored against a key.

**Always `git pull` before you start writing traps.** The simulator is being reshaped today so that history looks like a real ERP export (terse memos, missing links, a sloppy clerk). Function signatures may shift. Trust the code and docstrings over this file.

## 5. How to build month 4

Create `keys/build_m4.py` (gitignored, never pushed). Shape:

```python
from sim.build import build_test

def trap_something(w):
    # create the bank lines / ledger entries / invoices / emails for the situation
    # then call w.resolve(...) for EVERY item you created, with the correct handling per POLICIES.md
    ...

build_test("A", seed=<your secret seed>, traps=[trap_something, ...])
build_test("B", seed=<another secret seed>, traps=[...])
```

`build_test` restores the month 1-3 snapshot, generates a normal April with your seed, runs your traps on top, and writes `keys/A_2026-04.json` and `keys/B_2026-04.json`. April's data lands in `data/<client>/client.db`.

`w.resolve(item_kind, item_id, action, ledger_ids, adjustments, escalate_to, note, category, alternatives)`:

- `action` is one of `match | match_adjust | book | escalate | carry_forward`.
- `adjustments` look like `[{"account": "6110", "amount": 18.00}]` and must sum to (ledger amounts minus bank amount).
- `escalate_to` must be a role that exists for that client (A: `owner`, `ops_manager`. B: `controller`, `ar_lead`, `ap_lead`).
- Use `alternatives` when a second resolution is genuinely acceptable. Be fair: if a sharp human accountant could defend it, list it.
- Set `category` on every trap (for example `short_pay`, `fraud`, `duplicate`, `many_to_one`, `cutoff`) so you can report category-level results without revealing items.

An interim, machine-authored month-4 set may already exist on Sam's machine for pipeline debugging. It is labelled interim and is never reported. Yours is the headline set and replaces it.

## 6. What makes a good trap set

Aim for roughly 15 to 25 trap items per client on top of the standard April volume. Mix:

- **Policy-boundary cases.** Amounts just under and just over each threshold in `POLICIES.md` ($15, $20, $25, $50 for Client A). These test whether the agent learned the actual line, not a vibe.
- **Same situation, opposite answers.** A few situations that appear at both clients where A writes off and B escalates. This is the core claim of the project.
- **Must-escalate cases.** Vendor bank-detail change email followed by a payment that ties exactly (never auto-match). Duplicate payout or refund. Payee that does not match the vendor. Entry posted into a closed period.
- **Thin-precedent cases.** Something the client has seen once or never. Correct answer is usually escalate. This measures escalation precision.
- **Conflicting evidence.** Remittance says one thing, amount implies another.
- **Structural cases.** One wire covering several invoices with a remittance advice. Processor payout net of fees, refunds, chargebacks. Several cash count sheets on one deposit slip. Wire short by sender bank charges, with and without evidence.
- **Pattern cases.** Client A's driver rule (third short over $10 by the same driver in a month goes to the owner regardless of amount).
- **A few decoys.** Things that look alarming but are routine under that client's policy, so an agent that escalates everything gets punished on escalation precision.

Rules for fairness: every trap must be resolvable (or correctly escalatable) from information the agent can actually reach: bank lines, ledger, invoices, documents, and months 1-3 history. No trap should depend on knowledge that exists only in `POLICIES.md`. If the history does not contain enough precedent to learn a rule, the fair key answer is `escalate`. Make traps look native: reuse real counterparties and description formats from months 1-3, but invent some new counterparties too.

Do not tune traps to make the agent look good or bad. Write them, freeze them, and do not change them after you see the first graded run. If you find a genuine error in your key, fix it and tell Sam "key corrected, N items" with no detail.

## 7. Freeze, then reveal (the evaluation protocol)

The protection is ordering, not trust: **the agent code is frozen before month 4 exists anywhere near it, and the hidden test is run once.**

1. You write and freeze your traps on your own machine while the agent is still being built. Nobody else sees them.
2. When Sam's side is done, Sam's control-panel session tags the repo (`git tag freeze-1`) and tells you. After that tag, no change to `shadow/` counts toward the headline numbers.
3. You `git pull`, `git checkout freeze-1`, run `uv run python -m sim.build`, then your `keys/build_m4.py`. Leak check before anything else: April must have left no answers in the agent-visible database.
   ```sh
   for c in A B; do sqlite3 data/$c/client.db "SELECT COUNT(*) FROM reconcile_link WHERE period='2026-04'; SELECT COUNT(*) FROM journal_entry WHERE period='2026-04'; SELECT COUNT(*) FROM approval WHERE period='2026-04';"; done
   ```
   Every number must be `0`, and `sqlite3 data/A/client.db .tables` must not list `resolution` (ground truth lives in `truth.db`, which the agent cannot open). If anything is off, stop and tell Sam "the sim leaks truth into client.db" with no detail about your traps.
4. **You run the headline experiment on your machine**, because the "after human corrections" condition uses a simulated reviewer that needs the key:
   ```sh
   uv run python experiments.py          # both clients, all conditions; prints aggregates only
   ```
   This needs a model backend: either `ANTHROPIC_API_KEY` in `.env`, or the `claude` CLI logged in (it is used automatically when no key is present). Expect roughly 30 to 60 minutes and real model spend; zero-shot is the expensive condition. It runs: zero-shot, history-only, playbook, and corrected. Corrections are made on April 1 to 15 and every condition is also scored on April 16 to 30, which no human touched.
5. You send Sam `runs/results.json` plus the category-level counts. Do not send `grades*.json` (item-level key data) or anything under `keys/`.
6. One run. If something crashes, fix the crash and re-run, but do not let anyone tune the agent against month-4 results. If the agent must change after seeing results, that needs a new tag and you should say so in the failures slide.

Fallback if your machine cannot run it: after the freeze tag exists, send Sam your two key files and `build_m4.py`, he drops them in `keys/` and runs `experiments.py` without opening them. This is acceptable only because the code is already frozen. Say in the pitch which way it was done.

## 8. Your other deliverables

- **BenchRec number.** `benchrec/` has a deterministic matcher and scorer for the real-world Kaggle dataset. Download instructions are in `README.md`. Run `benchrec/run.py`, confirm the match rate at roughly 99.8% precision, and sanity check that the scorer is honest (it must never look at the solution file while matching). This is the one number that comes from data we did not create.
- **The honest-failures slide.** After grading, write 3 to 5 bullets on where the agent fails or over-escalates, at category level. Judges trust teams that show this.
- **Be the finance skeptic.** If something in the sim looks unrealistic to you, say so early.

## 9. Timeline

- First hour: setup, read the five files, run `sim.build`, skim months 1-3 in `data/A/client.db` so your traps look native.
- Next 3 hours: write and freeze traps for Client A, then Client B. Build both locally and run the leak check. BenchRec sanity check in the gaps.
- Then wait for the `freeze-1` tag. When it lands: pull, rebuild month 4 at that tag, run `experiments.py`, send results.
- Last 2 hours: failures slide, final numbers to Sam.

The threshold-boundary traps matter most. On ordinary exceptions a strong model with no playbook already scores about as well as one with a playbook, so the only place "it learned this client" can show up in the numbers is where the right answer depends on a line the model cannot guess (this client's dollar limits, who gets which escalation, which customer has special terms).

## 10. Instructions to the teammate's Claude

You are working for the blind-test owner. Follow section 2 strictly. Do not read or modify `shadow/`. Do not run `git add` on anything under `keys/`, and check `git status` before any commit your user asks for. Prefer small, realistic traps that follow `sim/POLICIES.md` exactly, and show your user the policy line that justifies each key answer so they can verify it. If a policy is ambiguous for a trap, either add an entry to `alternatives` or make the key answer `escalate`. Never push. Sam's side owns git for the shared repo; if your user wants something shared (for example a fix to `sim/` or `grade.py`), produce a patch or a branch and let them coordinate with Sam.
