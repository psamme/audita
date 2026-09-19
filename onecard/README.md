# One Payment, Every Desk

HackMIT 2026, Maximor track. Five finance desks work the cash side of an invented AI company for three simulated
months. Every bank line opens one shared **card**. Each desk pins its evidence and its claim to that card, a plain
deterministic checker compares the claims, and nothing reaches the ledger until they agree. What the desks cannot
settle goes to a human; the answer is saved with its reason and becomes a versioned, backtested policy that closes
the same case next month.

The company (Kestrel Inference) is synthetic. In batch runs the human is a scripted controller answering from a
hidden policy. Both facts are on every screen.

## Run it

```bash
# once, from the repository root
python3 -m venv .venv && source .venv/bin/activate && pip install fastapi uvicorn pytest httpx anthropic
cd onecard

python run.py --all                  # A, B and C over three months, scoreboard + runs/seed-7/touchless.svg (about 3 s)
python run.py --all --seeds 7 8 9    # three seeds, with the range
python run.py --until 2026-01-31     # one month of configuration C
python -m pytest tests -q            # 21 tests, including the answer-key leak tests

python run.py --demo                 # configuration C up to 12 January; the hero card is left waiting
python serve.py --run demo           # http://127.0.0.1:8000  review it, approve the rule, run to the end of March
python serve.py --run C              # browse a finished run
```

`--model live` lets ladder rung 5, the policy writer and the close memo call a model (Anthropic SDK when
`ANTHROPIC_API_KEY` is set, else the local `claude` CLI). Every call is logged to `runs/seed-N/model_calls.jsonl`;
`--model replay` reruns from that log with no network. All numbers below are **model off**.

## Results (model off, seeds 7, 8, 9; two full reruns are byte-identical)

| | January | February | March |
| --- | --- | --- | --- |
| **A. Independent desks** touchless | 89.4 to 90.9% | 86.7 to 87.1% | 84.7 to 85.1% |
| wrong postings (sum of 3 seeds) | 3 | 3 | 3 |
| contradictions at close (seed 7) | 2 | 5 | 9 |
| **B. Cards, no memory** touchless | 90.4 to 91.9% | 86.7 to 87.1% | 85.7 to 86.1% |
| wrong postings | 0 | 0 | 0 |
| **C. Full** touchless | 90.4 to 91.9% | 93.9 to 95.0% | 90.8 to 92.1% |
| wrong postings | 0 | 0 | 0 |
| human decisions, C vs B (seed 7) | 8 vs 8 | 6 vs 13 | 9 vs 14 |

What the numbers say, including the part that does not flatter the pitch:

- **Cards do the consistency work.** A leaves contradictions in the locked books every month and never reverses the
  refund that was keyed twice, so its cash balance drifts ($923, then $2,812, then $4,151 on seed 7). B and C lock
  every month with zero contradictions, zero balance error, and zero wrong postings on all three seeds.
- **Memory does the touchless work.** B and C are identical in January. After that C asks the human about half as
  often as B (6 vs 13, 9 vs 14).
- **C does not rise every month.** The hypothesis was "rises month over month in C only". It rises in February and
  falls back in March, because the generator plants more and harder traps each month (B is asked 8, 13, 14 questions)
  and the guardrails ask again for a larger amount than any human has approved, a customer's third repeat in a
  quarter, or a $15.00 gap that is both a plausible wire fee and a plausible 0.5% discount. Read C against B.
- Escalation recall is 100% in every configuration with no lucky guesses: every needs-a-human card was either asked
  or closed by a rule that traces to a human decision. Escalation precision is 50 to 88% with the model off, because
  email-only cases (a parent paying for a subsidiary, a free-text allocation thread) go to the human.
- The learned book agrees with the hidden policy on 20%, then 42%, then 47% of probe questions by month (seed 7), and
  contradicts it on 0%. The rest is silence: the rules start narrow and have not yet seen enough to widen.
- Forecast, four weeks ahead, weekly receipts: $64k mean absolute error with learned payer lags against $147k with
  fixed due dates (seed 7, 8 scored weeks). The forecast desk is the same in all three configurations.
- Audit: 3 of 3 planted self-approval breaches found, 0 false findings.

`python run.py --all` prints every miss by card with its cause.

## How it fits together

```
world/      generate.py    seeded world: bank lines, invoices, emails, contracts, Stripe payouts, ERP feed; truth/ answer key
            controller.py  the scripted human. Reads truth/ and the hidden policy. Desks never see it, only its answers
            metrics.py     the scoreboard, measured against truth/        chart.py  the one chart, dependency-free SVG
spine/      contract.py    reason codes, treatments, accounts, configurations, claim -> journal lines
            store.py       one SQLite file per run                        ledger.py  append-only, period locks, one write path
            tools.py       the only way desks read the world; never shows a record dated after today
            checker.py     invariants over claims and the ledger; the ledger's pre-posting hook
            policy.py      versioned rules: scope, backtest, probation, materiality cap, repeat guardrail
            review.py      one question, two to four options, each with its entry preview
            orchestrator.py  calendar, intake, reopen routing (two rounds), posting, orphan sweep, audit and close
desks/      cash_app.py (ladder rungs 1 to 6)  bank_rec.py  forecast.py  close.py  audit.py  policy_writer.py  llm.py
run.py      the harness: configurations A, B, C, seeds, the demo hold    serve.py  review API and screens    ui/  the screens
```

**Contracts.** All money is integer cents. A card is `{card_id, bank_line, kind, payer, evidence[], claims[], checks[],
status, rounds, human_touch}`. Every claim carries `desk` and `evidence`; a claim with no evidence fails invariant 8.
Reason codes are one closed list in `spine/contract.py`. The answer key is `data/seed-N/truth/truth.json`
(`lines[line_id] -> {entry, settles, residuals, traps, needs_human}`, month-end `balances`, `control_breaches`,
`duplicate_entries`).

**The wall.** `Tools` refuses to be pointed at `truth/`. Nothing under `desks/` or `spine/` imports `world/` or names the
truth files, and the tests check both, plus every public file and tool output for generator-only fields.

**Configurations.** A: same desks and tools, no shared card (the forecast desk never sees the cash application claim),
no checker gate, no policies; the checker runs after the fact at close to count contradictions. B: cards and checker
on, the policy book and alias table wiped on the first of each month. C: everything, carried across months.

## Evaluation method

Touchless rate is cards closed with no human touch over all cards, per month, as the build doc defines it. Outflows
and Stripe payouts are clean by construction and pad that rate, so the scoreboard also prints the rate over customer
receipts only. A wrong posting is a card whose net ledger effect or invoice application differs from the answer key,
or a planted duplicate entry left standing. Policy accuracy asks the learned book and the hidden policy the same 45
questions about a customer neither has seen. Full definitions are in `world/metrics.py`.

## Limits and disclosures

- Synthetic company, scripted human. A real controller is less consistent than a policy file.
- The cash application desk was debugged against seeds 7 to 11; five wrong-posting causes were found and fixed there
  (a human allocation that dropped its shortfall, near-match tolerance applied to named invoices, an overpayment claim
  the forecast desk would not adopt, a parked card that kept re-escalating, a duplicate applied to a same-amount
  invoice). Seeds 12 to 14 were run once afterwards as a holdout: zero wrong postings in B and C.
- The answer key makes some things easy: payer names are clean apart from truncation and two parent companies, every
  trap has exactly one right treatment, and outflows are pre-matched.
- Not built: trap 12 (late remittance after close), foreign currency, the QuickBooks mirror. The generator plants
  traps 1 to 11 and 13.
- The desks are plain Python functions that call one logged model wrapper, not Claude Agent SDK subagents over MCP.
  Rung 5, the policy writer, the close memo and the audit model path have not been exercised in a recorded run: no
  live-model run has been made, so there is no cost number yet.
- The audit desk's default re-performance is mechanical (can every posted line be traced to raw evidence or to a
  human's authority?). `--audit-model` adds a strongest-model rebuild.
