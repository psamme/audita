/* Screen 1: GET /api/experiment. The identical transaction, resolved per client.
   Either side may come back with any action (A escalates with a question before
   sign-off and writes the difference off after), so nothing here assumes which. */
(async function () {
  const { get, usd, cost, day, esc } = SO;
  const view = document.getElementById("view");

  const { role, cap, outcome, verdict, cite, reasoning, ruleBlock } = SO;

  function precedentRow(p, clientId) {
    const who = p.left_open ? "left open" : `${(p.handled_by || []).join(", ") || "handled"}${p.days_to_handle != null ? `, ${p.days_to_handle} day${p.days_to_handle === 1 ? "" : "s"}` : ""}`;
    const booked = (p.booked || []).map((b) => `${usd(Math.abs(b.amount))} to ${b.account}`).join(", ");
    return `<tr>
      <td>${cite(clientId, p.id)}</td>
      <td class="num">${day(p.date)}</td>
      <td class="wrap">${esc(p.text)}</td>
      <td class="r">${usd(p.amount)}</td>
      <td class="wrap">${esc(cap(who))}${booked ? `<div class="faint">${esc(booked)}</div>` : ""}</td>
    </tr>`;
  }

  function side(client, item) {
    const res = item.resolution, rule = item.rule, o = outcome(res), chart = client.chart || {};
    const precedents = item.precedents || [];
    const flags = (item.control_flags || []).map((f) => `<div class="rule-text"><span class="label">Control, enforced by code</span><div>${esc(cap(f.detail || f.flag))}</div></div>`).join("");

    return `
      <article class="panel side">
        <div class="panel-head">
          <div><h3>${esc(client.name)}</h3><span class="sub">${esc((client.blurb || "").split(". ")[0].replace(/\.$/, ""))}</span></div>
          <span class="state ${o.cls}">${esc(o.text)}</span>
        </div>
        <div class="panel-body verdict">
          <p class="line">${esc(verdict(res, chart))}</p>
          ${flags}
          ${reasoning(res)}
          ${ruleBlock(rule)}
          ${precedents.length ? `<div>
              <div class="label">Their own history</div>
              <div class="table-wrap"><table class="grid tight"><tbody>${precedents.slice(0, 3).map((p) => precedentRow(p, client.id)).join("")}</tbody></table></div>
              ${precedents.length > 3 ? `<div class="faint small">and ${precedents.length - 3} more</div>` : ""}
            </div>` : ""}
          <div class="meta-row">
            <span>Tier <span class="tier">${esc(item.tier)}</span></span>
            <span>Cost <span class="${item.usage.cost_usd === 0 ? "cost-zero" : ""}">${cost(item.usage.cost_usd)}</span></span>
            <span>${item.usage.llm_calls} model call${item.usage.llm_calls === 1 ? "" : "s"}</span>
            ${item.playbook_version != null ? `<span>Playbook v${esc(item.playbook_version)}</span>` : ""}
            <span>Confidence ${Number(res.confidence).toFixed(2)}</span>
          </div>
        </div>
      </article>`;
  }

  // "" is the latest playbook (after the controller's answers and corrections); "1" is as induced
  let version = "";
  const route = () => "/api/experiment" + (version ? "?version=" + version : "");

  function render(clients, exp) {
    const byId = Object.fromEntries(clients.map((c) => [c.id, c]));
    const t = exp.transaction;
    view.innerHTML = `
      <div class="screen">
        <div class="panel txn">
          <div><span class="label">Bank line · ${day(t.date)}</span><div class="desc">${esc(t.description)}</div></div>
          <dl class="kv num">
            <dt>Received</dt><dd class="amt">${usd(t.amount)}</dd>
            <dt>Invoice</dt><dd>${usd(t.invoice_amount)}</dd>
            <dt>Short by</dt><dd>${usd(t.difference)}</dd>
          </dl>
        </div>
        <div class="split">${side(byId.A, exp.results.A)}${side(byId.B, exp.results.B)}</div>
        <div class="runrow">
          <div class="seg" id="pbver" aria-label="Playbook version">
            <button data-v="1" aria-pressed="${version === "1"}">As induced</button>
            <button data-v="" aria-pressed="${version === ""}">After sign-off</button>
          </div>
          <button class="btn btn-secondary btn-sm" id="fresh">Run it live</button>
          <span class="note" id="freshNote">Showing the last saved run. A live run takes about 15 seconds and makes real model calls.</span>
        </div>
        <div id="control"></div>
      </div>`;
    document.getElementById("fresh").addEventListener("click", () => runFresh(clients));
    control(clients);
    document.querySelectorAll("#pbver button").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.v === version) return;
      version = b.dataset.v;
      try { render(clients, await get(route())); }
      catch (err) { document.getElementById("freshNote").textContent = "That playbook version has no saved run yet."; }
    }));
  }

  // Demo step 4: the amount ties exactly, so a matcher alone would clear it.
  // Code stops it because the payee account changed; no model gets a vote.
  async function control(clients) {
    const box = document.getElementById("control");
    try {
      const res = await fetch("/api/experiment/bank-change");
      if (!res.ok) return;
      const data = await res.json(), item = data.item || data, rec = item.record;
      const b = clients.find((c) => c.id === "B");
      box.innerHTML = `<div class="control-head"><div class="label">A control the model cannot talk its way past</div>
          <h2>The amount ties exactly. It still stops.</h2></div>
        <div class="panel txn"><div><span class="label">Bank line · ${day(rec.date)}</span><div class="desc">${esc(rec.description)}</div></div>
          <dl class="kv num"><dt>Paid out</dt><dd class="amt">${usd(Math.abs(rec.amount))}</dd><dt>Open payable</dt><dd>${usd(Math.abs(rec.amount))}</dd><dt>Difference</dt><dd>$0.00</dd></dl></div>
        ${side(b, item)}`;
    } catch (e) { /* server not running: the split screen stands on its own */ }
  }

  async function runFresh(clients) {
    const btn = document.getElementById("fresh"), note = document.getElementById("freshNote");
    btn.disabled = true;
    const started = Date.now();
    const tick = setInterval(() => { note.textContent = `Both agents are working the same bank line. ${Math.round((Date.now() - started) / 1000)}s`; }, 500);
    try {
      const res = await fetch(route() + (version ? "&" : "?") + "fresh=true");
      if (!res.ok) throw new Error("The live run failed (" + res.status + "). The saved run is still shown.");
      render(clients, await res.json());
    } catch (e) {
      note.textContent = e.message.includes("saved run") ? e.message : "Could not reach the server. The saved run is still shown.";
      btn.disabled = false;
    } finally { clearInterval(tick); }
  }

  try {
    const [clients, exp] = await Promise.all([get("/api/clients"), get("/api/experiment")]);
    render(clients, exp);
  } catch (e) {
    view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`;
  }
})();
