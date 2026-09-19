/* Screen 3: the playbook as sentences a controller can read and sign.
   Proposed rules carry the question the agent wants answered; answering one
   POSTs /api/playbook/answer and comes back as a diff. */
(async function () {
  const { get, esc, cap, cite, diffBlock } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary");
  const clientSeg = document.getElementById("client"), verSel = document.getElementById("version");
  let clients = [], client = new URLSearchParams(location.search).get("client") || "A", pb = null, openId = null;

  const CAUSE = { induction: "Induced from the ERP trail", correction: "A reviewer's correction", interview: "An answered question" };
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
    </div>${ids ? `<div class="cites">${ids}${more}</div>` : ""}`;
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
      box.innerHTML = `${d.cause && d.cause.note ? `<p class="muted">"${esc(d.cause.note)}"</p>` : ""}${diffBlock(d)}`;
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
      ${asks.length ? `<section class="panel"><div class="panel-head"><h3>Questions for you</h3><span class="faint small">The agent asks before it assumes. Each answer becomes a rule change you can read.</span></div>${asks.map(question).join("")}</section>` : ""}
      <section class="panel"><div class="panel-head"><h3>Approved rules</h3><span class="faint small">${approved.length} in force</span></div>${approved.map(ruleRow).join("") || `<div class="panel-body muted">None yet. Answer a question above to approve the first one.</div>`}</section>
      ${other.length ? `<section class="panel"><div class="panel-head"><h3>Proposed, no question</h3></div>${other.map(ruleRow).join("")}</section>` : ""}
      <section class="panel"><div class="panel-head"><h3>Versions</h3><span class="faint small">Every change has a cause</span></div>
        <div class="panel-body stack"><div class="versions">${pb.versions.map((v) => `<button class="vrow" data-v="${v.version}"><span class="num">v${v.version}</span><span>${esc(CAUSE[(v.cause || {}).type] || cap((v.cause || {}).type || "change"))}</span><span class="faint">${when(v.created_at)}</span></button>`).join("")}</div><div id="vdiff"></div></div></section>
    </div>`;

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
        out.innerHTML = `<div class="stack"><p>${esc(r.explanation || "")}</p>${diffBlock(r.diff)}<div class="runrow"><button class="btn btn-secondary btn-sm" id="reload">Show playbook version ${esc(r.new_version)}</button></div></div>`;
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
    verSel.innerHTML = pb.versions.slice().reverse().map((v) => `<option value="${v.version}"${v.version === pb.version ? " selected" : ""}>Version ${v.version}</option>`).join("");
    clientSeg.innerHTML = clients.map((c) => `<button data-v="${esc(c.id)}" aria-pressed="${c.id === client}">${esc(c.name)}</button>`).join("");
    clientSeg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { client = b.dataset.v; openId = null; load().catch(fail); }));
    paint();
  }
  const fail = (e) => { view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`; };
  verSel.addEventListener("change", () => { openId = null; load(verSel.value).catch(fail); });

  try { clients = await get("/api/clients"); await load(); } catch (e) { fail(e); }
})();
