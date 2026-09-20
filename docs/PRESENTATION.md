# Audita: the submission and judging guide

## Links to use

Public submission demo: https://audita-hackmit.vercel.app

Public source code: https://github.com/psamme/audita

The public site is a recorded interactive execution with benchmark results. The local app is the live demo where approvals and undo change policies.

## Start here

Open http://127.0.0.1:8795/ in the live presentation. Old tabs on port 8787 redirect here; they are not a second app to demo. It opens the review queue with the shortfall policy selected. Use the three navigation links in order: Review queue, Learned policy, Benchmark. Every other screen remains under More. Company onboarding remains available under More → Workspace.

Use two people if possible: one talks, one operates. The operator should follow the clicks below without narrating them. With one person, finish each click before speaking again. Rehearse once in three minutes, then undo the answer to restore the queue.

## The one-sentence pitch

Audita learns how a finance team handled past exceptions, turns uncertainty into focused review questions, and applies approved policies to the next related cases with an evidence trail and undo.

## The three-minute demo

### 0:00–0:20 · The problem, already on screen

SAY: “A payment arrives $12.40 short. Should you write it off or chase the customer? The right answer depends on the company. Audita learns from the team's past decisions and asks a person only where the evidence is not enough.”

SCREEN: Review queue, Lucky Quarter Holdings, Policy questions, Confirm how to handle shortfalls. The three differences are $12.40, $13.10 and $14.20. The fresh demonstration starts with nine bank transactions to review.

### 0:20–0:45 · Show the evidence

CLICK: “Why Audita suggested this”, beneath the policy form. Show one historical example, then collapse it.

SAY: “These policies came from model induction on historical reconciliation links, adjustment journals, approvals and emails. The history suggests small write-offs, but the account choices conflict. Audita surfaces that question instead of inventing an answer.”

DISCLOSURE: “This interactive workflow uses synthetic companies and constructed receipts. We'll show a separate real-data benchmark at the end.”

### 0:45–1:20 · The moment that matters

CLICK: Keep the displayed senior reviewer. Set “Limit you authorize” to 15. Tick the checkbox approving the displayed rule and adjustment account, effective May 1. Click “Preview what changes”.

PAUSE: The preview shows three fewer transactions to review, 9 → 6, and the proposed adjustments. Nothing is saved yet.

SAY: “We are approving a policy for a group, not clicking through each receipt. The preview shows exactly which cases change.”

CLICK: “Approve policy and clear eligible cases”. The queue switches to Resolved. Open one of the three short payments if it is not already selected.

SAY: “One answer clears three receipts. The policy runs as code, so this rerun needs no model call.”

### 1:20–1:45 · Guardrails and undo

CLICK: Verification. Show the two held vendor payments. Optionally open the source-email link, then return to the queue.

SAY: “A matching amount does not override a bank-change warning. Both payments stay held for independent verification. Audita does not initiate payments.”

CLICK: “Undo latest policy answer”, then its confirmation. The three receipts reopen. Select Policy questions again if needed.

SAY: “If a policy answer was wrong, undo rechecks the affected decisions and reopens those cases. The evidence trail remains.”

### 1:45–2:10 · Show what was learned

CLICK: “2. Learned policy”. Show an evidence citation and one rule. Do not answer another policy question here during the timed demo.

SAY: “The learned unit is a versioned company policy with cited precedents, not a one-off chat answer. Another company's history can lead to a different review decision.”

OPTIONAL, only if asked: switch the queue to Meridian and show its shortfall still routed to review. This is a difference in policy and routing, not a claim that both companies initially auto-resolved the same case.

### 2:10–2:45 · Real-data evidence

CLICK: “3. Benchmark”. Keep the headline and comparison visible.

SAY: “Separately, we tested matching on BenchRec: 32,048 real, anonymized bank transactions. We correctly matched 90.5 percent of the matchable lines at 99.87 percent pair-level precision, with zero LLM calls. The supplied reference matcher reached 65 percent coverage. We made 37 incorrect matches versus its 114, using the same scorer.”

SAY: “Thresholds and conventions were learned from training data. This benchmark measures our separate matching implementation, while the demo shows the human review and learning workflow.”

### 2:45–3:00 · Finish with the product

SAY: “Audita turns the finance team's prior decisions into reusable policies. People answer the uncertain questions, repeated cases clear, and every change is inspectable and reversible.”

STOP. Let judges ask questions. Do not introduce another feature in the final fifteen seconds.

## If you only have 90 seconds

Open the queue. Explain the $12.40 shortfall in one sentence. Enter 15, tick approval, preview, then approve. Point to the three cleared receipts and two vendor holds. Open Benchmark and state the result, baseline, and separate evaluation scope. Finish. Skip the full playbook, second company, deposit policy, Jev, uploads, Audit and Close.

## Backup, and what to do when something goes wrong

Open the downloaded recorded-demo.html directly, or use More → Recorded fallback. Use Next or the right arrow. Say: “This is a recorded execution of the same pipeline, including approval and undo.” It works offline and contains the benchmark context. It is an interactive recording, not a video or live backend.

If a preview reports stale data, refresh the queue and preview again. If a backend error persists after one retry, switch to the recording. Do not debug on stage.

If the queue starts at six review items with three resolved, a prior rehearsal approved the shortfall policy. Click Undo latest policy answer twice to restore it, then select Policy questions. If the state differs for another reason, use the recording rather than improvising counts.

The canonical judging sandbox is work/judging-submission. Earlier work is preserved in work/judging-final. Restart the server with: uv run python demo_stage.py --data-dir work/judging-submission --port 8795. Do not run model induction during judging. Do not reset company data or hidden evaluation data.

## The claims cheat sheet

- Dataset: BenchRec cash v1.0, real anonymized bank-to-ledger reconciliation data. Source: https://www.operartis.com/benchrec
- Evaluation size: 32,048 bank lines; 31,836 have labelled ledger matches.
- Training: 68,975 bank lines, including 11,054 groups closed by analysts. Thresholds and conventions frozen before evaluation. Training and evaluation dates overlap; this is a held-out transaction split, not a future-period test.
- Coverage: 28,818 correct / 31,836 matchable = 90.5%.
- Precision: 28,818 correct / 28,855 predictions = 99.87%. There are 37 incorrect predictions.
- Pair-level correctness: all attached ledger entries belong to the labelled group; an incomplete group may still count. Exact complete-group precision is 96.50%, with 87.46% coverage.
- Comparison: supplied reference matcher, same scorer, 65.0% coverage and 114 incorrect predictions. No claim of a state-of-the-art or official leaderboard result.
- Matcher alone reaches 89.6%; learned conventions add about 0.9 percentage points. Do not credit the entire 90.5% to policy learning.
- No LLM calls applies to this benchmark and the live deterministic approval demo. Learning the synthetic-company policies used model calls.
- Say “no hand-entered company policy rules” rather than “no configuration”. CSV mapping, reviewer roles and company setup still exist.
- The benchmark implementation is separate from the company-upload pipeline. It does not establish end-to-end production accuracy.
- The interactive demo clears three constructed receipts with one answer. Do not claim measured real-world hours or dollars saved.

## Judge questions and short answers

“Why not just use rules?” The output is executable rules, but the system proposes them from the team's evidence, exposes unsupported or conflicting decisions, and lets a reviewer authorize changes with a preview and undo.

“Is this just a chatbot?” The consequential step is a versioned policy change and deterministic rerun over affected cases. The UI shows source evidence, what will change before approval, and which cases reopen on undo.

“Where does the model help?” It reconstructs policy candidates from messy historical records and can investigate unresolved exceptions. The model is not needed for every recurring transaction.

“What is actually real?” The BenchRec data is real and anonymized. The two-company live demo is synthetic, with genuinely model-induced policies and real execution of preview, approval and undo.

“Does 99.87% mean the entire accounting task is right?” No. It is pair-level precision for emitted benchmark matches under the stated scorer. Complete-group precision is 96.50%. The number does not cover the upload workflow or every accounting decision.

“Can a company upload its own data?” Yes, the local prototype has company setup, historical CSV import, policy learning, and later undecided receipts. It accepts exports rather than live ERP connectors. The public recording does not accept uploads.

“Who is allowed to approve?” The prototype checks asserted reviewer roles, but the local role selector is not production authentication. There are no real payment or ledger-posting integrations.

“Why are some conventions held back?” They do not meet the historical agreement floor or lack enough evidence. They become suggestions or review questions instead of automatic decisions.

## Where the AI is used

Claude proposes company policies from historical evidence, investigates unresolved exceptions when enabled, and independently re-performs sampled cases in the audit feature. Deterministic code checks the policies and executes matching, preview, approval, reruns and undo.

Jev, through TypeSafe's jev-latest model, is an optional review assistant. Clicking Get suggestion sends the selected case, policy, source evidence, reviewer roles and optional note. It chooses a reviewer and next step from defined options. Low confidence falls back to manual triage. It cannot approve policies, bypass verification holds or move money. Confidence scores are not measured accuracy.

Pitch line: “Claude learns the company's conventions. Jev suggests the reviewer and next action. Code enforces the policy, and a human controls approval.” The live approval demo needs no new model calls; its policies were learned beforehand. BenchRec uses zero LLM calls.

## What to show only if asked

Company onboarding and CSV uploads, the second company, the second deposit-limit answer, Jev next-step suggestions, experiment controls, audit and close. These remain in the project. They are not necessary to prove the core story in three minutes. Close shows the May sample period. Audit needs a separately prepared audit report and shows a clear empty state in this sample workspace.

## Final submission checklist

- Open the public demo URL in a signed-out browser. It must load without a Vercel login.
- Put that URL in the website/demo field and describe it as a recorded interactive demo with benchmark results.
- The GitHub repository is public: https://github.com/psamme/audita. Include it in the source-code field.
- Put the honest benchmark headline and its source in the written submission. Use docs/PLUME.md as the base.
- Keep the local queue and recorded-demo.html open before judging. Charge the laptop and prevent sleep during the presentation.
- If the submission form requires a video, record the three-minute walkthrough separately. The HTML replay is not a video upload.
- Assign the presenter and operator, rehearse once with a timer, then restore the shortfall policy with Undo.
