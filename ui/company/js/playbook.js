/* Step 3. Check the history can teach something, say what it will cost, then induce.

   The preflight is not decoration. Induction on a history with no record of any judgement call
   costs real money and returns a playbook of rules that cite nothing, so it is refused with the
   reason rather than allowed to disappoint expensively. */
(function () {
  const view = document.getElementById("view");
  const esc = SO.esc;
  let company = null, pre = null, pb = null;

  async function load() {
    company = await CO.get("/api/onboarding/company");
    if (!company.created) {
      view.innerHTML = CO.empty("No company yet", 'Start at <a href="setup.html">Setup</a>.');
      return false;
    }
    pre = await CO.get("/api/onboarding/preflight");
    // Only ask for the playbook once we know there is one, so the first visit does not
    // log a 404 for something that is simply not written yet.
    pb = company.has_playbook ? await CO.get(`/api/playbook/${company.client}`) : null;
    return true;
  }

  function readiness() {
    const est = pre.estimate || {};
    return `<section class="panel">
      <div class="panel-head"><h3>Before we start</h3>
        <span class="faint">${pre.cases} exceptions in ${pre.clusters} recurring kinds</span></div>
      <div class="panel-body">
        ${CO.checks(pre.checks)}
        <div class="actions" style="margin-top:16px">
          <button class="btn btn-primary" id="go" ${pre.ready ? "" : "disabled"}>
            ${pb ? "Learn again" : "Write my playbook"}</button>
          <span class="faint">${est.llm_calls} model calls, about ${esc(String(est.usd))} and
            ${Math.round((est.seconds || 120) / 60)} minutes</span>
          ${pre.ready ? "" : `<a class="btn btn-secondary" href="import.html">Fix it in your history</a>`}
        </div>
        ${pre.ready ? "" : `<p class="faint" style="margin-top:8px">We will not spend anything until
          the blocking items above are sorted. Everything else is a warning: it will still work,
          it will just know less than you might expect.</p>`}
        <div id="job"></div>
      </div>
    </section>`;
  }

  function rulesPanel() {
    if (!pb || !pb.rules) return "";
    const approved = pb.rules.filter((r) => r.status === "approved");
    const proposed = pb.rules.filter((r) => r.status === "proposed");
    return `
      <div class="stat-row">
        <div class="stat"><b>${approved.length}</b><span>rules that run on their own</span></div>
        <div class="stat"><b>${proposed.length}</b><span>waiting on an answer</span></div>
        <div class="stat"><b>v${pb.version}</b><span>version</span></div>
      </div>
      ${section("Supported by your history", approved, "These agree with enough of your own history to act without asking.")}
      ${section("Questions for you", proposed, "We saw too little, or saw it handled two ways. Answering one of these is usually worth more than any amount of extra history.")}
      <div class="actions">
        <a class="btn btn-secondary" href="../playbook.html?client=${encodeURIComponent(company.client)}">Open the full playbook screen</a>
        <a class="btn btn-primary" href="receipts.html">Next: upload new receipts</a>
      </div>`;
  }

  function section(title, rules, note) {
    if (!rules.length) return "";
    return `<section class="panel">
      <div class="panel-head"><h3>${esc(title)}</h3><span class="faint">${rules.length}</span></div>
      <div class="panel-body">
        <p class="faint">${esc(note)}</p>
        ${rules.map((r) => `<div class="rule-text">
          ${SO.ruleBlock ? SO.ruleBlock(r) : esc(r.text)}
          ${r.open_question ? `<div class="note">${esc(r.open_question)}</div>` : ""}
        </div>`).join("")}
      </div>
    </section>`;
  }

  async function induce() {
    const box = document.getElementById("job");
    const btn = document.getElementById("go");
    btn.disabled = true;
    box.innerHTML = `<div class="note"><span class="spin"></span> Starting...</div>`;
    try {
      const job = await CO.post("/api/onboarding/induce", {});
      const done = await CO.watch(job.job_id, (r) => { box.innerHTML = CO.jobLine(r); });
      if (done.state !== "done") throw new Error(done.error || "that did not finish");
      const r = done.result;
      box.innerHTML = `<div class="note good">Wrote ${r.rules} rules: ${r.approved} run on their own,
        ${r.open_questions} have a question for you. Cost ${SO.cost(r.usage.cost_usd)}.</div>`;
      await load();
      draw();
    } catch (e) {
      btn.disabled = false;
      box.innerHTML = CO.err(e);
    }
  }

  function draw() {
    view.innerHTML = readiness() + rulesPanel();
    const go = document.getElementById("go");
    if (go) go.addEventListener("click", induce);
  }

  load().then((ok) => ok && draw()).catch((e) => { view.innerHTML = CO.err(e); });
})();
