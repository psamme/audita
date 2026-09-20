/* Screen 3: the playbook as sentences a controller can read and sign.
   Proposed rules carry the question the agent wants answered; answering one
   POSTs /api/playbook/answer and comes back as a diff. */
(async function () {
  const { get, esc, cap, usd, day, cite, diffBlock, bandBar } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary");
  const clientSeg = document.getElementById("client"), verSel = document.getElementById("version");
  let clients = [], client = new URLSearchParams(location.search).get("client") || "A", pb = null, openId = null;
  let bandQs = [], inputs = [], bandResult = "", undoResult = "", pendingPreview = null;

  const CAUSE = { induction: "Induced from the ERP trail", correction: "A reviewer's correction", interview: "An answered question",
    retraction: "An input was undone", one_off_exception: "A one-off exception was recorded", band: "A yes or no on a band" };
  const when = (iso) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  function evidence(r) {
    const bt = r.backtest || {}, n = (bt.support || 0) + (bt.conflicts || 0);
    const ids = (r.precedent_ids || []).slice(0, 5).map((id) => cite(client, id)).join(" ");
    const more = (r.precedent_ids || []).length > 5 ? ` <span class="faint small">and ${r.precedent_ids.length - 5} more</span>` : "";
    return `<div class="rule-meta">
      <span class="cite">${esc(r.id)}</span>
      ${n ? `<span>Agrees with ${bt.support} of ${n} past case${n === 1 ? "" : "s"}</span>` : `<span>No past cases in the trail</span>`}
      ${r.confidence != null ? `<span>Confidence ${Number(r.confidence).toFixed(2)}</span>` : ""}
      ${r.below_floor ? `<span class="state state-carry">Not running</span>` : r.executable ? `<span>Runs as code at $0.00</span>` : `<span>Guidance for the investigator</span>`}
      ${r.valid_from ? `<span>Counts from ${day(r.valid_from)}</span>` : ""}
      ${r.human_confirmed ? `<span class="state">Confirmed by a person</span>` : ""}
    </div>${r.floor_exempt ? `<div class="faint small">${esc(r.floor_exempt)}</div>` : ""}${ids ? `<div class="cites">${ids}${more}</div>` : ""}${bands(r)}`;
  }

  // condition keys look like amount_max, diff_min, doc.net_diff_abs_max: name the quantity, not the key
  const condLabel = (k) => (/pct/.test(k) ? "Difference, % of invoice" : /diff/.test(k) ? "Difference" : /amount/.test(k) ? "Amount" : /day/.test(k) ? "Days" : cap(k.replace(/[._]/g, " ")));
  function bands(r) {
    return Object.entries(r.bands || {}).filter(([, b]) => b.source !== "rejected").map(([cond, b]) => `<div class="band-wrap"><div class="label">${esc(condLabel(cond))}${b.source === "interview" ? " · narrowed by an answer" : b.source === "stated" ? " · stated by a person" : ""}</div>${bandBar(b, { client, dormant: !!r.below_floor })}</div>`).join("");
  }

  // The stage moment: one question about one band, four possible answers, no model call.
  // A real controller knows their number, so stating the limit leads whenever the band is open ended.
  // The person on stage answers as the client's senior role; a non-senior answer is recorded but held.
  const seniorRole = () => (clients.find((c) => c.id === client)?.senior_roles || [])[0] || "";
  // ?role=bookkeeper answers as someone junior, to show an answer being recorded but held. No picker on
  // the card: the stage path stays one click.
  const asRole = () => { const r = new URLSearchParams(location.search).get("role"); return r && /^[a-z_]{2,24}$/.test(r) ? r : seniorRole(); };
  function bandCard() {
    const q = bandQs[0];
    if (!q) return "";
    const rule = pb.rules.find((r) => r.id === q.rule_id), b = (rule && rule.bands && rule.bands[q.condition]) || { side: "upper", lo: q.lo, hi: q.hi };
    const can = (k) => !q.answers || q.answers.includes(k), limitFirst = q.ask === "limit";
    const text = q.text.startsWith(q.rule_text) ? q.text.slice(q.rule_text.length).trim() : q.text;
    const limit = can("limit") ? `<form class="limit" id="limitForm"><label for="limitValue">The limit is</label>
        <span class="money"><span>$</span><input class="input num" id="limitValue" inputmode="decimal" autocomplete="off" placeholder="${Number(q.value).toFixed(2)}"></span>
        <button class="btn ${limitFirst ? "btn-primary" : "btn-secondary"}">Set the limit</button></form>` : "";
    const yesNo = `<div class="runrow">
        ${can("review") ? `<button class="btn ${limitFirst ? "btn-secondary" : "btn-primary"}" data-answer="review">Yes, review one at ${usd(q.value)}</button>` : ""}
        ${can("usual") ? `<button class="btn btn-secondary" data-answer="usual">No, ${usd(q.value)} is handled as usual</button>` : ""}
      </div>`;
    return `<section class="panel ask"><div class="panel-body stack">
      <div class="label">One question · ${bandQs.length} band${bandQs.length === 1 ? "" : "s"} still open, widest first · answering as ${esc(SO.role(asRole()))}</div>
      <h2 class="ask-q">${esc(text)}</h2>
      <div id="askBand">${bandBar(b, { client, value: q.value })}${b.categorical ? `<span class="state state-proposed" style="margin-top: 8px;">Looks like a fee schedule</span>` : ""}</div>
      <div class="answers">${limitFirst ? limit + yesNo : yesNo + limit}
        ${can("not_amount") ? `<button class="btn btn-ghost" data-answer="not_amount">It is not about the amount</button>` : ""}</div>
      <label class="label" for="previewPeriod">Preview period</label><input class="input" id="previewPeriod" type="month" value="${esc(new URLSearchParams(location.search).get("period") || pb.trained_before || "")}"><span class="note" id="askNote">Preview the effect before applying. No model call.</span>
      <div class="rule-text"><span class="label">The rule this belongs to</span><div>${esc(q.rule_text)} <span class="cite">${esc(q.rule_id)}</span></div></div>
    </div></section>`;
  }

  async function answerBand(kind, amount) {
    undoResult = "";
    const q = bandQs[0], note = document.getElementById("askNote");
    const controls = document.querySelectorAll(".ask button, .ask input");
    const targetClient = client;
    const period = document.getElementById("previewPeriod").value;
    const role = asRole();
    if (!period) { note.textContent = "Choose a period to preview."; return; }
    controls.forEach((c) => { c.disabled = true; });
    note.textContent = "Comparing the current and proposed policy. Nothing has been saved.";
    try {
      const res = await fetch("/api/playbook/preview-band", { method: "POST", headers: { "content-type": "application/json" },
        body: SO.body({ client, rule_id: q.rule_id, condition: q.condition, value: q.value, answer: kind, role, period,
          limit: kind === "limit" ? amount : null }) });
      const r = await res.json();
      if (client !== targetClient) return;
      if (!res.ok) throw new Error(r.detail || String(res.status));
      if (r.held) { note.textContent = r.held; controls.forEach((c) => { c.disabled = false; }); return; }
      bandResult = `<section class="panel" id="policyPreview"><div class="panel-head"><h3>Review the effect before applying</h3><span class="state state-proposed">Not saved</span></div>
        <div class="panel-body stack"><p>Period ${esc(r.period)} · version ${r.version} · ${r.before.bank_exceptions} bank exceptions checked</p>
        <h2 class="ask-q">${r.before.automatic_exceptions} → ${r.after.automatic_exceptions} automatic exceptions</h2>
        <p>${r.after.needs_review} bank items still need review. ${r.changed_items.length} decisions change.</p>
        <p class="muted">${esc(r.scope_note)} ${esc(r.accuracy_note)}</p>
        ${diffBlock(r.diff, { client })}
        ${r.changed_items.length ? `<div class="table-wrap"><table class="grid tight"><thead><tr><th>Item</th><th>Before</th><th>After</th></tr></thead><tbody>${r.changed_items.map((x) => `<tr><td>${cite(client, x.item_id)}</td><td>${esc(x.before?.action || (x.before_claimed_by ? "Cleared by " + x.before_claimed_by : "No separate item"))} ${esc((x.before?.adjustments || []).map((a) => a.account + ": " + usd(a.amount)).join(", "))}</td><td>${esc(x.after?.action || (x.after_claimed_by ? "Cleared by " + x.after_claimed_by : "No separate item"))} ${esc((x.after?.adjustments || []).map((a) => a.account + ": " + usd(a.amount)).join(", "))}</td></tr>`).join("")}</tbody></table></div>` : `<p>No decisions change in this period.</p>`}
        <div class="runrow"><button class="btn btn-primary" id="applyPreview">Apply this policy change</button><button class="btn btn-secondary" id="cancelPreview">Cancel</button><span class="note" id="previewNote">The policy and supporting data will be checked again.</span></div></div></section>`;
      pendingPreview = { report: r, role };
      paint();
      document.getElementById("policyPreview").scrollIntoView({ behavior: "smooth", block: "center" });

    } catch (e) { note.textContent = e.message; controls.forEach((c) => { c.disabled = false; }); }
  }

  function wirePreview() {
    if (!pendingPreview || !document.getElementById("applyPreview")) return;
    const { report: r, role } = pendingPreview;
    document.getElementById("cancelPreview").onclick = () => { pendingPreview = null; bandResult = ""; paint(); };
    document.getElementById("applyPreview").onclick = async (e) => {
      e.target.disabled = true;
      try {
        const response = await fetch("/api/playbook/apply-preview", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ preview_id: r.preview_id, role }) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || String(response.status));
        pendingPreview = null;
        bandResult = `<section class="panel"><div class="panel-body stack"><h3>Applied as version ${result.new_version}</h3><p>${esc(result.cause?.note || "Policy updated.")}</p>${SO.reranBlock(result.reran, client, true)}${diffBlock(result.diff, { client })}${result.reconciliation ? `<p>Reconciled ${result.reconciliation.period} again with no model calls. ${result.reconciliation.escalated} items remain for review.</p>` : ""}<button class="btn btn-secondary undo" data-id="${esc(result.correction_id)}">Undo this answer</button></div></section>`;
        await load();
        scrollTo({ top: 0 });
      } catch (err) { document.getElementById("previewNote").textContent = err.message; }
    };
  }

  function findingsBlock() {
    const f = pb.findings || [];
    if (!f.length) return "";
    return `<section class="panel"><div class="panel-head"><h3>Pattern disagreements to review</h3><span class="faint small">Past items that disagree with a rule the rest of history supports. Reported, never learned from.</span></div>
      ${f.map((x) => `<div class="rule-row"><p>${esc(x.summary)}</p><div class="rule-meta"><span>${cite(client, x.item_id)}</span><span>${day(x.date)}</span><span class="num">${usd(x.amount)}</span><span>${esc((x.posted_by || []).join(", "))}</span><span>${x.agreeing_cases} comparable items went the other way</span><span class="cite">${esc(x.rule_id)}</span></div></div>`).join("")}</section>`;
  }

  // Unlearning: every input the playbook received can be undone. Deterministic, no model call.
  const INPUT = { correction: "Correction", interview: "Answer", band: "Yes or no", conflict: "Conflict raised", conflict_resolved: "Conflict settled", retraction: "Undo" };
  function inputsBlock() {
    if (!inputs.length) return "";
    // an undone input is not flagged on its own entry: the retraction entries name what they undid
    const undone = new Set(inputs.filter((c) => c.type === "retraction").map((c) => c.retracted));
    return `<section class="panel" id="taught"><div class="panel-head"><h3>Everything this playbook was taught</h3><span class="faint small">Any input can be undone. The rule reverts and every resolution that leaned on it is checked again.</span></div>
      ${(() => { const row = (c) => `<div class="rule-row input-row"><div><p>${esc(c.summary || c.note || c.answer || INPUT[c.type] || c.type)}</p>
        <div class="rule-meta"><span class="cite">${esc(c.correction_id)}</span><span>${esc(INPUT[c.type] || cap(c.type))}</span>${c.at ? `<span>${when(c.at)}</span>` : ""}${c.role || c.by_role ? `<span>${esc(cap(String(c.role || c.by_role).replace(/_/g, " ")))}</span>` : ""}${undone.has(c.correction_id) ? `<span class="state state-carry">Undone</span>` : ""}${c.status === "held" ? `<span class="state state-carry">Recorded, not applied</span>` : ""}</div></div>
        ${["correction", "interview"].includes(c.type) && !undone.has(c.correction_id) && c.status !== "held" ? `<button class="btn btn-secondary btn-sm undo" data-id="${esc(c.correction_id)}">Undo</button>` : ""}</div>`;
        // the latest few stay in reach for Undo; a long history folds away so it never buries the questions
        return inputs.slice(0, 3).map(row).join("") + (inputs.length > 3 ? `<details class="more"><summary>Show all ${inputs.length}</summary>${inputs.slice(3).map(row).join("")}</details>` : ""); })()}</section>`;
  }

  async function undo(btn) {
    if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Confirm undo"; return; }
    btn.disabled = true; btn.textContent = "Undoing";
    try {
      const res = await fetch("/api/retract", { method: "POST", headers: { "content-type": "application/json" }, body: SO.body({ client, correction_id: btn.dataset.id, role: asRole(), note: "Undone from the playbook screen" }) });
      if (!res.ok) throw new Error(String(res.status));
      const r = await res.json(), re = r.reopened || [];
      bandResult = "";
      undoResult = `<section class="panel"><div class="panel-body stack">
        <div class="label">${esc(r.retracted)} undone · playbook version ${esc(r.new_version)}</div>
        <h2 class="ask-q"><span class="num">${r.resolutions_checked}</span> past item${r.resolutions_checked === 1 ? "" : "s"} checked, <span class="num">${re.length}</span> re-opened</h2>
        ${re.length ? `<div class="table-wrap"><table class="grid tight"><tbody>${re.map((x) => `<tr><td>${cite(client, x.item_id)}</td><td class="wrap">${esc((x.record || {}).description || (x.record || {}).memo || "")}</td><td class="r">${x.record ? usd(x.record.amount) : ""}</td><td class="wrap">Rule withdrawn, back in the queue</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Nothing already resolved depended on it.</p>`}
        ${diffBlock(r.diff, { client })}
      </div></section>`;
      await load();
      scrollTo({ top: 0 });
    } catch (e) { btn.disabled = false; btn.dataset.armed = ""; btn.textContent = "Undo did not go through. Try again"; }
  }


  function question(r) {
    const open = r.id === openId;
    return `<div class="qa ${open ? "open" : ""}">
      <button class="qa-head" data-id="${esc(r.id)}" aria-expanded="${open}"><span class="qa-q">${r.below_floor ? `<span class="label">Not running until this is answered</span>` : ""}<span>${esc(r.open_question)}</span>${open ? "" : `<span class="faint small">${esc(r.text)}</span>`}</span><span class="cite">${esc(r.id)}</span></button>
      ${open ? `<div class="qa-body">
        <div class="rule-text"><span class="label">What the agent will do unless you say otherwise</span><div>${esc(r.text)}</div></div>
        ${evidence(r)}
        <form class="answer" data-id="${esc(r.id)}">
          <label class="label" for="ans-${esc(r.id)}">Your answer</label>
          <textarea class="input" id="ans-${esc(r.id)}" rows="2" placeholder="Yes, but only under $50. Anything larger comes to me."></textarea>
          <div class="runrow"><button class="btn btn-primary btn-sm">Answer</button><span class="note">Rewrites this rule and replays history. About 15 seconds, one model call.</span></div>
        </form>
        <div class="answer-result"></div>
      </div>` : ""}
    </div>`;
  }

  const ruleRow = (r) => `<div class="rule-row"><p>${esc(r.text)}</p>${evidence(r)}</div>`;

  async function versionDiff(v) {
    const box = document.getElementById("vdiff");
    if (v.version <= 1) { box.innerHTML = `<p class="muted">Version 1 is the playbook as induced. Nothing to compare it with.</p>`; return; }
    try {
      const d = await get(SO.withTrack(`/api/playbook/${client}/diff?from=${v.version - 1}&to=${v.version}`));
      box.innerHTML = `${d.cause && d.cause.note ? `<p class="muted">"${esc(d.cause.note)}"</p>` : ""}${diffBlock(d, { client })}`;
    } catch (e) { box.innerHTML = `<p class="muted">That comparison is not available.</p>`; }
  }

  function paint() {
    const live = pb.rules.filter((r) => r.status !== "retired");
    // below_floor: as written the rule disagrees with too much of its own history, so it does not run
    // (whatever its status says) until a person answers whether policy changed or it was written down wrong
    const asks = live.filter((r) => (r.status === "proposed" || r.below_floor) && r.open_question);
    const approved = live.filter((r) => r.status === "approved" && !(r.below_floor && r.open_question));
    const other = live.filter((r) => r.status === "proposed" && !r.open_question);
    if (openId === null && asks.length) openId = asks[0].id;
    summary.innerHTML = `<span><b class="num">${live.length}</b> rules</span><span><b class="num">${approved.length}</b> approved</span><span><b class="num">${asks.length}</b> waiting on an answer</span><span><b class="num">${live.filter((r) => r.executable && !r.below_floor).length}</b> run as code at $0.00</span>${live.some((r) => r.below_floor) ? `<span><b class="num">${live.filter((r) => r.below_floor).length}</b> held back by their own history</span>` : ""}<span>Version <b class="num">${pb.version}</b></span>`;
    view.innerHTML = `<div class="stack">
      ${pb.synthetic_demo ? `<section class="panel"><div class="panel-body"><b>Illustrative demo</b><p>Hand-authored policies and synthetic transactions. This demonstrates the interaction, not learned policy quality or benchmark performance.</p></div></section>` : ""}
      ${undoResult}
      ${bandResult}
      ${bandCard()}
      ${inputsBlock()}
      ${asks.length ? `<section class="panel"><div class="panel-head"><h3>Questions for you</h3><span class="faint small">The agent asks before it assumes. Each answer becomes a rule change you can read.</span></div>${asks.map(question).join("")}</section>` : ""}
      <section class="panel"><div class="panel-head"><h3>Approved rules</h3><span class="faint small">${approved.length} in force</span></div>${approved.map(ruleRow).join("") || `<div class="panel-body muted">None yet. Answer a question above to approve the first one.</div>`}</section>
      ${other.length ? `<section class="panel"><div class="panel-head"><h3>Proposed, no question</h3></div>${other.map(ruleRow).join("")}</section>` : ""}
      ${findingsBlock()}
      <section class="panel"><div class="panel-head"><h3>Versions</h3><span class="faint small">Every change has a cause</span></div>
        <div class="panel-body stack"><div class="versions">${pb.versions.map((v) => `<button class="vrow" data-v="${v.version}"><span class="num">v${v.version}</span><span>${esc(CAUSE[(v.cause || {}).type] || cap((v.cause || {}).type || "change"))}</span><span class="faint">${when(v.created_at)}</span></button>`).join("")}</div><div id="vdiff"></div></div></section>
    </div>`;

    wirePreview();
    view.querySelectorAll(".ask button[data-answer]").forEach((b) => b.addEventListener("click", () => answerBand(b.dataset.answer)));
    const lf = document.getElementById("limitForm");
    if (lf) lf.addEventListener("submit", (e) => {
      e.preventDefault();
      const amount = parseFloat(document.getElementById("limitValue").value.replace(/[$,\s]/g, ""));
      if (!(amount > 0)) { document.getElementById("askNote").textContent = "Type the amount first, for example 50."; return; }
      answerBand("limit", amount);
    });
    view.querySelectorAll("button.undo").forEach((b) => b.addEventListener("click", () => undo(b)));
    view.querySelectorAll(".qa-head").forEach((b) => b.addEventListener("click", () => { openId = openId === b.dataset.id ? "" : b.dataset.id; paint(); }));
    view.querySelectorAll(".vrow").forEach((b) => b.addEventListener("click", () => versionDiff(pb.versions.find((v) => String(v.version) === b.dataset.v))));
    if (pb.versions.length > 1) versionDiff(pb.versions[pb.versions.length - 1]);
    view.querySelectorAll("form.answer").forEach((f) => f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const ta = f.querySelector("textarea"), btn = f.querySelector("button"), note = f.querySelector(".note"), out = f.parentElement.querySelector(".answer-result");
      if (!ta.value.trim()) { note.textContent = "Write an answer first."; return; }
      btn.disabled = true;
      const t0 = Date.now(), tick = setInterval(() => { note.textContent = `Rewriting the rule and replaying history. ${Math.round((Date.now() - t0) / 1000)}s`; }, 500);
      try {
        const res = await fetch("/api/playbook/answer", { method: "POST", headers: { "content-type": "application/json" }, body: SO.body({ client, rule_id: f.dataset.id, answer: ta.value, role: asRole() }) });
        if (!res.ok) throw new Error(String(res.status));
        const r = await res.json();
        note.textContent = "Done.";
        out.innerHTML = `<div class="stack"><p>${esc(r.explanation || "")}</p>${diffBlock(r.diff, { client })}${SO.reranBlock(r.reran, client)}<div class="runrow"><button class="btn btn-secondary btn-sm" id="reload">Show playbook version ${esc(r.new_version)}</button></div></div>`;
        document.getElementById("reload").addEventListener("click", () => { openId = null; load(); });
      } catch (err) {
        note.textContent = "The answer did not go through. Nothing was changed. Check that the server is running and try again.";
        btn.disabled = false;
      } finally { clearInterval(tick); }
    }));
  }

  async function load(version) {
    view.innerHTML = `<div class="panel error">Loading the playbook</div>`;
    pb = await get(SO.withTrack(`/api/playbook/${client}` + (version ? `?version=${version}` : "")));
    // newer routes: absent on an older server, so each one fails soft
    const soft = (path, empty) => fetch(SO.withTrack(path)).then((r) => (r.ok ? r.json() : empty)).catch(() => empty);
    [bandQs, inputs] = await Promise.all([
      version ? [] : soft(`/api/playbook/${client}/questions`, {}).then((q) => q.band_questions || []),
      soft(`/api/corrections/${client}`, []),
    ]);
    verSel.innerHTML = pb.versions.slice().reverse().map((v) => `<option value="${v.version}"${v.version === pb.version ? " selected" : ""}>Version ${v.version}</option>`).join("");
    clientSeg.innerHTML = clients.map((c) => `<button data-v="${esc(c.id)}" aria-pressed="${c.id === client}">${esc(c.name)}</button>`).join("");
    clientSeg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { client = b.dataset.v; pendingPreview = null; openId = null; bandResult = ""; undoResult = ""; load().catch(fail); }));
    paint();
  }
  const fail = (e) => { view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`; };
  verSel.addEventListener("change", () => { pendingPreview = null; bandResult = ""; openId = null; load(verSel.value).catch(fail); });

  try { clients = await get("/api/clients"); await load(); } catch (e) { fail(e); }
})();
