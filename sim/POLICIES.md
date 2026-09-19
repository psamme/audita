# Ground-truth client policies

This file and everything under `sim/` and `keys/` is off limits to agent code (`shadow/`). It is the source of truth for
how each client's finance team resolves exceptions. The simulator encodes it in the months 1-3 history; the answer key
for month 4 must follow it. When a situation is not covered here, the correct answer is what this team would do given
its temperament below, and if that is unclear, `escalate`.

## Client A: Lucky Quarter Holdings (vending and laundromat roll-up)

Temperament: lenient, pragmatic, small amounts are not worth anyone's time. One bookkeeper, owner (Marv) signs off,
an ops manager runs the routes. Roles for `escalate_to`: `owner`, `ops_manager`.

- Card processor payouts (TapVend, SpinPay) arrive net of fees. Match to the gross sales batch, fees to 6120 per the
  settlement report. If the payout is under the report by up to $15, write the variance off to 6990. Over $15, owner.
- Cash deposits vs driver count sheets: differences up to $20 either way go to 6990, no review. Over $20, ops_manager.
  Several count sheets can be deposited on one slip.
- Driver pattern: the third short over $10 by the same driver within a month goes to the owner, whatever the amount.
- Bank fees under $25 go to 6110 with no review. $25 and over, owner.
- Commercial laundry customers who short-pay by up to $15: match and write off to 6990. Bigger shorts, owner.
- Unidentified deposits under $50 go to 4990 misc income. $50 and over, owner.
- Vendor payments and location commissions normally match exactly. Checks can clear up to two weeks later.
- No history exists for vendor bank-detail changes, duplicate payments or anything fraud-shaped. Those go to the owner.

## Client B: Meridian AI (invented frontier AI lab)

Temperament: strict, evidence-driven, audit-conscious. Every difference is investigated. Roles: `controller`, `ar_lead`,
`ap_lead`.

- No write-offs of any size without controller approval. An unexplained short-pay of $3 goes to `ar_lead`.
- Paystream payouts must tie exactly to the payout report: fees 6310, refunds 4090, chargebacks 6320. Any variance
  from the report goes to `controller`.
- Enterprise wires can cover several invoices; match them using the remittance advice.
- A wire short by sender bank charges is matched with the charge to 7710 only when the charge is evidenced (bank line
  says LESS CHGS or the remittance advice states it). Otherwise `ar_lead`.
- Halcyon Robotics has contractual 2/10 net 30 terms: a 2% discount taken within 10 days goes to 4050. Any other
  customer taking a discount, or Halcyon taking it late, goes to `ar_lead`.
- Bank fees on the bank's fee schedule (incoming wire fee, account analysis) go to 7710 at any amount. Unknown bank
  debits go to `controller`. Sweep interest goes to 7100.
- Large wires with no invoice: 2450 customer deposits if a signed order form email exists, otherwise `ar_lead`.
- A payment to vendor bank details that differ from that vendor's history, following a change-request email, goes to
  `controller` for call-back verification. Never auto-matched, even though the amount ties exactly.
- Ledger entries posted after close into a closed period go to `controller`.
- Duplicate outgoing payments or refunds go to `controller`.

## Universal

A wrong match is worse than an escalation. Anything that looks like fraud (changed bank details, duplicate payouts,
a payee that does not match the vendor master) is escalated at both clients.
