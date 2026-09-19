/* Screen 4: GET /api/metrics. Three conditions per client on month 4, plus the one
   number that comes from data we did not make (BenchRec). Each measure gets its own
   scale; bars start at zero so near-parity in accuracy looks like near-parity. */
(async function () {
  const { get, usd, esc, cap, period } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary");
  const COND = { zero_shot: "Frontier model, no playbook", playbook: "With the induced playbook", corrected: "After human corrections" };
  const pct = (x, d) => (x * 100).toFixed(d ?? 1) + "%";

  // one measure, one scale, one row per condition; the baseline row is lighter ink
  function bars(title, note, rows, max, fmt) {
    return `<div class="chart"><div class="chart-head"><h3>${esc(title)}</h3><span class="faint small">${esc(note)}</span></div>
      ${rows.map((r) => `<div class="bar-row" title="${esc(r.tip)}">
        <span class="bar-label">${esc(r.label)}</span>
        <span class="bar-track"><span class="mk ${r.base ? "base" : ""}" style="width: ${Math.max(0.5, (r.value / max) * 100)}%"></span></span>
        <span class="bar-value num">${esc(fmt(r.value))}</span></div>`).join("")}</div>`;
  }

  function tiers(conds) {
    const seg = (m) => [["Matcher", m.share_matcher, "t1"], ["Playbook rules", m.share_rule, "t2"], ["Investigator", m.share_llm, "t3"]];
    return `<div class="chart"><div class="chart-head"><h3>Who did the work</h3><span class="faint small">Share of items by tier. Matcher and rules cost $0.00.</span></div>
      ${conds.map(([k, m]) => `<div class="bar-row"><span class="bar-label">${esc(COND[k])}</span>
        <span class="bar-track stack-track">${seg(m).filter((s) => s[1] > 0).map((s) => `<span class="mk ${s[2]}" style="width: ${s[1] * 100}%" title="${esc(s[0])} ${pct(s[1])}"></span>`).join("")}</span>
        <span class="bar-value num">${pct(m.share_matcher + m.share_rule, 0)} free</span></div>`).join("")}
      <div class="legend"><span><i class="mk t1"></i>Matcher</span><span><i class="mk t2"></i>Playbook rules</span><span><i class="mk t3"></i>Investigator (model)</span></div></div>`;
  }

  function clientBlock(name, conds, tag) {
    const rows = (key) => conds.map(([k, m]) => ({ label: COND[k], value: m[key], base: k === "zero_shot", tip: `${COND[k]}: ${m.n_items} items, ${m.llm_calls ?? "?"} model calls` }));
    const maxCost = Math.max(...conds.map(([, m]) => m.cost_usd), 0.01);
    const maxErr = Math.max(...conds.map(([, m]) => m.wrong_auto_rate), 0.01);
    return `<section class="panel"><div class="panel-head"><h3>${esc(name)}</h3><span class="runrow">${tag}<span class="faint small">${conds[0][1].n_items} items${conds[0][1].scope === "second_half" ? ", 16 to 30 April" : ""}</span></span></div>
      <div class="panel-body charts">
        ${bars("Cost of the run", "Model spend", rows("cost_usd"), maxCost, (v) => usd(v))}
        ${bars("Silent errors", "Wrong and not escalated. Lower is better.", rows("wrong_auto_rate"), maxErr, (v) => pct(v))}
        ${bars("Resolution accuracy", "Scale 0 to 100%", rows("accuracy"), 1, (v) => pct(v))}
        ${tiers(conds)}
      </div></section>`;
  }

  function benchrec(b) {
    const rows = (key, baseKey) => [{ label: "Our matcher", value: b[key], tip: `${b.matched.toLocaleString()} matched of ${b.n_matchable.toLocaleString()} matchable` }, { label: "Shipped baseline", value: b[baseKey], base: true, tip: "The baseline that ships with the dataset" }];
    return `<section class="panel"><div class="panel-head"><h3>BenchRec, data we did not make</h3><span class="faint small">${b.n_bank_lines.toLocaleString()} real bank lines from a production cash ledger. Matcher tier only, no model calls.</span></div>
      <div class="panel-body charts">
        ${bars("Lines matched automatically", "Scale 0 to 100%", rows("match_rate", "baseline_match_rate"), 1, (v) => pct(v))}
        ${bars("Precision of those matches", "Scale 0 to 100%", rows("precision", "baseline_precision"), 1, (v) => pct(v, 2))}
      </div></section>`;
  }

  try {
    const [m, clients, runs] = await Promise.all([get("/api/metrics"), get("/api/clients"), get("/api/runs")]);
    const name = Object.fromEntries(clients.map((c) => [c.id, c.name]));
    const order = ["zero_shot", "playbook", "corrected"];
    const blocks = [];
    const month4 = Object.entries(m.clients || {}).filter(([, c]) => c && Object.keys(c).length);
    if (month4.length) {
      const tag = `<span class="state ${m.test_set === "blind" ? "" : "state-proposed"}">${m.test_set === "blind" ? "Blind test set" : "Interim test set"}</span>`;
      for (const [id, c] of month4) blocks.push(clientBlock(`${name[id] || id} · ${period(m.period)}`, order.filter((k) => c[k]).map((k) => [k, c[k]]), tag));
      summary.innerHTML = m.test_set === "blind" ? "" : `<span>${esc(period(m.period))} numbers use an interim test set built by the agent. The teammate's blind set replaces it.</span>`;
    } else {
      // month 4 not graded yet: show the March holdout, and say so
      for (const id of ["A", "B"]) {
        const dev = order.map((k) => [k, (runs.find((r) => r.client === id && r.condition === k && r.period === "2026-03" && r.cost_usd > 0) || {}).metrics]).filter(([, x]) => x && x.accuracy != null);
        if (dev.length) blocks.push(clientBlock(`${name[id] || id} · March holdout`, dev, `<span class="state state-proposed">Development holdout</span>`));
      }
      summary.innerHTML = `<span>${esc(period(m.period))} runs are still in flight. These are the March holdout numbers, not the headline.</span>`;
    }
    if (m.benchrec && m.benchrec.n_bank_lines) blocks.push(benchrec(m.benchrec));
    view.innerHTML = `<div class="stack">${blocks.join("") || `<div class="panel error">No graded runs yet.</div>`}</div>`;
  } catch (e) {
    view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`;
  }
})();
