/* Step 2. Upload files, confirm what each column is, then fill any gap in the trail by hand.

   The model suggests the column mapping; this screen exists so a person confirms it before
   anything is written. A wrong sign or a wrong date order does not throw, it just quietly makes
   every later answer worse, which is exactly the kind of mistake a confirmation step catches. */
(function () {
  const view = document.getElementById("view");
  const esc = SO.esc;

  const ROLES = [
    ["bank_lines", "Bank statement", "Every line on the account. Required.", true],
    ["ledger_entries", "Cash-clearing ledger", "The entries that clear against the bank, not your whole general ledger. Required.", true],
    ["reconciliations", "Reconciliation history", "Which bank line cleared which entry, who did it and when. This is most of what teaches the agent.", false],
    ["adjustments", "Adjusting entries", "Write-offs, fees and short-pay differences, each tied to the bank line it belongs to.", false],
    ["approvals", "Approvals", "Anything that needed a second signature.", false],
    ["invoices", "Invoices", "Optional. Lets rules reason about terms and invoice age.", false],
    ["documents", "Emails and remittances", "Optional, and unusually useful: remittance advices and the email trail.", false],
  ];

  let company = null, state = null, current = null;

  async function refresh() {
    company = await CO.get("/api/onboarding/company");
    if (!company.created) {
      view.innerHTML = CO.empty("No company yet", 'Start at <a href="setup.html">Setup</a>.');
      return null;
    }
    state = await CO.get("/api/onboarding/coldstart/status");
    return company;
  }

  function counts() {
    const c = company.counts || {};
    return `<div class="stat-row">
      ${stat(c.bank_line, "bank lines")}
      ${stat(c.ledger_entry, "ledger entries")}
      ${stat(state.your_links, "decisions of yours")}
      ${stat(state.derived_links, "worked out for you")}
      ${stat(c.journal_entry, "adjustments")}
    </div>`;
  }
  const stat = (n, label) => `<div class="stat"><b>${(n ?? 0).toLocaleString()}</b><span>${label}</span></div>`;

  function uploadPanel() {
    return `<section class="panel">
      <div class="panel-head"><h3>Files</h3></div>
      <div class="panel-body">
        <div class="roles">${ROLES.map(([role, label, note, required]) => `
          <div class="role-row">
            <div><div class="want">${esc(label)}${required ? "" : ' <span class="faint">optional</span>'}</div></div>
            <div class="req">${esc(note)}</div>
            <div><label class="btn btn-secondary btn-sm">Choose file
              <input type="file" accept=".csv,text/csv,text/plain" data-role="${role}"></label></div>
          </div>`).join("")}
        </div>
        <p class="faint" style="margin-top:12px">CSV. We read the header and a few rows to work out the
          columns, then show you what we think before anything is written.</p>
      </div>
    </section>`;
  }

  function coveragePanel() {
    const pct = state.bank_lines ? Math.round(100 * (state.bank_lines - state.residue) / state.bank_lines) : 0;
    const needMore = state.kinds_ready < Math.min(8, state.kinds);
    return `<section class="panel">
      <div class="panel-head"><h3>What is still unexplained</h3></div>
      <div class="panel-body">
        <p class="muted">${state.residue.toLocaleString()} of ${state.bank_lines.toLocaleString()} bank lines
          have nothing recorded against them (${pct}% accounted for).
          ${state.kinds_ready} of ${state.kinds} recurring kinds have the three examples a rule needs
          before it can run on its own.</p>
        ${state.shapes && state.shapes.length ? `<div class="coverage">${state.shapes.slice(0, 8).map((s) => `
          <div class="cov-row">
            <div class="shape" title="${esc(s.shape)}">${esc(s.shape || "(no description)")}</div>
            <div class="cov-bar"><i style="width:${Math.min(100, Math.round(100 * s.labelled / Math.max(1, s.total)))}%"></i></div>
            <div class="faint">${s.labelled} of ${s.total}${s.labelled >= 3 ? " · enough" : ""}</div>
          </div>`).join("")}</div>` : ""}
        <div class="actions" style="margin-top:16px">
          ${state.derived_links === 0 ? `<button class="btn btn-secondary" id="derive">Work out the easy matches</button>` : ""}
          ${state.residue ? `<a class="btn ${needMore ? "btn-primary" : "btn-secondary"}" href="#label" id="golabel">Work through what is left</a>` : ""}
          <a class="btn ${needMore ? "btn-secondary" : "btn-primary"}" href="playbook.html">Next: your playbook</a>
        </div>
        <div id="derivemsg"></div>
      </div>
    </section>`;
  }

  async function render() {
    if (!(await refresh())) return;
    view.innerHTML = counts() + uploadPanel() + coveragePanel() + `<div id="mapping"></div><div id="label"></div>`;
    view.querySelectorAll('input[type="file"]').forEach((inp) =>
      inp.addEventListener("change", () => inp.files[0] && upload(inp.dataset.role, inp.files[0])));
    const d = document.getElementById("derive");
    if (d) d.addEventListener("click", derive);
    const g = document.getElementById("golabel");
    if (g) g.addEventListener("click", (e) => { e.preventDefault(); labeller(); });
  }

  // --- upload and mapping -------------------------------------------------------------------
  async function upload(role, file) {
    const box = document.getElementById("mapping");
    box.innerHTML = `<section class="panel"><div class="panel-body"><div class="note">
      <span class="spin"></span> Reading ${esc(file.name)}...</div></div></section>`;
    try {
      const raw = await file.arrayBuffer();
      const up = await CO.postRaw(
        `/api/onboarding/upload?role=${encodeURIComponent(role)}&filename=${encodeURIComponent(file.name)}`, raw);
      box.innerHTML = `<section class="panel"><div class="panel-body"><div class="note">
        <span class="spin"></span> Working out what each column is...</div></div></section>`;
      const proposal = await CO.post("/api/onboarding/mapping/propose", { upload_id: up.upload_id });
      current = { up, mapping: { columns: proposal.columns, sign: proposal.sign, dayfirst: proposal.dayfirst },
                  why: proposal.why || {}, notes: proposal.notes, source: proposal.source };
      await validateAndShow();
      box.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      box.innerHTML = CO.err(e);
    }
  }

  async function validateAndShow() {
    const res = await CO.post("/api/onboarding/mapping/validate",
      { upload_id: current.up.upload_id, mapping: current.mapping });
    current.check = res;
    showMapping();
  }

  function showMapping() {
    const { up, mapping, check } = current;
    const cols = up.header;
    const fields = Object.keys(check.stats ? {} : {});
    const spec = FIELD_LIST(up.role);
    const box = document.getElementById("mapping");
    const s = check.stats || {};
    box.innerHTML = `<section class="panel" id="mapcard">
      <div class="panel-head"><h3>${esc(up.filename)}</h3>
        <span class="faint">${up.rows.toLocaleString()} rows · ${esc(up.encoding)} · delimiter "${esc(up.delimiter)}"${up.skipped_preamble ? ` · skipped ${up.skipped_preamble} row(s) above the header` : ""}</span></div>
      <div class="panel-body">
        ${current.source === "heuristic" ? `<div class="note">Matched by name, without the model. Check it closely.</div>` : ""}
        ${current.notes ? `<p class="faint">${esc(current.notes)}</p>` : ""}
        <div class="table-wrap"><table class="grid tight"><thead><tr>
          <th>We need</th><th>Your column</th><th>Why</th></tr></thead><tbody>
          ${spec.map((f) => `<tr class="r">
            <td><b>${esc(f.field)}</b>${f.required ? "" : ' <span class="faint">optional</span>'}</td>
            <td><select class="select" data-map="${esc(f.field)}">
              <option value="">not in this file</option>
              ${cols.map((c) => `<option value="${esc(c)}" ${mapping.columns[f.field] === c ? "selected" : ""}>${esc(c)}</option>`).join("")}
            </select></td>
            <td class="faint">${esc(current.why[f.field] || "")}</td>
          </tr>`).join("")}
        </tbody></table></div>

        ${signBlock(mapping, cols)}

        ${s.date_format_ambiguous ? `<div class="note bad">These dates could be read either way round.
          Say which: <button class="btn btn-sm btn-secondary" data-df="1">day first</button>
          <button class="btn btn-sm btn-secondary" data-df="0">month first</button></div>` : ""}
        ${s.sign_warning ? `<div class="note bad">${esc(s.sign_warning)}</div>` : ""}
        ${check.blocking && check.blocking.length ? `<div class="note bad">Still needed: ${check.blocking.map(esc).join(", ")}</div>` : ""}
        ${check.errors && check.errors.length ? `<div class="note bad">${check.errors.length} rows will not parse, first:
          row ${check.errors[0].row}, ${esc(check.errors[0].field)} = "${esc(check.errors[0].value)}" (${esc(check.errors[0].problem)})</div>` : ""}

        ${previewBlock(check)}

        <div class="actions" style="margin-top:16px">
          <button class="btn btn-primary" id="commit" ${check.ok ? "" : "disabled"}>Import ${up.rows.toLocaleString()} rows</button>
          <button class="btn btn-ghost" id="cancelmap">Cancel</button>
          <span id="impmsg" class="muted"></span>
        </div>
      </div>
    </section>`;
    wireMapping();
  }

  function previewBlock(check) {
    const rows = check.preview || [];
    if (!rows.length) return "";
    const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const s = check.stats || {};
    return `<h4 style="margin-top:20px">As we would read it</h4>
      ${s.date_range ? `<p class="faint">${s.money_in} in, ${s.money_out} out · ${esc(s.date_range[0])} to ${esc(s.date_range[1])}${s.dayfirst !== null && s.dayfirst !== undefined ? ` · dates read ${s.dayfirst ? "day first" : "month first"}` : ""}</p>` : ""}
      <div class="table-wrap"><table class="grid tight"><thead><tr>${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((r) => `<tr class="r">${keys.map((k) =>
        `<td class="${typeof r[k] === "number" ? "num" : ""}">${r[k] === undefined ? "" :
          (typeof r[k] === "number" ? SO.usd(r[k]) : esc(String(r[k]).slice(0, 48)))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function signBlock(mapping, cols) {
    const sign = mapping.sign || { mode: "signed" };
    const opt = (v, label) => `<option value="${v}" ${sign.mode === v ? "selected" : ""}>${label}</option>`;
    return `<h4 style="margin-top:20px">Which way is money out</h4>
      <p class="faint">Getting this backwards does not fail loudly. It just stops the agent seeing payments
        leaving the account, so say it plainly.</p>
      <div class="actions">
        <select class="select" id="signmode" style="max-width:18rem">
          ${opt("signed", "One amount column, already signed")}
          ${opt("debit_credit", "Separate debit and credit columns")}
          ${opt("flag", "One amount plus a DR/CR indicator")}
        </select>
        ${sign.mode === "signed" ? `<label class="faint"><input type="checkbox" id="invert" ${sign.invert ? "checked" : ""}>
          this file shows money out as positive</label>` : ""}
        ${sign.mode === "debit_credit" ? colSel("debitcol", "money out column", cols, sign.debit_column) + colSel("creditcol", "money in column", cols, sign.credit_column) : ""}
        ${sign.mode === "flag" ? colSel("flagcol", "indicator column", cols, sign.flag_column) : ""}
      </div>`;
  }

  const colSel = (id, label, cols, val) => `<label class="faint">${label}
    <select class="select" id="${id}">${cols.map((c) => `<option value="${esc(c)}" ${c === val ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>`;

  function FIELD_LIST(role) {
    const R = {
      bank_lines: [["date", 1], ["amount", 1], ["description", 1], ["counterparty", 0], ["ref", 0], ["source_id", 0]],
      ledger_entries: [["date", 1], ["account", 1], ["amount", 1], ["memo", 1], ["posted_at", 0], ["counterparty", 0], ["ref", 0], ["invoice_id", 0], ["source_id", 0]],
      reconciliations: [["bank_ref", 1], ["ledger_ref", 1], ["reconciled_by", 1], ["reconciled_at", 1], ["undone_at", 0]],
      adjustments: [["bank_ref", 1], ["account", 1], ["amount", 1], ["posted_by", 1], ["posted_at", 1], ["memo", 0], ["date", 0]],
      approvals: [["subject_ref", 1], ["approver", 1], ["status", 1], ["date", 1], ["requested_by", 0], ["comment", 0]],
      invoices: [["id", 1], ["date", 1], ["amount", 1], ["party", 0], ["direction", 0], ["due_date", 0], ["terms", 0], ["status", 0]],
      documents: [["type", 1], ["date", 1], ["body", 1], ["sender", 0], ["subject", 0], ["party", 0], ["batch_id", 0]],
      chart_of_accounts: [["account", 1], ["name", 1]],
      people: [["id", 1], ["name", 1], ["role", 1], ["senior", 1]],
    }[role] || [];
    return R.map(([field, required]) => ({ field, required: !!required }));
  }

  function wireMapping() {
    const box = document.getElementById("mapcard");
    box.querySelectorAll("[data-map]").forEach((sel) => sel.addEventListener("change", async () => {
      const f = sel.dataset.map;
      if (sel.value) current.mapping.columns[f] = sel.value; else delete current.mapping.columns[f];
      await validateAndShow();
    }));
    const mode = document.getElementById("signmode");
    if (mode) mode.addEventListener("change", async () => {
      current.mapping.sign = { mode: mode.value };
      await validateAndShow();
    });
    const inv = document.getElementById("invert");
    if (inv) inv.addEventListener("change", async () => {
      current.mapping.sign.invert = inv.checked; await validateAndShow();
    });
    const bind = (id, key) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", async () => { current.mapping.sign[key] = el.value; await validateAndShow(); });
    };
    bind("debitcol", "debit_column"); bind("creditcol", "credit_column"); bind("flagcol", "flag_column");
    box.querySelectorAll("[data-df]").forEach((b) => b.addEventListener("click", async () => {
      current.mapping.dayfirst = b.dataset.df === "1"; await validateAndShow();
    }));
    document.getElementById("cancelmap").addEventListener("click", () => {
      current = null; document.getElementById("mapping").innerHTML = "";
    });
    document.getElementById("commit").addEventListener("click", commit);
  }

  async function commit() {
    const msg = document.getElementById("impmsg");
    msg.className = "muted"; msg.textContent = "Importing...";
    try {
      const job = await CO.post("/api/onboarding/import",
        { upload_id: current.up.upload_id, mapping: current.mapping });
      const done = await CO.watch(job.job_id, (r) => { msg.textContent = r.phase; });
      if (done.state !== "done") throw new Error(done.error || "import failed");
      const r = done.result;
      msg.className = "note good";
      msg.textContent = `${r.inserted} added, ${r.updated} updated` +
        (r.errors.length ? `, ${r.errors.length} skipped` : "");
      current = null;
      await render();
      if (r.warnings && r.warnings.length) {
        document.getElementById("mapping").innerHTML =
          `<section class="panel"><div class="panel-body">${r.warnings.map((w) =>
            `<div class="note">${esc(w)}</div>`).join("")}</div></section>`;
      }
    } catch (e) {
      msg.className = "note bad"; msg.textContent = e.message;
    }
  }

  // --- derive and label ---------------------------------------------------------------------
  async function derive() {
    const box = document.getElementById("derivemsg");
    box.innerHTML = `<div class="note"><span class="spin"></span> Matching...</div>`;
    try {
      const job = await CO.post("/api/onboarding/coldstart/derive", {});
      const done = await CO.watch(job.job_id, (r) => { box.innerHTML = CO.jobLine(r); });
      if (done.state !== "done") throw new Error(done.error || "failed");
      await render();
      document.getElementById("derivemsg").innerHTML =
        `<div class="note good">Matched ${done.result.derived} lines that clear one entry exactly.
         Those are the ones the agent already gets right on its own, so they are out of your way,
         not evidence of anything. What is left is where your judgement shows.</div>`;
    } catch (e) { box.innerHTML = CO.err(e); }
  }

  let queue = [], at = 0, chosen = new Set();

  async function labeller() {
    const box = document.getElementById("label");
    box.innerHTML = `<div class="note"><span class="spin"></span> Loading...</div>`;
    const res = await CO.get("/api/onboarding/coldstart/queue?limit=25");
    queue = res.items; at = 0;
    if (!queue.length) {
      box.innerHTML = CO.empty("Nothing left", "Every bank line has something recorded against it.");
      return;
    }
    drawLabel();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function drawLabel() {
    const box = document.getElementById("label");
    const it = queue[at];
    if (!it) { render(); return; }
    const b = it.item;
    chosen = new Set();
    box.innerHTML = `<section class="panel">
      <div class="panel-head"><h3>What happened here?</h3>
        <span class="faint">${at + 1} of ${queue.length} loaded · ${state.residue.toLocaleString()} left in total</span></div>
      <div class="panel-body labeller">
        <div>
          <dl class="kv">
            <dt>Date</dt><dd>${SO.day(b.date)}</dd>
            <dt>Amount</dt><dd class="num">${SO.usd(b.amount)}</dd>
            <dt>Description</dt><dd>${esc(b.description)}</dd>
            ${b.counterparty ? `<dt>Counterparty</dt><dd>${esc(b.counterparty)}</dd>` : ""}
            ${b.ref ? `<dt>Reference</dt><dd class="mono">${esc(b.ref)}</dd>` : ""}
          </dl>
          ${it.control_flags && it.control_flags.length ? `<div class="note bad">
            ${it.control_flags.map((f) => esc(f.detail)).join("<br>")}</div>` : ""}
          <h4>Open entries it could be</h4>
          <div class="cands">${it.candidates.length ? it.candidates.map((c) => `
            <button class="cand" data-pick="${esc(c.id)}" aria-pressed="false">
              <span class="mono faint">${esc(c.id.split("-").pop())}</span>
              <span>${esc(c.memo || c.counterparty || c.account)}<br>
                <span class="faint">${SO.day(c.date)} · ${esc(c.account)}</span></span>
              <span class="diff">${SO.usd(c.amount)}${Math.abs(c.difference) > 0.004
                ? `<br><span class="faint">off by ${SO.usd(c.difference)}</span>` : ""}</span>
            </button>`).join("") : `<p class="faint">Nothing open looks like a match.</p>`}
          </div>
        </div>
        <div>
          <h4>What did your team do?</h4>
          <div class="field"><label>Action</label>
            <select class="select" id="act">
              <option value="match">Cleared it against the entry, exactly</option>
              <option value="match_adjust">Cleared it, and booked the difference</option>
              <option value="book">No entry existed, booked the whole amount</option>
              <option value="carry_forward">Nothing to do this month, it waits</option>
              <option value="escalate">Sent it to someone to decide</option>
            </select>
          </div>
          <div id="extra"></div>
          <div class="row2">
            <div class="field"><label for="who">Who handled it</label>
              <select class="select" id="who">${(company.users || []).map((u) =>
                `<option value="${esc(u.id)}">${esc(u.name || u.id)}${u.senior ? " (senior)" : ""}</option>`).join("")}</select></div>
            <div class="field"><label for="days">Days it took</label>
              <input class="input" id="days" type="number" min="0" max="60" value="1">
              <div class="hint">Same or next day counts as routine.</div></div>
          </div>
          <label class="faint"><input type="checkbox" id="senior"> a senior signed this off</label>
          <div class="actions" style="margin-top:16px">
            <button class="btn btn-primary" id="save">Record it</button>
            <button class="btn btn-ghost" id="skip">Skip</button>
            <span id="lmsg" class="muted"></span>
          </div>
        </div>
      </div>
    </section>`;
    wireLabel();
  }

  function wireLabel() {
    const box = document.getElementById("label");
    box.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.pick;
      if (chosen.has(id)) chosen.delete(id); else chosen.add(id);
      b.setAttribute("aria-pressed", String(chosen.has(id)));
      drawExtra();
    }));
    document.getElementById("act").addEventListener("change", drawExtra);
    document.getElementById("skip").addEventListener("click", () => { at++; drawLabel(); });
    document.getElementById("save").addEventListener("click", save);
    drawExtra();
  }

  function drawExtra() {
    const act = document.getElementById("act").value;
    const it = queue[at];
    const box = document.getElementById("extra");
    // Keep whatever account was already chosen: picking a different candidate should not silently
    // reset where the difference goes.
    const keepAcct = (document.getElementById("acct") || {}).value;
    const keepTo = (document.getElementById("to") || {}).value;
    const picked = it.candidates.filter((c) => chosen.has(c.id));
    const total = picked.reduce((s, c) => s + c.amount, 0);
    const diff = Math.round((total - it.item.amount) * 100) / 100;
    const chart = company.chart || {};
    const accounts = Object.entries(chart).map(([k, v]) =>
      `<option value="${esc(k)}">${esc(k)} ${esc(v)}</option>`).join("");
    if (act === "match_adjust") {
      box.innerHTML = `<div class="field"><label>Where the ${SO.usd(diff)} went</label>
        <select class="select" id="acct">${accounts}</select>
        <div class="hint">${picked.length ? `${picked.length} entry chosen, off by ${SO.usd(diff)}.`
          : "Choose the entry on the left first."}</div></div>`;
    } else if (act === "book") {
      box.innerHTML = `<div class="field"><label>Booked to</label>
        <select class="select" id="acct">${accounts}</select>
        <div class="hint">The whole ${SO.usd(-it.item.amount)} goes here.</div></div>`;
    } else if (act === "escalate") {
      const seniors = (company.users || []).filter((u) => u.senior);
      box.innerHTML = `<div class="field"><label>Sent to</label>
        <select class="select" id="to">${seniors.map((u) =>
          `<option value="${esc(u.id)}">${esc(u.name || u.id)} · ${esc(u.role)}</option>`).join("")}</select></div>`;
    } else {
      box.innerHTML = "";
    }
    const acct = document.getElementById("acct");
    if (acct && keepAcct && [...acct.options].some((o) => o.value === keepAcct)) acct.value = keepAcct;
    const to = document.getElementById("to");
    if (to && keepTo && [...to.options].some((o) => o.value === keepTo)) to.value = keepTo;
  }

  async function save() {
    const it = queue[at];
    const act = document.getElementById("act").value;
    const msg = document.getElementById("lmsg");
    const picked = it.candidates.filter((c) => chosen.has(c.id));
    const total = picked.reduce((s, c) => s + c.amount, 0);
    const diff = Math.round((total - it.item.amount) * 100) / 100;
    const acct = document.getElementById("acct");
    const to = document.getElementById("to");
    const body = {
      item_id: it.item.id, action: act,
      ledger_ids: picked.map((c) => c.id),
      adjustments: act === "match_adjust" ? [{ account: acct.value, amount: diff }]
        : act === "book" ? [{ account: acct.value, amount: Math.round(-it.item.amount * 100) / 100 }] : [],
      handled_by: document.getElementById("who").value,
      days_to_handle: +document.getElementById("days").value || 1,
      senior_signed_off: document.getElementById("senior").checked,
      escalate_to: to ? to.value : null,
    };
    msg.className = "muted"; msg.textContent = "Saving...";
    try {
      const out = await CO.post("/api/onboarding/coldstart/label", body);
      state.residue = out.remaining; state.kinds_ready = out.kinds_ready;
      at++;
      drawLabel();
    } catch (e) {
      msg.className = "note bad"; msg.textContent = e.message;
    }
  }

  render().catch((e) => { view.innerHTML = CO.err(e); });
})();
