# What I would build and pitch

Keep Shadow Onboarding. The sponsor's supplied brief explicitly says one process done deeply is a strong entry. Your strongest claim is: **learn where this client's automation must stop, let its controller change that boundary, and make the change reversible.** The $12.40 story makes that legible. Another workflow would dilute it.

This is a product recommendation based on the supplied track brief and inspected code, not a claim that the implementation already satisfies every control. See REVIEW_CODEX.md for the gaps.

## Priority 1: a policy approval preview, before the controller clicks

When the controller answers the $12.40 question, show the smallest exact policy diff and the development cases it would change: “3 become automatic; 2 still need review; none change vendor.” Then commit the answer. Maximor can see an onboarding decision with a bounded consequence rather than a chat message that silently edits everything. Cheapest demo: a new deterministic report over a copied playbook and a frozen set of dev fixtures, using existing rule execution; no UI redesign is required. Include IDs, before/after actions, accounts, and approval state. This is a bad idea if the counter silently excludes relevant cases, or if it is described as future safety: it is a preview over the stated fixture set only. Budget: 1-2 hours after replay fixes.

## Priority 2: make the controlled comparison test learned policy

Keep the transaction, model and tools fixed; change only the client's playbook/history, with neutral client labels. Then remove the relevant precedent or signed answer and show the agent abstain. Maximor cares whether client memory actually changes behavior, not whether a model knows that an AI lab sounds strict. Cheapest demo: offline copies of the same fixture, with original, swapped and removed policy context, all labelled as development interventions. This could be a bad idea if the swap also changes accounts or evidence validity; normalize those identifiers and distinguish a causal toy test from a real customer evaluation. Budget: 1-2 hours plus any separately approved model calls.

## Priority 3: measure how much review an answer removes

Use a small table with “0, 1, 2, ... answers”, automatic bank exceptions / all bank exceptions, wrong automatic decisions / automatic decisions, remaining review items, and model calls. Keep ordinary exact matches out of the exception denominator. Count incorrect bookings and write-offs as well as wrong matches. Maximor can see onboarding effort and resulting workload directly. Cheapest demo: extend the existing curve output with explicit counts and a fixed, disclosed denominator. Keep March labelled development; choosing questions from March outcomes means March is no longer an unbiased final test. Freeze the procedure before blind evaluation. This is a bad idea if the graph is called “trust” while hiding errors, tiny sample sizes or the simulated controller. Budget: under one hour for counts and copy.

## Priority 4: a one-page decision and approval record

I like the memo idea as a review artifact. Include policy scope, effective date, exact executable predicate, account/action, supporting precedents, conflicts, unresolved questions, named approval status, and playbook version. Put the $12.40 decision and its evidence beside it. Maximor gets something a controller can inspect and challenge. Cheapest demo: generated Markdown or HTML from the stored rule, marked DRAFT until approval; never simulate a real person's signature. Do not claim an auditor will accept it or that mid-market firms generally lack these policies without customer evidence. A attractive memo that disagrees with executable conditions would reduce trust. Budget: 1-2 hours, no model required.

## Ignorance bands: narrower claims and cheap mitigations

A band brackets observed actions under assumed comparable circumstances. It is not a statistical confidence interval and does not prove an unseen point inside the apparent safe region is safe. A $10 write-off approved last month may depend on vendor, age, payment channel or who approved it.

- Multi-dimensional conditions: learning each threshold independently does not establish that their joint corner is supported. Show nearby complete precedents and abstain outside their supported combination; do not add a multidimensional learner at the hackathon.
- Categorical rules: vendor or account changes have no numeric midpoint. Ask a scope question and preserve an explicit allowed set.
- Adversarial or mistaken precedents: label a conflict as “pattern disagreement”, not proven human misconduct. Show the competing evidence and require explicit disposition before broadening a rule.
- Seasonality and changing policy: state the training window and effective date. Do not claim statistical drift detection; use explicit policy-version changes and record edits as the supported cases.
- One precedent or none: show the count, preserve exclusions, and require sign-off before broadening autonomy. Retraction must not silently restore a written model threshold or replay approval that never occurred.
- Related bands: equal numbers do not mean the same policy. Only propagate an answer to a paired escalation rule when scope and policy identity also agree.

## Ways the evaluation can fool us

The zero-shot baseline needs the same model, tools, guardrails, records and item scope. Otherwise gains may come from access or safety scaffolding rather than memory. The all-volume auto-clearance rate can look excellent while the exception queue barely changes. Wrong-match rate misses bad standalone bookings. An abstain-on-everything model has no wrong matches but does no work, so always pair errors with coverage. A model that knows true policy is a simulated teacher, not evidence of human onboarding time. Historical replay is agreement with past decisions, not proof those decisions were correct. A blind trap author still shares the simulator's assumptions; blind synthetic tests are stronger than self-tuned synthetic tests but are not production validation. Report the shipped matcher separately from the BenchRec-specific matcher, using exact and pair-level metrics. None of those matcher results establishes exception-policy performance.

“Zero errors in N tested automatic decisions” is honest. “Zero risk” is not. Do not report an unrun blind test, a controller time-saving estimate, or these test fixtures as measured customer outcomes.

## What to do in the remaining build window

First merge and review the small safety fixes, then repair approval-preserving replay and bank-change holds. Rehearse the single story with a saved demo fixture and a backup recording. Spend remaining time on the approval preview or the memo, choosing one. Freeze before the blind evaluation. Keep forecasts, another client, schema mapping and a multi-agent finance team off the build list.

Source: [Maximor track brief](https://docs.google.com/document/d/1QAs_O_oTKl-PrKAKXdPxMO1AxKfbocy1EcL6PHkdwJk/edit?tab=t.0), read September 19, 2026; local CODEX_BRIEF, README, RUNBOOK and API contract.
