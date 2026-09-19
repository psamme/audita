# Results notes

For whoever builds the slides. Everything here is measured; where a number is weak or borrowed, it says so.
Headline numbers come only from the blind month 4, run once at the tag `freeze-1` (`uv run python experiments.py --label blind`).
That run writes `test_set: "blind"` and `code_commit` into `runs/results.json`. Until it exists, the numbers below are
from a development holdout (March) and from an interim month 4 that an agent built. Neither is a headline.

## The one-line claim

It asks before it acts, then it is right.

As induced, with no human input, the playbook is not more accurate than a frontier model with tools. It is safer and
cheaper. The accuracy arrives after a controller answers its questions, and it arrives with fewer silent errors and
a fraction of the model cost.

## Interim month 4, April 16-30, like for like

Agent-built validation set, single runs, CLI backend, claude-opus-5 at medium effort. Produced before three late
fixes (see "Two holes" below), so the frozen code should show fewer silent wrong resolutions and somewhat more escalations.

| | Accuracy | Silent wrong | Escalation precision | Review load | Cost | Model calls |
|---|---|---|---|---|---|---|
| A zero-shot | 85.7% | 1.4% | 0.33 | 43 | $11.99 | 224 |
| A history only | 87.1% | 0.7% | 0.37 | 41 | $12.31 | 183 |
| A playbook as induced | 87.1% | 0% | 0.38 | 46 | $8.57 | 124 |
| A after sign-off and corrections | 93.6% | 0.7% | 0.58 | 27 | $2.67 | 33 |
| B zero-shot | 93.4% | 2.2% | 0.67 | 22 | $5.72 | 105 |
| B history only | 87.9% | 2.2% | 0.47 | 29 | $6.91 | 96 |
| B playbook as induced | 87.9% | 1.1% | 0.47 | 31 | $4.30 | 59 |
| B after sign-off and corrections | 98.9% | 0% | 0.91 | 20 | $3.03 | 36 |

n = 140 items for A and 91 for B. Human input per client was about 15 answered questions and 8 to 14 queue corrections,
which cost $2 to $3 in model calls to absorb. "Silent wrong" is the share of all items resolved wrongly without an
escalation: the number a controller cares about most.

What to say about it:

- A frontier model with tools is already strong. Maximor told us as much. Do not pitch raw accuracy as the innovation.
- On the strict client, the playbook as induced is 5 points less accurate than zero-shot. Rules with an unanswered
  question do not execute, and the investigator may not book an entry on an unsigned rule, so it escalates instead.
  That is the design, and it is why silent errors halve before anyone has answered anything.
- After sign-off the order flips on every column at once: more accurate, no silent errors on B, 4.5x cheaper on A.
- History without a playbook is not the value. Giving the model the client's ERP trail and no playbook cost more than
  zero-shot and was no more accurate on B: it reads more and escalates more. The playbook is what adds value, not retrieval.
- The deterministic matcher clears 45 to 51% of items here, not the 90% in our original brief. Matcher plus compiled
  playbook rules clear 96% on A and 88% on B after sign-off, at zero model cost. Quote those numbers, not 90%.
- Escalation precision as induced is low (0.38, 0.47). It asks when unsure. After sign-off it is 0.58 and 0.91.
- Single runs. A difference of one to three items is noise. Nothing here has been repeated three times.

## Questions to trust (development holdout, March 16-31, final code)

How many human answers until the zero-cost tiers handle the exceptions right, with zero silent wrong resolutions.
Playbook induced from January and February; answers come from a simulated controller that has the client's full
written policy and may volunteer limits beyond what was asked (the figure depends on that, and the JSON says so).

- Client B: 31% of exceptions handled right at $0 as induced, 72% after 3 answers, 90% after 4, 93% after 10.
  Items left for the model fall from 39 to 9; estimated model cost per half month from $3.80 to $0.88.
- Client A: 89% as induced, 91% after one stated limit, then flat.
- Silent wrong resolutions: zero at every one of the 37 points.
- The dip on B at the second answer is the system withdrawing a rule: the controller said the wire-fee rule is not
  about the amount, so it stopped executing. The curve JSON captions that point.
- Client A is flat because the execution floor refused two rules the patcher wrote from the controller's answers: as
  written they disagreed with most of the client's own history, twice. The safety worked; the value of those answers
  is forfeited until a person settles "change of policy, or did I write it down wrong?". An earlier run with a luckier
  induction reached 98% on A. Induction and patch quality vary run to run, and each curve is one run.
- Model cost on the curve is an estimate: items left after the free tiers times the measured cost per investigated item.

## Two holes the development curve caught, and how they were closed

Both were found on the March holdout before the freeze, neither on the hidden month.

1. One correction became a policy. A rule created from a single queue correction was approved and executed on other
   items with one third agreement with history. Now any rule a person teaches is replayed over the client's history
   before it is saved; if it meets at least two comparable past items and agrees with fewer than 60% of them, the
   patcher gets one retry with the counterexamples, and if it still fails the rule does not execute. It is kept as
   guidance with the question "change of policy from now on, or did I write it down wrong?".
2. A mistranslated answer became policy. A controller's free-text answer about card settlements was turned into a rule
   that read a document field that does not exist, so whole fees went to the write-off account: 33 silent wrong
   resolutions on the dev holdout, with a back-test of 0 agreements and 109 disagreements that was ignored because a
   human had "confirmed" it. Same floor, now applied to interview answers as well as corrections. A declared change
   of policy is the explicit exemption: dated, tied to the senior who settled it, and shown in the playbook diff.

Also closed before the freeze, from a class-level audit of the interim runs (tier and rule origin only, no item
details): the investigator can no longer book an entry while citing a rule that is only proposed; and a rule whose
precedents are all round multiples of 5 is treated as a fee schedule, not a range, and asks "is this about the amount
at all?". That last change was made after we were told the failure class on the interim set, so the interim set is
burned for that rule. The blind set is the real test of it.

All wrong matches in the audited interim runs came from the rule tier or the investigator. The matcher made none.

## BenchRec (real external data)

Matcher tier only. 32,048 labelled bank lines, thresholds tuned on the train split, eval scored once.

- Pair-level precision 99.89% at an 89.6% match rate (every ledger record we attach belongs to the labelled group).
  The baseline shipped with the dataset: 99.45% at 65.0%.
- Strict precision (predicted set equals the labelled set) 96.5% at 86.6%; baseline 95.2% at 62.2%. Strict is capped
  because production staff bulk-matched interchangeable same-amount lines under one label.
- Quote both. `benchrec/matcher.py` was built for that dataset's shape; it shares the abstain-unless-unique principle
  with `shadow/matcher.py`, not its code. BenchRec has no many-to-one batch structure or exception reasoning, so it
  says nothing about the playbook or the investigator.

## Things a judge may ask

- "Is the test set yours?" Month 4 is authored blind by a teammate after the code was frozen and tagged; the agent
  code cannot open the answer key or the simulator's truth, and a test enforces that. The interim set was built by a
  separate agent and never read by the agent's developers; it is labelled interim everywhere and is not a headline.
- "What did it learn from?" An ERP-style trail: reconcile links, terse or empty journal memos, who posted what and
  when, an approvals log where one exists, some internal email. One inconsistent clerk per client, a few mistakes,
  a month partly done in a spreadsheet. No table of decisions or reasons.
- "Can it unlearn?" Yes. The playbook is the induced version plus stored patches. Retracting one input replays the
  rest with no model call, changes only the rules that input touched, and re-opens exactly the past resolutions that
  no longer hold. Measured live: 0.07 s; "3 checked, 1 re-opened".
- "What does it refuse to do?" Match a payment to changed bank details, however exactly it ties. Act inside a band
  where the client has never been seen to act. Book an entry on a rule nobody signed off. Widen a limit on a junior's
  say-so.
- "What is next?" Three repeats per condition with ranges; statistical drift detection beyond the conflict path; the
  SDK backend with a shared cached prompt (the CLI backend writes a fresh prompt cache on every call, so our costs are
  an upper bound); close, accruals and forecasting are out of scope by choice.
