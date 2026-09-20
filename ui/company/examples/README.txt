EXAMPLE COMPANY: SYNTHETIC CSV REHEARSAL

These are small example files, not real company records or an accuracy benchmark.
No policy is pre-installed. The model learns it from the uploaded history.

1. Open /company/setup.html?example=1 to fill the example company details.
   Review the details and create the workspace.
2. On History, import these CSVs in order:
   history/bank_lines.csv (Bank statement)
   history/ledger_entries.csv (Cash-clearing ledger)
   history/invoices.csv (Invoices)
   history/reconciliations.csv (Reconciliation history)
   history/adjustments.csv (Adjusting entries)
   Confirm each column mapping and import. The example headers need no model call.
3. On Playbook, click Write my playbook. This calls the configured Anthropic model.
4. On New receipts, import these CSVs:
   new_receipts/bank_lines.csv
   new_receipts/ledger_entries.csv
   new_receipts/invoices.csv
   These files have source records only, with no matches or decision labels.
5. On Review, select March 2026, Check receipts, then Reconcile receipts.
   AI investigation is optional and enabled by default.
6. Open the review queue for the cases that need a person.

Example accounts: 1200 Accounts receivable; 6990 Small payment differences;
2000 Accounts payable.
People: id clerk, role bookkeeper, not senior; id lead, role finance lead, senior.
The default preparer is clerk.
Historical records cover January and February. New receipts are in March.
Future model outputs can differ from the integration verification.
