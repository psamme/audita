/* Step 4. Run a period, then work the queue.

   The estimate is free and worth having: the deterministic tiers run without the model and tell us
   exactly how many items actually need it, so the cost shown before the button is measured rather
   than guessed. */
(function () {
  const view = document.getElementById("view");
  const esc = SO.esc;
  let company = null, est = null, run = null, queue = [];

  async function load() {
    company = await CO.get("/api/onboarding/company");
    if (!company.created) {
      view.innerHTML = CO.empty("No company yet", 'Start at <a href="setup.html">Setup</a>.');
      return false;
    }
    return true;
  }

  function periodOptions() {
    const ps = company.periods || [];
    if (!ps.length) return `<option value="">nothing imported yet</option>`;
    return ps.map((p, i) => `<option value="${p}" ${i === ps.length - 1 ? "selected" : ""}>${SO.period ? SO.period(p) : p}</option>`).join("");
  }

  function controls() {
    return `<section class="panel">
      <div class="panel-head"><h3>Which period</h3></div>
      <div class="panel-body">
        <div class="actions">
          <select class="select" id="period" style="max-width:14rem">${periodOptions()}</select>
          <button class="btn btn-secondary" id="estimate">What will this cost?</button>
          <button class="btn btn-primary" id="go" disabled>Reconcile it</button>
        </div>
        <p class="faint" style="margin-top:8px">To bring in a new month, add its statement and ledger
          on the <a href="import.html">history</a> screen. Leave the reconciliation file out: deciding
          is the part we are doing here.</p>
        <div id="est"></div>
        <div id="job"></div>
      </div>
    </section>`;
  }

  function tierBar(t, n) {
    const seg = (k, label) => t[k] ? `<span class="t-${k}" style="width:${(100 * t[k] / n).toFixed(1)}%" title="${label}: ${t[k]}"></span>` : "";
    return `<div class="tiers">
      <div class="tier-bar">${seg("matcher", "Matched outright")}${seg("rule", "Your playbook")}${seg("investigator", "Worked by the model")}${seg("guardrail", "Held by a control")}</div>
      <div class="tier-key">
        <span><i style="background:var(--ink)"></i>matched outright ${t.matcher || 0}</span>
        <span><i style="background:var(--ink-3)"></i>your playbook ${t.rule || 0}</span>
        <span><i style="background:var(--surface-2)"></i>worked by the model ${t.investigator || 0}</span>
        ${t.guardrail ? `<span><i style="background:var(--neg-wash)"></i>held by a control ${t.guardrail}</span>` : ""}
      </div>
    </div>`;
  }

  function estimatePanel() {
    if (!est) return "";
    const free = est.n_items - est.needs_model;
    return `<div class="note good" style="display:block">
      ${est.n_items.toLocaleString()} items. ${free.toLocaleString()} of them
      (${Math.round(100 * free / Math.max(1, est.n_items))}%) clear with no model call at all.
      ${est.needs_model.toLocaleString()} need the model: about ${SO.cost(est.est_usd)} and
      ${Math.round(est.est_seconds / 60) || 1} minute(s).
      ${tierBar(est.tiers, est.n_items)}
    </div>`;
  }

  function resultPanel() {
    if (!run) return "";
    const t = run.tiers || {};
    const reasons = run.escalation_reasons || {};
    const named = Object.entries(reasons).filter(([, v]) => v);
    return `<section class="panel">
      <div class="panel-head"><h3>${esc(run.period)}</h3><span class="faint">${esc(run.run_id)}</span></div>
      <div class="panel-body">
        <div class="stat-row">
          <div class="stat"><b>${run.n_items}</b><span>items</span></div>
          <div class="stat"><b>${run.n_items - run.escalated}</b><span>settled</span></div>
          <div class="stat"><b>${run.escalated}</b><span>for you to look at</span></div>
          <div class="stat"><b>${SO.cost(run.cost_usd)}</b><span>spent</span></div>
        </div>
        ${tierBar(t, run.n_items)}
        ${named.length ? `<p class="faint">Sent to you because:
          ${named.map(([k, v]) => `${v} ${esc(k.replace(/_/g, " "))}`).join(", ")}.</p>` : ""}
        <div class="actions">
          <a class="btn btn-primary" href="../queue.html?run=${encodeURIComponent(run.run_id)}">
            Work the queue (${run.escalated})</a>
          <a class="btn btn-secondary" href="../playbook.html?client=${encodeURIComponent(company.client)}">See the playbook</a>
        </div>
        <p class="faint" style="margin-top:8px">Resolving an item in the queue also teaches the
          playbook, so the next month of the same thing does not come back to you.</p>
      </div>
    </section>`;
  }

  function queuePanel() {
    if (!queue.length) return "";
    return `<section class="panel">
      <div class="panel-head"><h3>What it would not decide alone</h3><span class="faint">${queue.length}</span></div>
      <div class="panel-body"><div class="table-wrap"><table class="grid tight">
        <thead><tr><th>Date</th><th>Amount</th><th>What it is</th><th>Why it stopped</th><th>To</th></tr></thead>
        <tbody>${queue.slice(0, 12).map((it) => `<tr class="r">
          <td>${SO.day(it.record.date)}</td>
          <td class="num">${SO.usd(it.record.amount)}</td>
          <td class="wrap">${esc((it.record.description || it.record.memo || "").slice(0, 60))}</td>
          <td>${SO.reasonPill ? SO.reasonPill(it.resolution) : esc(it.resolution.reason || "")}</td>
          <td>${esc(SO.role ? SO.role(it.resolution.escalate_to) : (it.resolution.escalate_to || ""))}</td>
        </tr>`).join("")}</tbody></table></div>
        ${queue.length > 12 ? `<p class="faint">${queue.length - 12} more in the queue screen.</p>` : ""}
      </div></section>`;
  }

  async function doEstimate() {
    const box = document.getElementById("est");
    const period = document.getElementById("period").value;
    if (!period) return;
    box.innerHTML = `<div class="note"><span class="spin"></span> Running the free tiers...</div>`;
    try {
      est = await CO.post("/api/onboarding/reconcile/estimate", { period });
      draw();
      document.getElementById("go").disabled = false;
    } catch (e) { box.innerHTML = CO.err(e); }
  }

  async function doRun() {
    const box = document.getElementById("job");
    const period = document.getElementById("period").value;
    document.getElementById("go").disabled = true;
    box.innerHTML = `<div class="note"><span class="spin"></span> Starting...</div>`;
    try {
      const job = await CO.post("/api/onboarding/reconcile", { period });
      const done = await CO.watch(job.job_id, (r) => { box.innerHTML = CO.jobLine(r); });
      if (done.state !== "done") throw new Error(done.error || "that did not finish");
      run = done.result;
      queue = await SO.get(`/api/runs/${run.run_id}/queue`);
      draw();
    } catch (e) {
      box.innerHTML = CO.err(e);
      document.getElementById("go").disabled = false;
    }
  }

  function draw() {
    view.innerHTML = controls() + resultPanel() + queuePanel();
    const e = document.getElementById("est");
    if (e) e.innerHTML = estimatePanel();
    document.getElementById("estimate").addEventListener("click", doEstimate);
    const go = document.getElementById("go");
    go.disabled = !est;
    go.addEventListener("click", doRun);
  }

  load().then((ok) => ok && draw()).catch((err) => { view.innerHTML = CO.err(err); });
})();
