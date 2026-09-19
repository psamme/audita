/* Screen 3: the playbook as sentences a controller can read and sign.
   Proposed rules carry the question the agent wants answered; answering one
   POSTs /api/playbook/answer and comes back as a diff. */
(async function () {
  const { get, esc, cap, usd, day, cite, diffBlock, bandBar } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary");
  const clientSeg = document.getElementById("client"), verSel = document.getElementById("version");
  let clients = [], client = new URLSearchParams(location.search).get("client") || "A", pb = null, openId = null;
  let bandQs = [], inputs = [], bandResult = "", undoResult = "";

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
      ${r.executable ? `<span>Runs as code at $0.00</span>` : `<span>Guidance for the investigator</span>`}
      ${r.human_confirmed ? `<span class="state">Confirmed by a person</span>` : ""}
    </div>${ids ? `<div class="cites">${ids}${more}</div>` : ""}${bands(r)}`;
  }

  // condition keys look like amount_max, diff_min, doc.net_diff_abs_max: name the quantity, not the key
  const condLabel = (k) => (/pct/.test(k) ? "Difference, % of invoice" : /diff/.test(k) ? "Difference" : /amount/.test(k) ? "Amount" : /day/.test(k) ? "Days" : cap(k.replace(/[._]/g, " ")));
  function bands(r) {
    return Object.entries(r.bands || {}).map(([cond, b]) => `<div class="band-wrap"><div class="label">${esc(condLabel(cond))}${b.source === "interview" ? " · narrowed by an answer" : b.source === "stated" ? " · stated by a person" : ""}</div>${bandBar(b, { client })}</div>`).join("");
  }

  // The stage moment: one yes or no, the band narrows, the playbook has a new version. No model call.
  function bandCard() {
    const q = bandQs[0];
    if (!q) return bandResult;
    const rule = pb.rules.find((r) => r.id === q.rule_id), b = (rule && rule.bands && rule.bands[q.condition]) || { side: "upper", lo: q.lo, hi: q.hi };
    return `<section class="panel ask"><div class="panel-body stack">
      <div class="label">One question · ${bandQs.length} band${bandQs.length === 1 ? "" : "s"} still open, widest first</div>
      <h2 class="ask-q">${esc(q.text.startsWith(q.rule_text) ? q.text.slice(q.rule_text.length).trim() : q.text)}</h2>
      <div id="askBand">${bandBar(b, { client, value: q.value })}</div>
      <div class="runrow">
        <button class="btn btn-primary" data-review="true">Yes, send it to a person</button>
        <button class="btn btn-secondary" data-review="false">No, that is fine on its own</button>
        <span class="note" id="askNote">Instant. No model call.</span>
      </div>
      <div class="rule-text"><span class="label">The rule this belongs to</span><div>${esc(q.rule_text)} <span class="cite">${esc(q.rule_id)}</span></div></div>
    </div></section>${bandResult}`;
  }

  async function answerBand(review) {
    const q = bandQs[0], note = document.getElementById("askNote");
    document.querySelectorAll(".ask button").forEach((b) => { b.disabled = true; });
    try {
      const res = await fetch("/api/playbook/answer-band", { method: "POST", headers: { "content-type": "application/json" },
        body: SO.body({ client, rule_id: q.rule_id, condition: q.condition, value: q.value, review }) });
      if (!res.ok) throw new Error(String(res.status));
      const r = await res.json();
      // redraw the same bar on the same scale so the narrowing is visible, then move on
      const max = document.querySelector("#askBand .band").dataset.max;
      document.getElementById("askBand").innerHTML = bandBar(r.band, { client, value: q.value, max: Number(max) });
      note.textContent = `Recorded. The playbook is now version ${r.new_version}.`;
      bandResult = `<section class="panel"><div class="panel-head"><h3>Last answer: ${review ? "yes" : "no"} at ${usd(q.value)}</h3><span class="mono faint">${esc(r.correction_id || "")}</span></div>
        <div class="panel-body stack"><p class="muted">${esc((r.cause && r.cause.note) || "")}</p>${diffBlock(r.diff, { client })}</div></section>`;
      setTimeout(() => load().catch(fail), 1600);
    } catch (e) {
      note.textContent = "The answer did not go through. Check that the server is running and try again.";
      document.querySelectorAll(".ask button").forEach((b) => { b.disabled = false; });
    }
  }

  function findingsBlock() {
    const f = pb.findings || [];
    if (!f.length) return "";
    return `<section class="panel"><div class="panel-head"><h3>Where a person broke the pattern</h3><span class="faint small">Past items that disagree with a rule the rest of history supports. Reported, never learned from.</span></div>
      ${f.map((x) => `<div class="rule-row"><p>${esc(x.summary)}</p><div class="rule-meta"><span>${cite(client, x.item_id)}</span><span>${day(x.date)}</span><span class="num">${usd(x.amount)}</span><span>${esc((x.posted_by || []).join(", "))}</span><span>${x.agreeing_cases} comparable items went the other way</span><span class="cite">${esc(x.rule_id)}</span></div></div>`).join("")}</section>`;
  }

  // Unlearning: every input the playbook received can be undone. Deterministic, no model call.
  const INPUT = { correction: "Correction", interview: "Answer", band: "Yes or no", conflict: "Conflict raised", conflict_resolved: "Conflict settled", retraction: "Undo" };
  function inputsBlock() {
    if (!inputs.length && !undoResult) return "";
    // an undone input is not flagged on its own entry: the retraction entries name what they undid
    const undone = new Set(inputs.filter((c) => c.type === "retraction").map((c) => c.retracted));
    return `${undoResult}<section class="panel"><div class="panel-head"><h3>Everything this playbook was taught</h3><span class="faint small">Any input can be undone. The rule reverts and every resolution that leaned on it is checked again.</span></div>
      ${inputs.map((c) => `<div class="rule-row input-row"><div><p>${esc(c.summary || c.note || c.answer || INPUT[c.type] || c.type)}</p>
        <div class="rule-meta"><span class="cite">${esc(c.correction_id)}</span><span>${esc(INPUT[c.type] || cap(c.type))}</span>${c.at ? `<span>${when(c.at)}</span>` : ""}${c.role || c.by_role ? `<span>${esc(cap(String(c.role || c.by_role).replace(/_/g, " ")))}</span>` : ""}${undone.has(c.correction_id) ? `<span class="state state-carry">Undone</span>` : ""}</div></div>
        ${["correction", "interview"].includes(c.type) && !undone.has(c.correction_id) ? `<button class="btn btn-secondary btn-sm undo" data-id="${esc(c.correction_id)}">Undo</button>` : ""}</div>`).join("")}</section>`;
  }

  async function undo(btn) {
    if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Confirm undo"; return; }
    btn.disabled = true; btn.textContent = "Undoing";
    try {
      const res = await fetch("/api/retract", { method: "POST", headers: { "content-type": "application/json" }, body: SO.body({ client, correction_id: btn.dataset.id, note: "Undone from the playbook screen" }) });
      if (!res.ok) throw new Error(String(res.status));
      const r = await res.json(), re = r.reopened || [];
      undoResult = `<section class="panel"><div class="panel-body stack">
        <div class="label">${esc(r.retracted)} undone · playbook version ${esc(r.new_version)}</div>
        <h2 class="ask-q"><span class="num">${r.resolutions_checked}</span> past item${r.resolutions_checked === 1 ? "" : "s"} checked, <span class="num">${re.length}</span> re-opened</h2>
        ${diffBlock(r.diff, { client })}
        ${re.length ? `<div class="table-wrap"><table class="grid tight"><tbody>${re.map((x) => `<tr><td>${cite(client, x.item_id)}</td><td class="wrap">${esc((x.record || {}).description || (x.record || {}).memo || "")}</td><td class="r">${x.record ? usd(x.record.amount) : ""}</td><td class="wrap">Rule withdrawn, back in the queue</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Nothing already resolved depended on it.</p>`}
      </div></section>`;
      await load();
    } catch (e) { btn.disabled = false; btn.dataset.armed = ""; btn.textContent = "Undo did not go through. Try again"; }
  }


  function question(r) {
    const open = r.id === openId;
    return `<div class="qa ${open ? "open" : ""}">
      <button class="qa-head" data-id="${esc(r.id)}" aria-expanded="${open}"><span class="qa-q"><span>${esc(r.open_question)}</span>${open ? "" : `<span class="faint small">${esc(r.text)}</span>`}</span><span class="cite">${esc(r.id)}</span></button>
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
    const asks = live.filter((r) => r.status === "proposed" && r.open_question);
    const approved = live.filter((r) => r.status === "approved");
    const other = live.filter((r) => r.status === "proposed" && !r.open_question);
    if (openId === null && asks.length) openId = asks[0].id;
    summary.innerHTML = `<span><b class="num">${live.length}</b> rules</span><span><b class="num">${approved.length}</b> approved</span><span><b class="num">${asks.length}</b> waiting on an answer</span><span><b class="num">${live.filter((r) => r.executable).length}</b> run as code at $0.00</span><span>Version <b class="num">${pb.version}</b></span>`;
    view.innerHTML = `<div class="stack">
      ${bandCard()}
      ${asks.length ? `<section class="panel"><div class="panel-head"><h3>Questions for you</h3><span class="faint small">The agent asks before it assumes. Each answer becomes a rule change you can read.</span></div>${asks.map(question).join("")}</section>` : ""}
      <section class="panel"><div class="panel-head"><h3>Approved rules</h3><span class="faint small">${approved.length} in force</span></div>${approved.map(ruleRow).join("") || `<div class="panel-body muted">None yet. Answer a question above to approve the first one.</div>`}</section>
      ${other.length ? `<section class="panel"><div class="panel-head"><h3>Proposed, no question</h3></div>${other.map(ruleRow).join("")}</section>` : ""}
      ${findingsBlock()}
      ${inputsBlock()}
      <section class="panel"><div class="panel-head"><h3>Versions</h3><span class="faint small">Every change has a cause</span></div>
        <div class="panel-body stack"><div class="versions">${pb.versions.map((v) => `<button class="vrow" data-v="${v.version}"><span class="num">v${v.version}</span><span>${esc(CAUSE[(v.cause || {}).type] || cap((v.cause || {}).type || "change"))}</span><span class="faint">${when(v.created_at)}</span></button>`).join("")}</div><div id="vdiff"></div></div></section>
    </div>`;

    view.querySelectorAll(".ask button[data-review]").forEach((b) => b.addEventListener("click", () => answerBand(b.dataset.review === "true")));
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
        const res = await fetch("/api/playbook/answer", { method: "POST", headers: { "content-type": "application/json" }, body: SO.body({ client, rule_id: f.dataset.id, answer: ta.value }) });
        if (!res.ok) throw new Error(String(res.status));
        const r = await res.json();
        note.textContent = "Done.";
        out.innerHTML = `<div class="stack"><p>${esc(r.explanation || "")}</p>${diffBlock(r.diff, { client })}<div class="runrow"><button class="btn btn-secondary btn-sm" id="reload">Show playbook version ${esc(r.new_version)}</button></div></div>`;
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
    clientSeg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { client = b.dataset.v; openId = null; bandResult = ""; undoResult = ""; load().catch(fail); }));
    paint();
  }
  const fail = (e) => { view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`; };
  verSel.addEventListener("change", () => { openId = null; load(verSel.value).catch(fail); });

  try { clients = await get("/api/clients"); await load(); } catch (e) { fail(e); }
})();
