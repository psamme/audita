/* Real ledger: GET /api/real/results, /api/real/playbook, /api/real/examples.
   BenchRec, a real bank's anonymised cash ledger. No model is called anywhere on this page. */
(async function () {
  const { get, usd, esc, day } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary");
  const pct = (x, d) => (x == null ? "n/a" : (x * 100).toFixed(d ?? 2) + "%");
  const int = (n) => Number(n).toLocaleString("en-US");
  const REASON = {
    no_ledger_candidate: "No ledger entry of that amount exists yet",
    several_same_amount_candidates: "Several ledger entries of that amount, nothing to tell them apart",
    one_same_amount_candidate_outside_every_band: "One candidate, but outside every band the history supports",
  };

  function bars(title, note, rows, max, fmt) {
    return `<div class="chart"><div class="chart-head"><h3>${esc(title)}</h3><span class="faint small">${esc(note)}</span></div>
      ${rows.map((r) => `<div class="bar-row"><span class="bar-label">${esc(r.label)}</span>
        <span class="bar-track"><span class="mk ${r.base ? "base" : ""}" style="width: ${Math.max(0.5, (r.value / max) * 100)}%"></span></span>
        <span class="bar-value num">${esc(fmt(r.value))}</span></div>`).join("")}</div>`;
  }

  // same three zones as the playbook screen; log scale because a band can run from cents to dollars
  function band(c) {
    const money = c.unit === "cents";
    const show = (v) => (money ? usd(v / 100) : `${v} ${c.unit}`);
    const max = Math.max(c.asks_to || 1, c.applies_to || 1) * 1.6;
    const pos = (v) => Math.min(100, (Math.log(1 + v) / Math.log(1 + max)) * 100);
    const lo = c.executes ? pos(c.applies_to) : 0, hi = pos(c.asks_to);
    const end = (at, v, p, word) => `<span class="band-end ${at < 8 ? "at-start" : at > 92 ? "at-end" : ""}" style="left: ${at}%"><b class="num">${esc(show(v))}</b><span class="faint">${word} <span class="cite">${esc(p)}</span></span></span>`;
    return `<div class="band"><div class="band-track">
        <span class="band-seg acts" style="left: 0; width: ${lo}%"></span>
        <span class="band-seg asks" style="left: ${lo}%; width: ${hi - lo}%"></span></div>
      <div class="band-ends">${c.executes ? end(lo, c.applies_to, c.applies_precedent, "precedent") : ""}${end(hi, c.asks_to, c.asks_precedent, "widest seen")}</div>
      <div class="legend"><span><i class="band-key acts"></i>${c.executes ? "Applies on its own, $0.00" : "Automatic execution disabled"}</span><span><i class="band-key asks"></i>Asks a person</span><span><i class="band-key out"></i>Does not apply</span></div></div>`;
  }

  function convention(c, onEval) {
    const bt = c.backtest.at_applies_to || c.backtest.at_widest || {};
    const e = onEval[c.id];
    return `<section class="panel"><div class="panel-head"><h3>${esc(c.title)}</h3>
        <span class="runrow"><span class="state ${c.executes ? "state-book" : "state-proposed"}">${c.executes ? "Executes" : "Held back by its own history"}</span></span></div>
      <div class="panel-body stack"><p style="max-width: 78ch;">${esc(c.statement)}</p>
        ${c.asks_to != null ? band(c) : ""}
        <dl class="kv num">
          <dt>Analyst precedents</dt><dd>${int(c.support)} match groups closed by a person</dd>
          <dt>Back-test on history</dt><dd>${bt.bank_lines ? `agrees with the analysts on ${int(bt.agree)} of ${int(bt.bank_lines)} lines (${pct(bt.agreement, 1)})${c.executes ? "" : ", under the 98% it needs to run"}` : "nothing to test it on"}</dd>
          ${e ? `<dt>On the held-out split</dt><dd>${int(e.right)} of ${int(e.bank_lines)} right (${pct(e.precision, 1)})</dd>` : ""}
          <dt>Cites</dt><dd>${(c.precedents || []).map((p) => `<span class="cite">${esc(p)}</span>`).join(" ")}</dd>
        </dl>${c.note ? `<p class="muted small" style="max-width: 78ch;">${esc(c.note)}</p>` : ""}</div></section>`;
  }

  function line(l, found) {
    return `<tr><td>${l.side === "bank" ? "Bank" : "Ledger"}</td><td class="num" style="text-align: right;">${esc(usd(l.amount))}</td><td class="num">${esc(day(l.value_date))}</td>
      <td class="mono small">${esc(l.references || "")}${found ? ` <span class="state state-book">reference found in bank text</span>` : ""}<div class="faint">${esc(l.attributes || "")}</div></td></tr>`;
  }

  function example(x) {
    const graded = x.verdict === "escalated"
      ? (x.suggestion_would_have_been_right == null ? "" : `<span class="state state-proposed">${x.suggestion_would_have_been_right ? "Suggestion matched the key" : "Suggestion did not match the key"}</span>`)
      : `<span class="state ${x.right_per_scorer ? "state-book" : "state-escalate"}">${x.right_per_scorer ? "Right per the answer key" : "Wrong per the answer key"}</span>`;
    const verdict = x.verdict === "cleared" ? "Cleared at $0.00" : x.verdict === "no_ledger" ? "Closed, no ledger side" : "Sent to a person";
    return `<details class="panel ex"><summary class="panel-head" style="cursor: pointer;"><h3 style="font-size: var(--t-md);">${esc(verdict)} · <span class="num">${esc(usd(x.bank_lines[0].amount))}</span> <span class="faint">${esc(x.convention_title || REASON[x.reason] || x.reason)}</span></h3><span class="runrow">${graded}</span></summary>
      <div class="panel-body stack">
        <div class="table-wrap"><table><thead><tr><th>Side</th><th style="text-align: right;">Amount</th><th>Value date</th><th>References</th></tr></thead><tbody>
          ${x.bank_lines.map((l) => line(l)).join("")}${x.ledger_lines.map((l, i) => line(l, x.reference_found_in_bank_text[i])).join("")}</tbody></table></div>
        <dl class="kv num"><dt>Why</dt><dd>${esc(REASON[x.reason] || x.reason)}</dd>
          ${x.measured ? `<dt>Measured</dt><dd>${esc(x.measured)}${x.band && x.band.applies_to != null ? `, band applies to ${esc(String(x.band.applies_to))} and asks to ${esc(String(x.band.asks_to))} ${esc(x.band.unit)}` : ""}</dd>` : ""}
          ${(x.precedents || []).filter(Boolean).length ? `<dt>Precedents</dt><dd>${x.precedents.filter(Boolean).map((p) => `<span class="cite">${esc(p)}</span>`).join(" ")}</dd>` : ""}
          ${x.ledger_lines.length === 0 ? `<dt>Ledger</dt><dd>No candidate entry found</dd>` : ""}</dl></div></details>`;
  }

  try {
    const [r, pb, ex] = await Promise.all([get("/api/real/results"), get("/api/real/playbook"), get("/api/real/examples")]);
    const t = r.tiers, base = t.shipped_baseline, m = t.matcher, mc = t.matcher_plus_conventions, z = r.residue;
    summary.innerHTML = `<span class="state state-book">Real data</span><span><b>${int(r.eval.bank_lines)}</b> bank lines, ${esc(day(r.eval.from))} to ${esc(day(r.eval.to))}. Learned from <b>${int(r.train.bank_lines)}</b> training lines and <b>${int(r.train.analyst_closed_groups)}</b> matches the bank's own analysts closed by hand. Run once with everything frozen. <b>0</b> model calls, <b>$0.00</b>.</span>`;
    const tierRows = (key) => [{ label: "Reference matcher shipped with the data", value: base[key], base: true }, { label: "Our matcher", value: m[key] }, { label: "Matcher plus induced conventions", value: mc[key] }];
    const other = Object.entries(z.escalated_other).sort((a, b) => b[1] - a[1]);
    const head = `<section class="panel"><div class="panel-head"><h3>BenchRec cash v1.0 · held-out evaluation</h3><span class="runrow"><span class="faint small">Code ${esc(r.code_commit || "uncommitted")} · ${esc(r.generated_at.slice(0, 16).replace("T", " "))} UTC</span></span></div>
      <div class="panel-body stack">
        <h2 class="ask-q" style="max-width: 44ch;"><span class="num">${pct(mc.match_rate_pair, 1)}</span> of matchable bank lines matched at <span class="num">${pct(mc.precision_pair, 2)}</span> pair-level precision. Zero LLM calls.</h2>
        <p style="max-width: 85ch;">Tested on <b>${int(r.eval.bank_lines)} real, anonymised bank transactions</b> from BenchRec, a bank-to-ledger reconciliation benchmark. ${int(r.eval.matchable)} have a labelled ledger match. Our benchmark matcher plus learned conventions correctly matched <b>${int(mc.correct_pair)}</b> of those lines and made <b>${int(mc.wrong_pair)} incorrect matches</b> among ${int(mc.matched)} predictions.</p>
        <p class="muted" style="max-width: 85ch;">Thresholds and conventions were learned from ${int(r.train.bank_lines)} training bank lines, then frozen for evaluation. No hand-entered company policy rules; no LLM inference on this benchmark. The training and evaluation dates overlap, so this is a held-out set of transactions, not a future-period test.</p>
        <p class="muted" style="max-width: 80ch;">The reference matcher that ships with the dataset reaches ${pct(base.match_rate_pair, 1)} at ${pct(base.precision_pair, 2)}. Correct means every ledger entry we attach belongs to the match the analysts recorded for that bank line. This permits a partial match to a larger group. Requiring the complete, exact set gives ${pct(mc.precision_strict, 2)} precision and ${pct(mc.match_rate_strict, 2)} coverage of matchable lines.</p>
        <p class="small muted" style="max-width: 85ch;">Scored with our interpretation of the supplied labels, using the same scorer for both systems; this is not an official leaderboard result. It evaluates the BenchRec matching implementation, not the full company-upload or LLM review workflow. <a href="https://www.operartis.com/benchrec" target="_blank" rel="noopener">About the benchmark ↗</a> · <a href="#methodology">Scoring and limitations ↓</a></p>
        <div class="charts">
          ${bars("Bank lines matched correctly", "Share of matchable lines. Scale 0 to 100%", tierRows("match_rate_pair"), 1, (v) => pct(v, 1))}
          ${bars("Wrong matches", "Count, of the matches made. Lower is better", tierRows("wrong_pair"), Math.max(base.wrong_pair, 1), (v) => int(v))}
          ${bars("Precision", "Of the matches made. Scale 0 to 100%", tierRows("precision_pair"), 1, (v) => pct(v, 2))}
          ${bars("Exact-set precision", "Complete labelled group, of predictions made", tierRows("precision_strict"), 1, (v) => pct(v, 2))}
          ${bars("Exact-set coverage", "Share of matchable lines, predicted set equals the labelled set", tierRows("match_rate_strict"), 1, (v) => pct(v, 1))}
        </div></div></section>`;
    const residue = `<section class="panel"><div class="panel-head"><h3>What the matcher left: ${int(z.bank_lines_left_by_matcher)} lines</h3><span class="runrow"><span class="faint small">${pct(m.left_for_review, 1)} of volume</span></span></div>
      <div class="panel-body stack">
        <div class="chart"><div class="bar-row"><span class="bar-label">Where they went</span><span class="bar-track stack-track">
          <span class="mk t1" style="width: ${(z.cleared_by_conventions / z.bank_lines_left_by_matcher) * 100}%" title="Cleared by a convention"></span>
          <span class="mk t2" style="width: ${(z.escalated_with_a_suggestion / z.bank_lines_left_by_matcher) * 100}%" title="To a person, with a suggestion"></span>
          <span class="mk t3" style="width: ${((z.still_for_a_person - z.escalated_with_a_suggestion) / z.bank_lines_left_by_matcher) * 100}%" title="To a person, with a reason"></span></span>
          <span class="bar-value num">${int(z.bank_lines_left_by_matcher)}</span></div>
          <div class="legend"><span><i class="mk t1"></i>Cleared by a convention, $0.00</span><span><i class="mk t2"></i>To a person with a suggestion</span><span><i class="mk t3"></i>To a person with a reason</span></div></div>
        <dl class="kv num">
          <dt>Cleared by induced conventions</dt><dd>${int(z.cleared_by_conventions)} lines, ${int(z.right_pair)} right (${pct(z.precision_pair, 1)})</dd>
          <dt>Sent to a person with a suggestion</dt><dd>${int(z.escalated_with_a_suggestion)} lines. The suggestion matched the key on ${int(z.suggestions_that_were_right)} (${pct(z.suggestions_that_were_right / z.escalated_with_a_suggestion, 0)}), which is why those conventions ask instead of act</dd>
          ${other.map(([k, n]) => `<dt>${esc(REASON[k] || k)}</dt><dd>${int(n)} lines</dd>`).join("")}
        </dl></div></section>`;
    const ops = `<section class="panel"><div class="panel-head"><h3>Who resolves what at this bank</h3><span class="state state-proposed">Measured on history</span></div>
      <div class="panel-body stack"><p style="max-width: 80ch;">${esc(r.account_contrast.finding)}</p>
        <div class="table-wrap"><table><thead><tr><th>Operator</th><th>Kind</th><th style="text-align: right;">Match groups</th><th style="text-align: right;">Closed by hand</th><th style="text-align: right;">One to one</th><th style="text-align: right;">Exact amount</th><th style="text-align: right;">Same value date</th><th style="text-align: right;">Median difference accepted</th></tr></thead><tbody>
        ${r.operators.map((o) => `<tr><td class="mono">${esc(o.operator)}</td><td>${esc(o.kind.split(" (")[0])}</td><td class="num" style="text-align: right;">${int(o.groups)}</td><td class="num" style="text-align: right;">${pct(o.share_manual, 0)}</td><td class="num" style="text-align: right;">${pct(o.share_one_to_one, 0)}</td><td class="num" style="text-align: right;">${pct(o.one_to_one_amount_exact, 0)}</td><td class="num" style="text-align: right;">${pct(o.one_to_one_same_value_date, 0)}</td><td class="num" style="text-align: right;">${o.one_to_one_with_a_difference < 30 ? `too few to say (${int(o.one_to_one_with_a_difference)})` : usd(o.median_accepted_difference_cents / 100)}</td></tr>`).join("")}
        </tbody></table></div><p class="faint small">Operator names are the dataset's own anonymised labels. Which one is the engine is our inference: it never closes anything by hand.</p></div></section>`;
    const convs = `<div class="lede" style="margin-top: var(--s-5);"><div class="label">Induced playbook</div><h2>${pb.conventions.filter((c) => c.executes).length} of ${pb.conventions.length} conventions earned the right to run</h2><p class="muted">Each one is read out of how the analysts closed matches the engine could not, and must agree with them on 98% of the history it touches before it may act. The rest become suggestions for a person.</p></div>
      ${pb.conventions.map((c) => convention(c, pb.by_convention_on_eval)).join("")}`;
    const exs = `<div class="lede" style="margin-top: var(--s-5);"><div class="label">Evidence</div><h2>${ex.examples.length} lines from the held-out split</h2><p class="muted">A fixed random draw, one miss included wherever a convention has one. Open a line to see both sides and the precedent behind the verdict.</p></div>
      ${ex.examples.map(example).join("")}`;
    const notes = `<section class="panel" id="methodology"><div class="panel-head"><h3>How this was measured</h3></div><div class="panel-body"><ul class="muted small" style="max-width: 90ch; display: grid; gap: 6px; padding-left: 1.1em;">${r.protocol.concat(r.notes).map((n) => `<li>${esc(n)}</li>`).join("")}<li>${esc(r.dataset)}.</li></ul></div></section>`;
    view.innerHTML = `<div class="stack">${head}${residue}${ops}${r.summary_only ? `<section class="panel"><div class="panel-body"><h3>Saved benchmark summary</h3><p>This checkout includes verified aggregate results from ${esc(r.generated_at.slice(0, 10))}. Detailed conventions and transaction examples require the original benchmark report. No evaluation was rerun to display these results.</p></div></section>` : convs + exs}${notes}</div>`;
  } catch (e) {
    view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`;
  }
})();
