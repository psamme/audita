# Plume submission text

Paste each block into the matching Plume field. Tick: General track, Maximor, Ramp, The Token Company, Long Lake.

## Inspiration

Maximor told us their hardest problem is that every client is different. We took that literally. The difference between two finance teams is rarely the schema. It is policy and convention: one team writes a $12.40 difference off, another opens an investigation, and nobody wrote either rule down. It lives in how past exceptions were resolved. So we built a reconciliation agent that gets no per-client configuration and has to learn the client from their own books.

## What it does

Audita reconciles bank activity to the ledger in five tiers, cheapest first, and every tier may refuse.

1. Guardrails. A payment to changed vendor bank details is held however exactly the amount ties.
2. A deterministic matcher that only matches when the answer is unique. No model, $0.
3. Playbook rules. The agent reads three months of the client's ERP trail (reconcile links, terse journal memos, approvals, some email) and induces a human readable playbook. Every rule cites the precedents behind it and carries a back-tested agreement rate. Thresholds are measured bands: applies, asks a person, does not apply. Inside the band where the client has never been seen to act, the agent does not act either. Rules compile to a small rule language and run with no model call.
4. An investigator agent with tools (ledger search, bank search, documents, invoices, precedents) works what is left and must ground every resolution in evidence.
5. A person. The controller answers one question, not a review queue. The answer becomes a versioned playbook diff, and it is replayed against the client's history before it is allowed to run. Under 60% agreement it does not execute, and the system asks whether this is a change of policy or a mistake in how it was written down.

Any answer can be undone. The playbook replays without it, with no model call, and re-opens exactly the past resolutions that no longer hold. Juniors cannot widen a limit.

An independent auditor agent then checks the preparer's work: control tests over the books, re-performance of a risk-weighted sample without seeing the preparer's reasoning, and consistency checks. Across both clients it re-performed 75 sampled items (24 of them blind, by a model that never saw the preparer's reasoning), agreed on all 75, ran 16 control and consistency tests per client, and still raised five real findings for $0.58, including a bug in our own preparer: it escalated to a controller at a client that has no controller. Every sampled item has an exportable audit file.

Two clients with opposite policies receive the identical ambiguous transaction. The agent writes it off for the lenient one and opens an investigation for the strict one, each time citing that client's own history.

## How we built it

Python, FastAPI, SQLite, and Claude as the investigator through a tool loop. The UI is plain HTML, CSS and JS in a black and white marble design system we made for it. A simulator writes each client's first three months as a deliberately messy ERP trail, with one inconsistent clerk and a few mistakes. Agent code cannot open the simulator's truth or the answer key, and a test enforces that. Everything runs on one laptop.

For real data we used BenchRec, 32,048 real bank lines labelled by a bank's own analysts. We induced conventions from 68,975 training lines, including 11,054 groups the analysts closed by hand, and graded on the held-out 32,048 lines with the answer key opened only by the grader. With thresholds learned on training data and no model calls we correctly match 90.5% of the 31,836 matchable bank lines at 99.87% pair-level precision (28,818 correct among 28,855 predictions, 37 wrong matches). The matcher shipped with the dataset reaches 65.0% at 99.45% (114 wrong). Six conventions were induced and the execution floor refused five of them on their own history. The one that runs is a measured tolerance: differences up to $15.29 are accepted when the reference ties, from 689 analyst precedents. The refused ones still reach a person as suggestions and match the key 88% of the time.

## Our measure of better

Silent wrong resolutions (resolved wrongly with no escalation) and questions to trust (how many human answers until the zero-cost tiers handle the exceptions right).

On the interim April set (231 items, single runs): at the lenient client, a zero-shot frontier model with tools scored 85.7% with 1.4% silent wrong for $11.99. The playbook as induced scored 87.1% with zero silent wrong for $8.57. After sign-off and corrections, 93.6% for $2.67. At the strict client: 93.4% / 2.2% / $5.72 zero-shot, then 98.9% / 0% / $3.03 after sign-off. As induced, the playbook is not more accurate than zero-shot. It is safer and cheaper, and we show the row where it scored lower.

On the development curve, four answers take the strict client from 31% to 90% of exceptions handled right at zero model cost, with zero silent wrong resolutions at all 37 points across both clients.

## Challenges we ran into

A mistranslated controller answer once became policy and mis-booked every card payout: 33 silent errors, with a back-test of 0 agreements and 109 disagreements that was ignored because a human had confirmed it. That bug is why the execution floor exists and why it applies to human answers too.

## What we are honest about

The two demo clients are synthetic; the real ledger is one account, so it does not show two clients differing, and no model was run on it. The controller in the experiments is simulated by a model holding the client's written policy. Runs are single, so a difference of one to three items is noise. Costs are an upper bound because the CLI backend writes a fresh prompt cache per call.

## What is next

Three repeats per condition with ranges, drift detection beyond the conflict path, and the rest of the close: accruals and flux, on the same playbook.


Benchmark scoring context: BenchRec cash v1.0 contains anonymised production bank and general-ledger transactions. The evaluation contains 32,048 bank lines, 31,836 with labelled ledger matches. Pair-level correctness means every attached ledger entry belongs to the labelled group; partial groups can count as correct. Requiring the exact complete group yields 96.50% precision and 87.46% coverage. We use our own scorer, applied equally to the supplied reference baseline, not an official leaderboard score. Training and evaluation dates overlap; this is a held-out transaction split, not a future-period test. These results evaluate the separate BenchRec implementation, not the company upload workflow. Dataset context: https://www.operartis.com/benchrec


## Submission links

Public recorded interactive demo and benchmark: https://audita-hackmit.vercel.app

Public source code: https://github.com/psamme/shadow-onboarding

The public site replays measured approval and undo execution with synthetic sample companies, then explains the separate real-data BenchRec result. The local application runs live approval, policy changes and undo.
