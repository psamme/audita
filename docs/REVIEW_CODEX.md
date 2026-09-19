# Codex independent safety review

**Implementation follow-up:** all ten numbered findings below are now addressed by passing regression tests in the controller-preview build. See BUILD_CODEX.md and the API additions. The original scenarios and baseline line references below are retained as the historical audit record. No xfail markers remain in the safety suite.

Reviewed baseline `f28e411e9f1c6dd5d2a3c0f472aa57c573d48fd4`, September 19, 2026. Scope: deterministic matcher, rule execution, bands, correction/retraction and stale evidence. No hidden keys, item-level grades, simulator policies or client implementations were read. No model calls were made. Line references below identify the baseline, before this PR's small changes.

## Findings ranked by impact

### C10. P1: corrections that fail validation are still published (fixed)

`shadow/correct.py:181` and `:186-192`. A controller requests booking a $5 fee to `fees`; both mocked model responses propose account `wrong`. Both reproduction checks fail, but the code saves an approved rule anyway. The new test exercises the real validation and persistence path with only the model response mocked. Fix: return the unchanged playbook version and failed check when both attempts fail. This PR does so. The attempted correction remains in the input log, but does not become an executable policy.

### C6. P1: retraction replay can approve a junior's correction (open)

`shadow/unlearn.py:40`, `shadow/correct.py:78-85` and `:173-191`. Live correction processing changes a junior's automatic rule to proposed/awaiting senior. The stored patch only contains model ops; replay applies those ops through `_apply_ops`, which marks modified rules approved and human-confirmed. Retract another input and the junior's surviving patch can acquire authority it never had. The regression test demonstrates this at the replay boundary. Fix: persist validated rule state, teaching authority and effective date in each patch, and replay those semantics without inventing approval. Back-test restoration of human-confirmed status makes this especially important.

### C4. P1: the second payment escapes a bank-change request hold (open)

`shadow/guardrails.py:52-55`. A bank-change email names Acme. Payments of $100 and $200 follow on consecutive days, with no account tokens in the bank feed and exact ledger counterparts. Only the first payment is flagged; the second can auto-match. Different amounts avoid the duplicate detector. Fix: hold all affected payments until explicit verification clears the change, rather than treating observation of the first payment as clearance. A payment already arriving in the feed is not proof that someone approved the bank change.

### C1. P1: rules bypass the matcher's late-posting protection (fixed)

`shadow/rules.py:78-79`; `shadow/pipeline.py:43` and `:57`. The matcher refuses a March entry posted April 20, but the rule context contains the unfiltered open ledger. A counterparty rule can immediately match the same entry. Fix: apply `matcher.posted_late` to rule candidate selection as well. This PR does so, retaining late ledger records for separate review rather than deleting them from the entire context.

### C3. P1: many-to-one matching is order-dependent (open)

`shadow/matcher.py:103-120`. Two $100 receipts both cite invoices for $40 and $60. The first bank row consumes the only batch; the second is left over. Neither receipt has a mutually unique claim. Fix: collect batch candidates and all ledger claims before consuming any; abstain on cross-bank conflicts, as tier 0 already does for one-to-one matches. Merely avoiding duplicate ledger consumption does not prove the selected receipt is correct.

### C2. P1: anonymous batches can match with no corroborating evidence (open)

`shadow/matcher.py:61-62`. An anonymous $100 deposit and unrelated non-invoice entries of $40 and $60 auto-match solely because their sum is unique. This is an explicit exception in the implementation, but violates the requested safety contract and is not evidence of a relationship. Fix: require a batch manifest, references or another verified source linking the entries. Until available, abstain. Expect coverage to fall; that is not justification to retain unsupported matches.

### C8. P1: excluding every precedent reloads the excluded history (fixed)

`shadow/playbook.py:327`. Back-test passes an empty observed map after exclusions, but `observed or ...` treats it like a missing argument and reads all history again. Fix: default only when `observed is None`. This PR does so. This prevents silent reintroduction of excluded evidence; it is not a complete proof of zero-support rule safety.

### C7. P2: effective dates do not constrain execution (open)

`shadow/rules.py:193`, `shadow/playbook.py:343`, `shadow/correct.py:174`. `valid_from` filters band induction, but an approved rule effective March 20 still books a March 10 item. Replay also does not persist this date in the stored ops. Fix: define effective-date semantics explicitly, enforce them during execution, and include the date in patches and policy diffs. If the intended contract is only a training cutoff, rename the field and avoid claiming dated policy changes apply prospectively.

### C9. P2: document edits leave evidence marked current (open)

`shadow/stale.py:19` and `:38-40`. A matched item's remittance body changes to withdraw the instruction. Verification still reports no stale items because it checks document existence only. Fix: fingerprint decision-relevant document content and metadata as well as IDs. Compare journal reversals and link state when they are dependencies; those tables are not currently checked either.

### C5. P2: unanswered questions are not a runtime barrier (open)

`shadow/rules.py:303-310`. An executable approved rule with a nonempty `open_question` and `human_confirmed=False` still books a transaction. Normal induction usually demotes this state, so this is a defense-in-depth gap rather than proof that every ordinary question bypasses approval. Fix: make the runtime fail closed on unresolved questions and `awaiting_senior`, independent of a potentially inconsistent status field.

## Additional inspected risks, without regression tests in this PR

- `shadow/correct.py:134` treats an omitted role as senior. `answer` and `answer_band` accept no role, and the server forwards these endpoints without a teaching-authority check. A local demo may assume a trusted operator, but do not describe this as enforced role-based authorization. A junior can use interview routes unless the caller is restricted elsewhere. `resolve_conflict` likewise does not verify seniority before processing the outcome.
- `shadow/playbook.py:402-409` accepts arbitrary band values without checking finite numbers, interval membership or condition side. It also moves another rule's band based on numeric equality and dimension alone, without checking vendor or document scope. Equal thresholds at unrelated vendors are not evidence of one shared policy.
- `shadow/unlearn.py:48-49`: retracting the same correction twice raises rather than returning an idempotent result. It does not reapply the correction, but the operation is not idempotent as an API contract.
- `shadow/playbook.py:48`: diffs omit `valid_from`, approval provenance and open questions. This can hide meaningful changes and undercount affected rules in blast-radius checks. Bands themselves ARE included, so a changed band value is not inherently invisible to the diff.
- `shadow/stale.py:45` compares posting dates to the run timestamp truncated to a day. Date-only records cannot establish whether a same-day posting occurred before or after the run. Journal reversals and undone reconciliation links are not compared. New postings are returned separately rather than associated with affected decisions.
- `shadow/rules.py:300`: first matching rule wins, including conflicting approved rules. That is documented behavior, but a learned overlap should be explicitly reviewed rather than silently settled by model-generated order.

## Verification

Fresh clone: `uv sync`, `uv run python -m sim.build`, `uv run pytest -q` succeeded. Baseline: **20 passed**.

New fixtures use in-memory SQLite and temporary output paths. They do not need generated client databases. Any unmocked model call fails the test.

Before fixes: `uv run pytest -q tests/test_safety_codex.py --runxfail --tb=short` reproduced **10 failed, 14 passed**. After fixes on the original baseline, the full suite reported **37 passed, 7 xfailed**. The seven strict xfails document open defects, not successful safety checks. Run with `--runxfail` to expose them as failures; remove a marker when the corresponding fix lands. Strict markers make an unexpected pass require updating the review.

Passing coverage includes late-posting rejection, reference-supported invoice batches, duplicate payments without references, band edges, pipeline `reason=in_band`, proposed rules with questions, empty interview patches, and stale amount/date/account edits. The tests also pin the three fixes above.

## Fresh-clone documentation ambiguities

`docs/RUNBOOK.md` starts with hidden-month and paid commands in its build-order block; the safe development block should come first. Its demo script still directs users to `GET ...?fresh=true`, while the later API contract says GET is cache-only and POST runs the demo. README's instruction to commit to main conflicts with AGENTS/CODEX_BRIEF branch instructions; followed the latter. Python 3.14.6 was available and passed, satisfying the declared 3.13+ requirement.

No API fields were added or renamed. The correction endpoint can now return `diff: null`, the unchanged version and a failed `check` after exhausted retries; callers should show that failure instead of implying the model's explanatory text was accepted.

The existing learning tests append to tracked `data/A/corrections.jsonl`; those test-generated changes were restored in this isolated clone and are excluded from the PR. New safety tests keep their correction logs under the temporary fixture directory. The post-fix explicit failure run reports **7 failed, 17 passed**, matching the seven remaining strict xfails.

## Rebase verification

Rebased onto `d70af18`. Upstream added band role checks, stated limits, a categorical-fee heuristic and guards against widening conflicted rules. The baseline-only observation that `answer_band` lacks a role parameter is now superseded; omitted-role trust and ordinary interview authorization still need review. The seven open reproductions continue to fail as expected.

A separate clean worktree of unmodified `d70af18` produced **21 passed, 1 failed**: the existing retraction test expected widening despite the new conflict guard. This PR updates that test to tighten the band and verify both restored boundaries, preserving the guard. Final full suite: **39 passed, 7 xfailed**. No UI or other upstream behavior was reverted.
