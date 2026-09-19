# UI data contract

Served by `uv run uvicorn shadow.server:app --port 8787` (FastAPI, JSON only; static files from `ui/` mounted at `/`).
Status: planned shapes, owned by the code-build session. Fields may be added, not renamed.

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
  "precedent_ids": ["B-RES-00231"],      // past human resolutions cited
  "evidence_ids": ["B-DOC-00088", "B-LE-00412"],
  "confidence": 0.0 }

// Item: one bank line or ledger entry the run had to account for
{ "item_id": "B-BL-00977", "item_kind": "bank | ledger",
  "record": { "id", "date", "amount", "description", "counterparty", "ref" },   // ledger: memo, account, posted_at
  "tier": "matcher | guardrail | rule | investigator",
  "resolution": Resolution,
  "trace": [ { "step": 1, "kind": "matcher | guardrail | rule | tool_call | model | final",
               "label": "search_documents", "input": {}, "output": "short text", "ids": ["B-DOC-00088"] } ],
  "usage": { "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0, "llm_calls": 0 },
  "grade": { "correct": true, "wrong_match": false, "key_action": "escalate" } | null }  // null until graded

// Rule
{ "id": "A-R-003", "status": "approved | proposed | retired", "executable": true,
  "text": "Bank fees under $25 go to 6110 with no review.",
  "when": { ... machine conditions ... }, "then": { "action": "book", "account": "6110" },
  "precedent_ids": ["A-RES-00019"], "backtest": { "support": 18, "conflicts": 0 },
  "origin": "induced | correction", "version_added": 1 }
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
| `POST /api/corrections` | body `{client, run_id, item_id, resolution: Resolution, note}` returns `{correction_id, diff, new_version, reran: [Item]}` (similar open items re-resolved under the new playbook) |
| `GET /api/metrics` | see below |
| `GET /api/experiment` | `{transaction: {date, amount, description, invoice_amount, difference}, results: {A: Item, B: Item}}` (the identical ambiguous transaction given to both clients) |

```jsonc
// GET /api/metrics
{ "period": "2026-04",
  "clients": { "A": { "zero_shot": Metrics, "playbook": Metrics, "corrected": Metrics }, "B": { ... } },
  "benchrec": { "n_bank_lines": 32048, "match_rate": 0.0, "precision": 0.0, "left_for_review": 0.0,
                "baseline_match_rate": 0.0, "baseline_precision": 0.0 } }

// Metrics
{ "n_items": 0, "accuracy": 0.0, "escalation_precision": 0.0, "escalation_recall": 0.0,
  "wrong_match_rate": 0.0, "wrong_auto_rate": 0.0, "cost_usd": 0.0, "llm_calls": 0,
  "share_matcher": 0.0, "share_rule": 0.0, "share_llm": 0.0,
  "by_source": { "standard": {accuracy, n}, "blind": {accuracy, n} },
  "scope": "full_month | second_half" }
```
