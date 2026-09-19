/* Screen 1: GET /api/experiment. The identical transaction, resolved per client. */
(async function () {
  const { get, usd, cost, day, esc } = SO;
  const view = document.getElementById("view");

  const ESCALATE_TO = { controller: "the controller", ar_lead: "the AR lead", owner: "the owner", ops_manager: "the ops manager" };

  function outcome(res) {
    if (res.action === "escalate") return { cls: "state-escalate", text: "Escalated to " + (ESCALATE_TO[res.escalate_to] || "a person") };
    if (res.action === "carry_forward") return { cls: "state-carry", text: "Carried forward" };
    if (res.action === "book") return { cls: "state-book", text: "Booked" };
    if (res.action === "match_adjust") return { cls: "state-book", text: "Matched with adjustment" };
    return { cls: "", text: "Matched" };
  }

  function ruleText(rule, chart) {
    // name any account the rule mentions, so the sentence reads without a chart of accounts
    return esc(rule.text).replace(/\b(\d{4})\b/g, (m) => (chart[m] ? m + " " + esc(chart[m]) : m));
  }

  function side(client, item, rule) {
    const res = item.resolution, o = outcome(res);
    const cites = [...res.precedent_ids, ...res.evidence_ids.filter((id) => !res.precedent_ids.includes(id))];
    const more = rule && rule.backtest ? Math.max(0, rule.backtest.support - res.precedent_ids.length) : 0;
    return `
      <article class="panel side">
        <div class="panel-head">
          <div><h3>${esc(client.name)}</h3><span class="sub">${esc(client.policy_label || client.blurb)}</span></div>
          <span class="state ${o.cls}">${esc(o.text)}</span>
        </div>
        <div class="panel-body verdict">
          <p class="line">${esc(res.rationale)}</p>
          ${rule ? `<div class="rule-text">${ruleText(rule, client.chart || {})} <span class="cite">${esc(rule.id)}</span></div>` : ""}
          <div>
            <div class="label">Their own history${rule && rule.backtest ? ` · ${rule.backtest.support} precedents, ${rule.backtest.conflicts} conflicts` : ""}</div>
            <div class="cites">
              ${cites.map((id) => `<a class="cite" href="/api/record/${esc(client.id)}/${esc(id)}">${esc(id)}</a>`).join("")}
              ${more ? `<span class="cite">+${more}</span>` : ""}
            </div>
          </div>
          <div class="meta-row">
            <span>Tier <span class="tier">${esc(item.tier)}</span></span>
            <span>Cost <span class="${item.usage.cost_usd === 0 ? "cost-zero" : ""}">${cost(item.usage.cost_usd)}</span></span>
            <span>${item.usage.llm_calls} model call${item.usage.llm_calls === 1 ? "" : "s"}</span>
            <span>Confidence ${res.confidence.toFixed(2)}</span>
          </div>
        </div>
      </article>`;
  }

  try {
    const [clients, exp] = await Promise.all([get("/api/clients"), get("/api/experiment")]);
    const byId = Object.fromEntries(clients.map((c) => [c.id, c]));
    const rules = {};
    for (const id of ["A", "B"]) {
      const item = exp.results[id];
      rules[id] = item.rule || null;
      if (!rules[id] && item.resolution.rule_id) {
        const pb = await get("/api/playbook/" + id).catch(() => null);
        rules[id] = pb ? pb.rules.find((r) => r.id === item.resolution.rule_id) || null : null;
      }
    }
    const t = exp.transaction;
    view.innerHTML = `
      <div style="display: grid; gap: var(--s-4);">
        <div class="panel txn">
          <div><span class="label">Bank line · ${day(t.date)}</span><div class="desc">${esc(t.description)}</div></div>
          <dl class="kv num">
            <dt>Received</dt><dd class="amt">${usd(t.amount)}</dd>
            <dt>Invoice</dt><dd>${usd(t.invoice_amount)}</dd>
            <dt>Short by</dt><dd>${usd(t.difference)}</dd>
          </dl>
        </div>
        <div class="split">${side(byId.A, exp.results.A, rules.A)}${side(byId.B, exp.results.B, rules.B)}</div>
      </div>`;
  } catch (e) {
    view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`;
  }
})();
