/* Screen 2: the human review queue for one run, the evidence trace for one item,
   and the correction form. A correction POSTs /api/corrections (10 to 25 s, one
   model call) and comes back as a playbook diff plus the items it now clears. */
(async function () {
  const { get, usd, cost, day, esc, role, cap, period, reasonPill, bandBar, outcome, verdict, cite, reasoning, ruleBlock, diffBlock } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary"), runSel = document.getElementById("run");
  let clients = {}, run = null, queue = [], current = null, resultHtml = "";
  // Grades come from the answer key. They stay off screen unless the presenter asks (?grades=1),
  // and even then they sit in their own marked block, never beside the agent's own output.
  const showGrades = new URLSearchParams(location.search).get("grades") === "1";

  const text = (rec) => rec.description || rec.memo || rec.id;
  const TRACE_WORDS = {
    case_file: "Assembled the case file", search_bank: "Searched the bank feed", search_ledger: "Searched the ledger",
    search_documents: "Searched documents", find_precedents: "Looked for how this was handled before", get_record: "Opened a record",
  };
  function traceStep(t, client) {
    const isFinal = t.kind === "final";
    const what = isFinal ? "Decided: " + t.label.replace(/_/g, " ") : TRACE_WORDS[t.label] || TRACE_WORDS[t.kind] || cap(String(t.label).replace(/_/g, " "));
    const input = Object.entries(t.input || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ");
    const ids = (t.ids || []).slice(0, 8).map((id) => cite(client, id)).join(" ");
    const more = (t.ids || []).length > 8 ? ` <span class="faint small">and ${(t.ids || []).length - 8} more</span>` : "";
    return `<li class="${isFinal ? "final" : ""}"><span class="step">${esc(t.step)}</span><div>
      <div class="what">${esc(what)}${t.kind === "case_file" || t.kind === "matcher" || t.kind === "rule" || t.kind === "guardrail" ? ` <span class="faint small">no model call</span>` : ""}</div>
      ${input ? `<div class="out mono">${esc(input)}</div>` : ""}
      ${!isFinal && t.output ? `<div class="out">${esc(t.output)}</div>` : ""}
      ${ids ? `<div class="cites">${ids}${more}</div>` : ""}
    </div></li>`;
  }

  function listRow(it) {
    const o = outcome(it.resolution), sel = current && current.item_id === it.item_id;
    return `<button class="qrow" data-id="${esc(it.item_id)}" aria-pressed="${sel}">
      <span class="qtop"><span class="qdesc">${esc(text(it.record))}</span><span class="num">${usd(it.record.amount)}</span></span>
      <span class="qsub"><span class="mono">${esc(it.item_id)}</span><span>${day(it.record.date)}</span><span>${esc(o.short)}</span><span class="${it.usage.cost_usd === 0 ? "cost-zero" : ""}">${cost(it.usage.cost_usd)}</span></span>
    </button>`;
  }

  function correctionForm(it, chart) {
    const start = it.resolution.proposed || it.resolution;
    const accounts = Object.entries(chart).map(([a, n]) => `<option value="${esc(a)}">${esc(a)} ${esc(n)}</option>`).join("");
    const roles = ["owner", "controller", "ar_lead", "ap_lead", "ops_manager"].map((r) => `<option value="${r}">${esc(cap(role(r).replace(/^the /, "")))}</option>`).join("");
    return `<form id="correct" class="panel">
      <div class="panel-head"><h3>Correct this</h3><span class="faint small">Your answer becomes a playbook change you can read before it applies anywhere else.</span></div>
      <div class="panel-body formgrid">
        <div class="ctl"><label class="label" for="c-action">What should happen</label>
          <select class="select" id="c-action">
            <option value="match">Match it</option><option value="match_adjust">Match and book the difference</option>
            <option value="book">Book it</option><option value="carry_forward">Carry it forward</option><option value="escalate">Send it to a person</option>
          </select></div>
        <div class="ctl" data-for="match match_adjust"><label class="label" for="c-ledger">Ledger entries</label>
          <input class="input mono" id="c-ledger" value="${esc((start.ledger_ids || []).join(", "))}" placeholder="B-LE-00322, B-LE-00323"></div>
        <div class="ctl" data-for="match_adjust book"><label class="label" for="c-account">Account</label><select class="select" id="c-account">${accounts}</select></div>
        <div class="ctl" data-for="match_adjust book"><label class="label" for="c-amount">Amount</label><input class="input num" id="c-amount" inputmode="decimal" placeholder="0.00"></div>
        <div class="ctl" data-for="escalate"><label class="label" for="c-role">Send to</label><select class="select" id="c-role">${roles}</select></div>
        <div class="ctl wide"><label class="label" for="c-note">Why, in your words</label>
          <textarea class="input" id="c-note" rows="2" placeholder="Quarry always nets their wire fee and a 3% volume rebate. Book the rebate to 4050."></textarea></div>
        <div class="wide runrow"><button class="btn btn-primary" id="c-send">Send correction</button><span class="note" id="c-note-status">Takes 10 to 25 seconds. One model call, then a back-test against history.</span></div>
      </div>
    </form>`;
  }

  function wireForm(it) {
    const form = document.getElementById("correct"), action = document.getElementById("c-action");
    const sync = () => form.querySelectorAll("[data-for]").forEach((el) => { el.hidden = !el.dataset.for.split(" ").includes(action.value); });
    action.value = "match_adjust"; sync();
    action.addEventListener("change", sync);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const a = action.value, amt = parseFloat(document.getElementById("c-amount").value);
      const needsAmount = a === "match_adjust" || a === "book";
      const status = document.getElementById("c-note-status"), btn = document.getElementById("c-send");
      if (needsAmount && !(Math.abs(amt) > 0)) { status.textContent = "Enter the amount to book."; return; }
      const resolution = {
        action: a,
        ledger_ids: a === "match" || a === "match_adjust" ? document.getElementById("c-ledger").value.split(/[\s,]+/).filter(Boolean) : [],
        adjustments: needsAmount ? [{ account: document.getElementById("c-account").value, amount: amt }] : [],
        escalate_to: a === "escalate" ? document.getElementById("c-role").value : null,
        rationale: document.getElementById("c-note").value, rule_id: null, precedent_ids: [], evidence_ids: [], confidence: 1,
      };
      btn.disabled = true;
      const t0 = Date.now(), tick = setInterval(() => { status.textContent = `Rewriting the playbook and replaying history. ${Math.round((Date.now() - t0) / 1000)}s`; }, 500);
      try {
        const res = await fetch("/api/corrections", { method: "POST", headers: { "content-type": "application/json" },
          body: SO.body({ client: run.client, run_id: run.run_id, item_id: it.item_id, resolution, note: resolution.rationale }) });
        if (!res.ok) throw new Error(String(res.status));
        const result = await res.json();
        // the corrected item and anything the new rule cleared leave the queue
        const gone = new Set([it.item_id, ...(result.reran || []).map((x) => x.item_id)]);
        queue = queue.filter((x) => !gone.has(x.item_id));
        current = queue[0] || null;
        showResult(result, it);
      } catch (err) {
        status.textContent = "No result came back. The correction may already be logged, so check the playbook's versions before sending it again.";
        btn.disabled = false;
      } finally { clearInterval(tick); }
    });
  }

  function showResult(r, corrected) {
    const reran = r.reran || [];
    resultHtml = `<div class="panel result">
      <div class="panel-head"><h3>${esc(text(corrected.record))} is corrected. ${r.diff ? `The playbook is now version ${esc(r.new_version)}` : "The playbook did not need to change"}.</h3><span class="mono faint">${esc(r.correction_id)}</span></div>
      <div class="panel-body stack">
        <p>${esc(r.explanation || "")}</p>
        ${diffBlock(r.diff)}
        ${r.check ? `<div class="rule-text"><span class="label">Checked by code, not by the model</span><div>${esc(cap(r.check))}</div></div>` : ""}
        ${reran.length ? `<div><div class="label">Now cleared by the new rule, at $0.00</div>
          <div class="table-wrap"><table class="grid tight"><tbody>${reran.map((x) => `<tr><td>${cite(run.client, x.item_id)}</td><td class="wrap">${esc(text(x.record))}</td><td class="r">${usd(x.record.amount)}</td><td>${esc(outcome(x.resolution).text)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
      </div></div>`;
    paint();
  }

  function detail(it) {
    const c = clients[run.client] || { chart: {} }, res = it.resolution, o = outcome(res);
    const flags = (it.control_flags || []).map((f) => `<div class="rule-text"><span class="label">Control, enforced by code</span><div>${esc(cap(f.detail || f.flag))}</div></div>`).join("");
    const proposed = res.proposed ? `<div class="rule-text"><span class="label">What the agent wanted to do</span><div>${esc(verdict(res.proposed, c.chart))}</div></div>` : "";
    const graded = showGrades && it.grade ? `<div class="rule-text graded"><span class="label">Grader, from the answer key. The agent never sees this.</span>
      <div>${it.grade.correct ? "Marked correct." : "Marked wrong."}${!it.grade.correct && it.grade.key_action ? " The key's action was " + esc(it.grade.key_action.replace(/_/g, " ")) + "." : ""}</div></div>` : "";
    return `<div class="stack">
      <div class="panel">
        <div class="panel-head"><div><h3>${esc(text(it.record))}</h3><span class="sub faint">${cite(run.client, it.item_id)} · ${day(it.record.date)} · ${esc(it.record.counterparty || "")}</span></div>
          <div class="amt2 num">${usd(it.record.amount)}</div></div>
        <div class="panel-body verdict">
          <div class="rule-top"><p class="line">${esc(verdict(res, c.chart))}</p><span class="runrow"><span class="state ${o.cls}">${esc(o.text)}</span>${reasonPill(res)}</span></div>
          ${it.band ? `<div class="rule-text"><span class="label">Between what history shows handled this way and what it shows handled another way</span>${bandBar({ side: it.band.side || "upper" /* items made before the field existed */, lo: it.band.lo, hi: it.band.hi, lo_precedent: it.band.lo_precedent, hi_precedent: it.band.hi_precedent }, { client: run.client, value: it.band.value })}</div>` : ""}
          ${flags}${reasoning(res)}${proposed}${it.rule ? ruleBlock(it.rule) : res.rule_id ? `<div class="faint small">Cites playbook rule <span class="cite">${esc(res.rule_id)}</span></div>` : ""}
          <div class="meta-row"><span>Tier <span class="tier">${esc(it.tier)}</span></span><span>Cost ${cost(it.usage.cost_usd)}</span><span>${it.usage.llm_calls} model call${it.usage.llm_calls === 1 ? "" : "s"}</span><span>Confidence ${Number(res.confidence).toFixed(2)}</span></div>
          ${graded}
        </div>
      </div>
      <div class="panel"><div class="panel-head"><h3>Evidence trace</h3><span class="faint small">Every step the agent took, in order</span></div>
        <div class="panel-body"><ol class="trace">${it.trace.map((t) => traceStep(t, run.client)).join("")}</ol></div></div>
      ${correctionForm(it, c.chart || {})}
    </div>`;
  }

  function paint() {
    if (!queue.length) { view.innerHTML = `${resultHtml}<div class="panel error">Nothing in this run is waiting on a person.</div>`; return; }
    view.innerHTML = `${resultHtml}<div class="qlayout"><div class="panel qlist">${queue.map(listRow).join("")}</div><div id="detail">${detail(current)}</div></div>`;
    view.querySelectorAll(".qrow").forEach((b) => b.addEventListener("click", () => {
      current = queue.find((q) => q.item_id === b.dataset.id); paint();
      if (matchMedia("(max-width: 900px)").matches) document.getElementById("detail").scrollIntoView({ behavior: "smooth" });
    }));
    wireForm(current);
  }

  async function load(runId) {
    resultHtml = "";
    view.innerHTML = `<div class="panel error">Loading the queue</div>`;
    const runs = await get("/api/runs");
    run = runs.find((r) => r.run_id === runId);
    if (!run) throw new Error("That run is not on the server any more. Pick another one.");
    queue = await get(`/api/runs/${runId}/queue` + (showGrades ? "?grades=true" : ""));
    current = queue[0] || null;
    const t = run.tiers, free = t.matcher + t.guardrail + t.rule;
    summary.innerHTML = `<span><b class="num">${run.n_items}</b> items</span><span><b class="num">${free}</b> cleared by code at $0.00</span><span><b class="num">${t.investigator}</b> worked by the investigator</span><span><b class="num">${queue.length}</b> sent to a person</span><span>Run cost <b class="num">${cost(run.cost_usd)}</b></span>${run.label ? `<span class="state state-proposed">${esc(cap(run.label))}</span>` : ""}`;
    paint();
  }

  const fail = (e) => { summary.innerHTML = ""; view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`; };

  try {
    const [cl, runs] = await Promise.all([get("/api/clients"), get("/api/runs")]);
    clients = Object.fromEntries(cl.map((c) => [c.id, c]));
    const usable = runs.filter((r) => r.n_items > 1);
    runSel.innerHTML = usable.map((r) => `<option value="${esc(r.run_id)}">${esc((clients[r.client] || {}).name || r.client)} · ${esc(period(r.period))} · ${esc(r.condition.replace(/_/g, " "))}${r.cost_usd === 0 ? " · no model" : ""}</option>`).join("");
    const want = new URLSearchParams(location.search).get("run");
    const first = usable.find((r) => r.run_id === want) || usable.find((r) => r.condition === "playbook" && r.cost_usd > 0) || usable[0];
    if (!first) throw new Error("No runs yet. Start one with the build session, then reload.");
    runSel.value = first.run_id;
    runSel.addEventListener("change", () => load(runSel.value).catch(fail));
    await load(first.run_id);
  } catch (e) { fail(e); }
})();
