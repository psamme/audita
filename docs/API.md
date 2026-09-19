# UI data contract

Served by `uv run uvicorn shadow.server:app --port 8787` (FastAPI, JSON only; static files from `ui/` mounted at `/`).
Status: implemented in `shadow/server.py`. Fields may be added, not renamed. Until the teammate's blind month 4 lands, month-4 numbers come from an agent-built validation set: `GET /api/metrics` says `"test_set": "interim"` and every such run has `"label": "interim"`. Show that label wherever those numbers appear.

Clients are `A` (Lucky Quarter Holdings, vending/laundromat, lenient) and `B` (Meridian AI, strict). Test period is `2026-04`.
Conditions are `zero_shot`, `playbook`, `corrected`.

## Shared objects

```jsonc
// Resolution: the same shape for history (humans), agent output and the answer key
{ "action": "match | match_adjust | book | escalate | carry_forward",
  "ledger_ids": ["B-LE-00412"],
  "adjustments": [{"account": "7710", "amount": 25.00}],   // sum = sum(ledger) - bank amount
  "escalate_to": "controller | ar_lead | owner | ops_manager | null",
  "rationale": "one or two sentences",
  "rule_id": "B-R-007 | null",
  "precedent_ids": ["B-BL-00231"],       // past items in the ERP trail handled the same way
  "proposed": Resolution | null,          // what the investigator wanted to do when code forced an escalation (control flag, low confidence)
  "evidence_ids": ["B-DOC-00088", "B-LE-00412"],
  "questions": ["Did Brightwater deduct a bank charge?"],   // when escalating: what the person needs to answer (may be empty)
  "confidence": 0.0 }

// Item: one bank line or ledger entry the run had to account for
{ "item_id": "B-BL-00977", "item_kind": "bank | ledger",
  "record": { "id", "date", "amount", "description", "counterparty", "ref" },   // ledger: memo, account, posted_at
  "tier": "matcher | guardrail | rule | investigator",
  "resolution": Resolution,
  "trace": [ { "step": 1, "kind": "matcher | guardrail | rule | tool_call | model | final",
               "label": "search_documents", "input": {}, "output": "short text", "ids": ["B-DOC-00088"] } ],
  "usage": { "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0, "llm_calls": 0 },
  "grade": { "correct": true, "wrong_match": false, "key_action": "escalate" } | null,  // null until graded
  "rule": Rule | null,                    // the cited rule, from the playbook track and version that run used
  "control_flags": [{"flag": "payee_bank_details_changed", "detail": "..."}] }   // present on guardrail/investigator items

// Rule
{ "id": "A-R-003", "status": "approved | proposed | retired", "executable": true,
  "text": "Bank fees under $25 go to 6110 with no review.",
  "when": { ... machine conditions ... }, "then": { "action": "book", "account": "6110" },
  "precedent_ids": ["A-BL-00019"],        // bank lines / ledger entries in the ERP trail that show the convention
  "precedent_count": 18, "confidence": 0.95,   // measured: smoothed share of replayed history the rule agrees with
  "backtest": { "support": 18, "conflicts": 0, "conflict_examples": [{item_id, observed, rule_would}] },
  "open_question": "I see 2 of these, both posted by the controller 3+ days later. Is controller approval required?" | null,
  "human_confirmed": true,                // set once a person answered the question or made the correction behind it
  "origin": "induced | correction A-COR-0003 | interview A-COR-0001", "version_added": 1 }

// History is an ERP trail, not a table of decisions. A precedent (as returned in /api/experiment and by the
// investigator's find_precedents tool) looks like this:
{ "id": "A-BL-00311", "period": "2026-02", "item_kind": "bank", "date", "text", "counterparty", "amount",
  "left_open": false, "handled_by": ["bookkeeper"], "days_to_handle": 1,
  "booked": [{"account": "6990", "amount": 9.12, "memo": "w/o", "by": "bookkeeper"}],
  "approvals": [], "linked_ledger": [{id, amount, account, memo, ref, days_before_bank}], "diff": 9.12, "diff_pct": 0.31 }
```

## Routes

| Route | Returns |
|---|---|
| `GET /api/clients` | `[{id, name, blurb, chart: {account: name}}]` |
| `GET /api/runs` | `[{run_id, client, condition, period, created_at, n_items, tiers: {matcher, guardrail, rule, investigator}, cost_usd, metrics}]` |
| `GET /api/runs/{run_id}` | `{...run summary, items: [Item]}` |
| `GET /api/runs/{run_id}/queue` | items whose resolution is `escalate`, newest first (the human review queue) |
| `GET /api/record/{client}/{id}` | any record by id (bank line, ledger entry, invoice, document, historical resolution) for evidence links |
| `GET /api/playbook/{client}` | `{client, version, versions: [{version, created_at, cause}], rules: [Rule]}`; `?version=n` for an older one |
| `GET /api/playbook/{client}/diff?from=1&to=2` | `{from, to, cause: {correction_id, item_id, note}, added: [Rule], removed: [Rule], changed: [{before: Rule, after: Rule}]}` |
| `POST /api/corrections` | body `{client, run_id, item_id, resolution: Resolution, note}` returns `{correction_id, diff, new_version, explanation, check, reran: [Item]}`. `check` is code's verdict that the patched playbook reproduces the human's answer on that item; `reran` are other queue items the new rule now clears (rules only, $0). Takes 10-25 s (one model call plus a back-test). `diff` is null when the correction changed nothing. |
| `GET /api/metrics` | see below |
| `POST /api/playbook/answer` | body `{client, rule_id, answer}`: the controller answers a proposed rule's `open_question`; returns `{correction_id, diff, new_version, explanation}` |
| `GET /api/experiment` | `{transaction: {date, amount, description, invoice_amount, difference, counterparty}, results: {A: Item, B: Item}}`; each result Item also carries `rule` (the cited Rule inline, or null), `precedents` (list of precedent objects) and `playbook_version`. Cached; `?fresh=true` re-runs it live (about 15 s). `?version=1` pins both clients to that playbook version (1 = as induced, before any human input); default is the latest version, so `version=1` vs default is the before/after sign-off toggle. |
| `GET /api/experiment/bank-change` | one Item (client B): a vendor payment whose amount ties exactly to the ledger but went to bank details that differ from the vendor's history after a change-request email. Expect `tier: "guardrail"`, `control_flags`, `resolution.action: "escalate"` to the controller, and `resolution.proposed` holding what the investigator would otherwise have done. Cached; `?fresh=true` re-runs live. |

```jsonc
// GET /api/metrics
{ "period": "2026-04", "test_set": "interim | blind", "backend": "cli | sdk", "model": "claude-opus-5",
  "clients": { "A": { "zero_shot": Metrics, "playbook": Metrics, "corrected": Metrics }, "B": { ... } },  // all scored on April 16-30 (like for like)
  "full_month": { "A": { "zero_shot": Metrics, "playbook": Metrics }, "B": { ... } },
  "detail": { "A": { "induction": {rules, approved, open_questions, cost_usd},
                     "human_input": {questions_answered, queue_corrections, corrections_that_changed_the_playbook, playbook_versions, learning_cost_usd} } },
  "benchrec": { ...runs/benchrec_results.json verbatim: n_bank_lines, n_matchable, matched, correct, precision, match_rate,
                left_for_review, baseline_precision, baseline_match_rate, thresholds, notes } }

// Metrics
{ "n_items": 0, "accuracy": 0.0, "escalation_precision": 0.0, "escalation_recall": 0.0,
  "wrong_match_rate": 0.0, "wrong_auto_rate": 0.0, "cost_usd": 0.0, "llm_calls": 0,
  "share_matcher": 0.0, "share_rule": 0.0, "share_llm": 0.0,
  "by_source": { "standard": {accuracy, n}, "blind": {accuracy, n} },
  "scope": "full_month | second_half" }
```

## Added 2026-09-19 (bands, findings, questions, unlearning, stale evidence)

GETs never run the pipeline or write anything. Everything that costs model calls or changes the playbook is a POST.

```jsonc
// Rule additions
{ "bands": { "amount_max": {                 // one entry per numeric condition that expresses a policy line
      "side": "upper | lower",
      "lo": 22.56, "hi": null,               // upper: fires at or below lo, silent at or above hi, ESCALATES in between. hi null = never seen a larger one
      "lo_precedent": "A-BL-00311", "hi_precedent": null, "n_known": 11, "n_other": 0,
      "written": 22.56,                      // what the model wrote before the trail was replayed
      "source": "trail | interview | stated",// interview = moved by a yes/no answer; stated = a person gave the number
      "beyond": ["A-BL-00402"] } },          // items handled the rule's way beyond hi: reported as findings, never used to widen
  "valid_from": "2026-04-09" | absent,       // policy change: precedents before this date no longer count toward the bands
  "awaiting_senior": true | absent,          // taught by a non-senior; proposed until a senior confirms
  "repaired_in_round": 2 | absent }          // failed its own precedents after induction and was rewritten (max 3 rounds)

// Resolution additions
{ "reason": "in_band | no_rule | conflicting_precedents | fraud_shaped | thin_precedent | null",   // set on every escalation
  "questions": ["..."], "proposed": Resolution | null }
// Item additions: "band": {condition, value, lo, hi, lo_precedent, hi_precedent} on in_band escalations (tier "rule", $0),
//                 "evidence_fingerprint": {ledger: {id: {amount, date, account, counterparty}}, documents: [ids]}
// Run summary additions: track, llm, escalated, escalation_reasons: {in_band, no_rule, ...}
// Trace step kinds: case_file | matcher | rule | tool_call | check | final
// Playbook version cause.type: induction | correction | interview | retraction | one_off_exception
// GET /api/playbook/{client} also returns findings: [Finding]

// Finding: a past item that disagrees with a rule the rest of history supports (probable mistake or undocumented exception)
{ "finding_id": "AF-003", "rule_id": "A-R-002", "rule_text": "...", "item_id": "A-BL-00077", "date": "2026-01-28", "amount": -19.98,
  "description": "CASH HANDLING FEE", "posted_by": ["Tam Nguyen (part-time clerk)"], "what_was_done": "19.98 to 6120",
  "agreeing_cases": 11, "summary": "one sentence", "evidence_ids": ["A-JE-00041"] }
```

| Route | Returns |
|---|---|
| `GET /api/playbook/{client}/questions` | `{band_questions: [BandQuestion], open_questions: [{rule_id, rule_text, text, precedent_count}]}` widest band first |
| `POST /api/playbook/answer-band` | body `{client, rule_id, condition, value, review: bool}`. Instant, no model call. `review: true` ("yes, send one at that value for review") pulls `hi` down to value; `false` pushes `lo` up. Returns `{correction_id, new_version, diff, band, cause: {band_before, band_after, note}}`; the diff shows the band shrinking under `changed[].before.bands / after.bands` |
| `POST /api/corrections` | now also takes `role` (who is teaching). May return `conflict` instead of a diff (see below). A non-senior role that widens auto-resolution gets a proposed rule with `awaiting_senior` |
| `POST /api/conflicts/{conflict_id}` | body `{client, outcome: one_off_exception | policy_change | mistake, role}`. one_off: recorded, excluded from precedents, no rule change. policy_change: the rule changes with `valid_from` = the item's date. mistake: correction rejected and logged |
| `POST /api/retract` | body `{client, correction_id | precedent_id, note}`. Deterministic, no model call. Returns RetractionResult |
| `GET /api/corrections/{client}` | every input the playbook has received, newest first: `{correction_id, at, type: correction | interview | conflict | conflict_resolved | retraction, ...}`; the list to pick a retraction from |
| `GET /api/reopened/{client}` | items re-opened by retractions: `{run_id, item_id, reason: "rule_retracted", record, before, after}` |
| `GET /api/runs/{run_id}/stale` | `{stale: [{item_id, reason: "evidence_changed", record, resolution, changes: [{record, change: edited | deleted, fields: {amount: {was, now}}}]}], posted_after_reconciliation: [ledger entries]}` |
| `GET /api/curve` | questions-to-trust, per client: `{target_auto_resolve_rate, questions_to_trust, scored_on, per_item_cost_usd, points: [{k, answer_kind: induction | band | open_question | correction, answer, auto_resolve_rate, wrong_matches, wrong_auto, left_for_model_or_human, in_band_escalations, tiers: {matcher, guardrail, rule, investigator}, est_llm_cost_usd}]}`. Development holdout (March), never the hidden month |
| `POST /api/experiment/run` | body `{which: same_transaction | bank_change, version?}`: the only way to run the demo live. `GET /api/experiment[?version=1]` and `GET /api/experiment/bank-change` are cache-only and return 404 until computed |
| `GET /api/runs/{id}` and `/queue` | `?grades=true` attaches per-item grades (off by default: it reveals the answer key item by item). `metrics.by_category` is never served |

```jsonc
// BandQuestion
{ "question_id": "A-R-002:amount_max", "rule_id": "A-R-002", "condition": "amount_max", "value": 35.0, "lo": 22.56, "hi": null,
  "relative_width": null, "rule_text": "...", "text": "... If one came in at $35.00, would you want it sent to someone for review?" }

// Conflict (inside the POST /api/corrections response when a correction contradicts a signed-off, well-supported rule)
{ "correction_id": "A-COR-0007", "rule_id": "A-R-004", "rule_text": "...", "rule_support": 101, "note": "...", "by_role": "bookkeeper",
  "outcomes": ["one_off_exception", "policy_change", "mistake"] }

// RetractionResult
{ "retracted": "A-COR-0005", "new_version": 9, "diff": PlaybookDiff /* cause.type = "retraction" */, "rules_changed": ["A-R-021"],
  "resolutions_checked": 14, "reopened": [{run_id, item_id, reason: "rule_retracted", record, before, after}], "replay_notes": [] }
```
