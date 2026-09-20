# Company onboarding and new receipts

Open http://127.0.0.1:8795/company/index.html?track=main on the current demo server.

For a standalone company workspace, run:

```sh
uv run uvicorn shadow.app:app --host 127.0.0.1 --port 8787
```

For the existing isolated stage server plus company onboarding:

```sh
uv run python demo_stage.py --data-dir work/judging-final --port 8795
```

The root URL opens the company workspace. The sample review queue remains at `/queue.html?track=stage`, and all original experiment and playbook screens remain available. Company screens use the main track for that company's own database; they clear a remembered demo track.

## What the company does

1. **Create the workspace.** Name the company, paste the chart of accounts, and add people and roles. At least one reviewer needs authority to approve policies. Match staff IDs to those in the historical exports.
2. **Upload historical decisions.** Drop the whole export folder on the History page at once. Each file is read and named on its own: bank statement, cash-clearing ledger, reconciliation history, adjustments, approvals, invoices, emails, chart of accounts or people. The screen shows what it decided and the evidence for it, you correct anything wrong from a dropdown, and Import writes them parents first so the reconciliation trail resolves against records that already exist. Naming files and matching columns are both deterministic and editable; inspect the date format, signs and preview before importing. Blank CSV templates are under "What we can read".
3. **Learn the policies.** The preflight checks whether the historical records contain usable decisions. “Write my playbook” uses the existing Anthropic induction and repair pipeline. It shows learned rules and unresolved questions. No company policy is pre-installed.
4. **Upload new receipts.** Use the separate New receipts page, which takes a folder the same way. It accepts bank/receipt CSVs, ledger entries, invoices and documents. Payment and ledger dates must fall after the training window; supporting invoices and documents can be older. New files cannot contain reconciliation outcomes, approval files or adjustment decisions, and a file that reads as one is named and refused rather than quietly rejected.
5. **Reconcile and review.** Select the new receipt month and check the records. Then run reconciliation, with optional AI investigation enabled by default. The results separate resolved receipts from escalations. Open the company-specific review queue to inspect evidence, request Jev routing suggestions, and provide a correction. Existing policy history and Undo remain available.

Money in is positive; money out is negative. The ledger upload is the cash-clearing subset, not every line in a general ledger. CSV imports are saved locally. Decisions do not post to an external accounting system.

## Try it without preparing files

Download `ui/company/example-csvs.zip` from the workspace. Click “Use example company details” to prefill the setup, then review and save it. The ZIP's README gives the import order. Its January-February history contains decisions; its March receipts contain source records only. These are small synthetic examples, clearly labelled. Learning still runs the real model.

## Verified behavior

The integration check created a fresh company, imported four historical examples from CSV, and made a real induction request through the existing model pipeline. The model produced four rules. Three subsequent undecided receipts yielded:

- An exact receipt resolved by the deterministic matcher.
- A shortfall resolved using a rule learned from the uploaded history.
- An unidentified receipt escalated to the configured finance lead, including after actual AI investigation.

This verification used six model calls: four for induction/repair ($0.434107) and two for investigation (reported $0.1277). These are observed results on the included synthetic examples, not an accuracy benchmark. Future model outputs may differ.

The browser check also processed these receipts with AI disabled, showed two resolved and one in review, and opened the correct company queue with its actual reviewer roles. The main company onboarding screen remains empty for your own setup.

## How a file is named

No model call, and no menu to start from. Each file's header and sample rows are matched against
the fields every kind requires, using the same column hints the mapping step uses.

- A kind whose required fields are not all present is out, whatever else matches. A file with
  nothing that reads as an amount is not a bank statement.
- Among the kinds that fit, the winner explains the most of the file with the least stretched
  matches, and a required field counts for more when few kinds ask for it. A column named `memo`
  says far more about a ledger than about a statement.
- A column this kind has nowhere to put, which another kind requires by name, counts against it.
  This is what separates a ledger from a statement: both carry a date, an amount and a line of
  text, but only one of them can hold a GL account code.
- The file name is a tiebreak and nothing more. A file called "bank statement january" that
  carries an account code is a ledger somebody misnamed.
- When two kinds land close together, both are shown and the confidence drops, because a coin
  flip presented as an answer costs more than a question.

An unplaced file waits with a "needs you" flag. "Ask the model" spends one call on that file
alone, and a kind the model names is still refused if the file cannot supply its required fields.

## Separation and limits

Learning freezes a SQLite snapshot of the imported history and its cutoff. Re-learning uses that snapshot, so later uploads cannot enter the induction prompt. New records cannot overwrite existing training records. History uploads are closed once learning starts. User corrections remain a separate, visible policy-change path.

This hackathon implementation supports one configured company per local server, CSV exports, and a local role selector rather than authenticated accounts. A separate server or data directory gives a fresh company workspace. Model calls require the configured Anthropic backend; Jev suggestions use the optional TypeSafe connection. Loading pages, confirming CSV columns, and checking receipts make no model calls. Induction, AI investigation and model-assisted corrections do.
