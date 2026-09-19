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
          <span class="runrow"><span class="state ${o.cls}">${esc(o.text)}</span>${SO.reasonPill(res)}</span>
        </div>
        <div class="panel-body verdict">
          <p class="line">${esc(verdict(res, chart))}</p>
          <div class="meta-row lead">
            <span>Tier <span class="tier">${esc(item.tier)}</span></span>
            <span>Cost <span class="${item.usage.cost_usd === 0 ? "cost-zero" : ""}">${cost(item.usage.cost_usd)}</span></span>
            <span>${item.usage.llm_calls} model call${item.usage.llm_calls === 1 ? "" : "s"}</span>
            ${item.playbook_version != null ? `<span>Playbook v${esc(item.playbook_version)}</span>` : ""}
            <span>Confidence ${Number(res.confidence).toFixed(2)}</span>
          </div>

          ${flags}
          ${reasoning(res)}
          ${ruleBlock(rule)}
          ${precedents.length ? `<div>
              <div class="label">Their own history</div>
              <div class="table-wrap"><table class="grid tight"><tbody>${precedents.slice(0, 3).map((p) => precedentRow(p, client.id)).join("")}</tbody></table></div>
              ${precedents.length > 3 ? `<div class="faint small">and ${precedents.length - 3} more</div>` : ""}
            </div>` : ""}
        </div>
      </article>`;
  }

  // GETs are cache-only on the server: a 404 means "not run yet", which is an empty state
  // with a Run button, never a reason to show fixtures. Only an unreachable server does that.
  // "" is the latest playbook (after the controller's answers and corrections); "1" is as induced.
  let version = "", busy = false, clients = [];
  const route = () => "/api/experiment" + (version ? "?version=" + version : "");
  const runBody = (which) => SO.body({ which, version: version ? Number(version) : null });

  async function fetchCached(path) {
    let res;
    try { res = await fetch(path, { headers: { accept: "application/json" } }); } catch (e) { return { down: true }; }
    if (res.status === 404) return { empty: true };
    if (!res.ok) return { failed: res.status };
    return { data: await res.json() };
  }

  function controls(note) {
    const off = SO.usedFixture ? "disabled" : "";
    return `<div class="runrow">
      <div class="seg" id="pbver" aria-label="Playbook version">
        <button data-v="1" aria-pressed="${version === "1"}" ${off}>As induced</button>
        <button data-v="" aria-pressed="${version === ""}" ${off}>After sign-off</button>
      </div>
      <button class="btn btn-secondary btn-sm" id="fresh" ${off}>Run it live</button>
      <span class="note" id="freshNote">${esc(note)}</span>
    </div>`;
  }

  function wire() {
    document.getElementById("fresh").addEventListener("click", () => runLive("same_transaction"));
    document.querySelectorAll("#pbver button").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.v === version || busy) return;
      version = b.dataset.v;
      show();
    }));
  }

  function render(exp, live) {
    const byId = Object.fromEntries(clients.map((c) => [c.id, c]));
    const t = exp.transaction;
    const note = SO.usedFixture ? "Saved example. Start the server to switch playbook versions or run it live."
      : live ? "This is a live run, finished just now." : "Showing the last saved run. A live run takes about 15 seconds and makes real model calls.";
    view.innerHTML = `
      <div class="screen">
        <div class="panel txn">
          <div class="txn-left"><div><span class="label">Bank line · ${day(t.date)}</span><div class="desc">${esc(t.description)}</div></div>
            ${controls(note)}</div>
          <dl class="kv num">
            <dt>Received</dt><dd class="amt">${usd(t.amount)}</dd>
            <dt>Invoice</dt><dd>${usd(t.invoice_amount)}</dd>
            <dt>Short by</dt><dd>${usd(t.difference)}</dd>
          </dl>
        </div>
        <div class="split">${side(byId.A, exp.results.A)}${side(byId.B, exp.results.B)}</div>
        <div id="control"></div>
      </div>`;
    wire();
    control();
  }

  function renderEmpty(message) {
    view.innerHTML = `<div class="screen">
      <div class="panel empty"><h3>${esc(message)}</h3><p class="muted">Both agents will work the same bank line, one per client. About 15 seconds, real model calls.</p></div>
      ${controls("Nothing saved for this playbook version yet.")}
      <div id="control"></div></div>`;
    wire();
    control();
  }

  async function show() {
    const r = await fetchCached(route());
    if (r.data) return render(r.data);
    if (r.empty) return renderEmpty(version ? "The as-induced playbook has not been run on this payment yet." : "This payment has not been run yet.");
    if (r.down && !version) return render(await get("/api/experiment"));   // server unreachable: saved example, with the banner
    view.innerHTML = `<div class="panel error">The server answered with an error${r.failed ? " (" + r.failed + ")" : ""}. Reload once it is back.</div>`;
  }

  // Demo step 4: the amount ties exactly, so a matcher alone would clear it.
  // Code stops it because the payee account changed; no model gets a vote.
  async function control(item) {
    const box = document.getElementById("control");
    if (!box || SO.usedFixture) return;
    const head = `<div class="control-head"><div class="label">A control the model cannot talk its way past</div><h2>The amount ties exactly. It still stops.</h2></div>`;
    if (!item) {
      const r = await fetchCached("/api/experiment/bank-change");
      if (r.empty) {
        box.innerHTML = `${head}<div class="panel empty"><h3>The vendor bank change has not been run yet.</h3>
          <div class="runrow"><button class="btn btn-secondary btn-sm" id="runControl">Run it</button><span class="note" id="controlNote">About 15 seconds, one model call.</span></div></div>`;
        document.getElementById("runControl").addEventListener("click", () => runLive("bank_change"));
        return;
      }
      if (!r.data) return;
      item = r.data.item || r.data;
    }
    const rec = item.record, b = clients.find((c) => c.id === "B");
    box.innerHTML = `${head}
      <div class="panel txn"><div><span class="label">Bank line · ${day(rec.date)}</span><div class="desc">${esc(rec.description)}</div></div>
        <dl class="kv num"><dt>Paid out</dt><dd class="amt">${usd(Math.abs(rec.amount))}</dd><dt>Open payable</dt><dd>${usd(Math.abs(rec.amount))}</dd><dt>Difference</dt><dd>$0.00</dd></dl></div>
      ${side(b, item)}`;
    if (location.hash === "#control") box.scrollIntoView();
  }

  // one pending state for anything that runs the agents: ticker on, controls off
  function working(noteId, message) {
    busy = true;
    const note = document.getElementById(noteId), started = Date.now();
    const buttons = document.querySelectorAll("#fresh, #pbver button, #runControl");
    buttons.forEach((c) => { c.disabled = true; });
    note.textContent = message;
    const tick = setInterval(() => { note.textContent = `${message} ${Math.round((Date.now() - started) / 1000)}s`; }, 500);
    return (failure) => {
      busy = false; clearInterval(tick);
      buttons.forEach((c) => { c.disabled = false; });
      if (failure) note.textContent = failure;
    };
  }

  async function runLive(which) {
    if (busy) return;
    const same = which === "same_transaction";
    const done = working(same ? "freshNote" : "controlNote", same ? "Both agents are working the same bank line." : "The agent is working the payment.");
    try {
      const res = await fetch("/api/experiment/run", { method: "POST", headers: { "content-type": "application/json" }, body: runBody(which) });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      done();
      if (same) render(data, true); else control(data.item || data);
    } catch (e) {
      done("The live run did not finish. What was on screen before is unchanged.");
    }
  }

  try {
    clients = await get("/api/clients");
    await show();
  } catch (e) {
    view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`;
  }
})();
