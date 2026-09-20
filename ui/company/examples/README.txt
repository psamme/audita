EXAMPLE COMPANY: SYNTHETIC CSV REHEARSAL

These are small example files, not real company records or an accuracy benchmark.
No policy is pre-installed. The model learns it from the uploaded history.

1. Open /company/setup.html?example=1 to fill the example company details.
   Review the details and create the workspace.
2. On History, drop all five files in history/ on the page at once. Each one is named from
   its own columns, so no order is needed and nothing has to be selected by kind. Check what
   it decided, confirm the column mappings, then Import. The import runs parents first so the
   reconciliation and adjustment rows resolve. The example headers need no model call.
3. On Playbook, click Write my playbook. This calls the configured Anthropic model.
4. On New receipts, drop the three files in new_receipts/ the same way. They have source
   records only, with no matches or decision labels.
5. On Review, select March 2026, Check receipts, then Reconcile receipts.
   AI investigation is optional and enabled by default.
6. Open the review queue for the cases that need a person.

Example accounts: 1200 Accounts receivable; 6990 Small payment differences;
2000 Accounts payable.
People: id clerk, role bookkeeper, not senior; id lead, role finance lead, senior.
The default preparer is clerk.
Historical records cover January and February. New receipts are in March.
Future model outputs can differ from the integration verification.
