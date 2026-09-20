/* Audit screen: GET /api/audit/{client}. The team as a hand-off strip, what the auditor tested,
   where it disagrees with the preparer (on top), and the audit file behind any sampled item. */
(async function () {
  const { get, usd, esc, cap, cite, day, role } = SO;
  const view = document.getElementById("view"), summary = document.getElementById("summary");
  const seg = document.getElementById("client"), dl = document.getElementById("download");
  const pct = (x) => (x == null ? "n/a" : (x * 100).toFixed(x === 1 ? 0 : 1) + "%");
  const VERDICT = { agree: "Agrees", disagree: "Disagrees", cautious: "Preparer more cautious", cannot_conclude: "Cannot conclude" };
  const STRATUM = { model_resolved: "Resolved by the model", material: "Material amount", random: "Random draw", escalated: "Escalation" };
  const STATUS = { pass: "Pass", exceptions: "Exceptions", not_testable: "Not testable" };
  const ACTION = { match: "Matched", match_adjust: "Matched, adjusted", book: "Booked", carry_forward: "Carried forward", escalate: "Escalated", cannot_conclude: "Cannot conclude" };
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  let client = "A";
  try { client = new URLSearchParams(location.search).get("client") || sessionStorage.getItem("so-audit-client") || "A"; } catch (e) {}

  function findingRow(f, c) {
    return `<div class="finding"><div><span class="sev sev-${esc(f.severity)}">${esc(f.severity)}</span> <b>${esc(f.title)}</b></div>
      <div class="muted small">${esc(f.detail)}</div>
      <div class="cites">${(f.evidence_ids || []).map((id) => cite(c, id)).join("")}${f.route_to ? `<span class="state state-escalate">Routed to ${esc(role(f.route_to))}</span>` : ""}</div></div>`;
  }

  function checkRow(k, c) {
    const items = k.findings || (k.details || []).map((d) => ({ severity: "high", title: d, detail: "", evidence_ids: [] }));
    const notes = items.filter((f) => f.severity === "note").length;
    return `<div class="check"><span class="code">${esc(k.code)}</span>
      <div><div>${esc(k.name)}</div><div class="what">${esc(k.what)}</div></div>
      <span class="runrow"><span class="faint small num">${k.tested.toLocaleString()} tested</span><span class="state ${k.status === "pass" ? "" : k.status === "exceptions" ? "v-exceptions" : "state-proposed"}">${esc(STATUS[k.status] || k.status)}</span></span>
      ${items.length || k.note ? `<details ${k.status === "exceptions" ? "open" : ""}><summary>${k.status === "pass" && notes ? plural(notes, "case") + " where the control held" : plural(items.length, "observation")}</summary>
        ${k.note ? `<p class="muted small">${esc(k.note)}</p>` : ""}${items.map((f) => findingRow(f, c)).join("")}</details>` : ""}</div>`;
  }

  async function openFile(c, id) {
    let dlg = document.getElementById("auditfile");
    if (!dlg) {
      dlg = document.createElement("dialog"); dlg.id = "auditfile"; dlg.className = "panel record file";
      document.body.appendChild(dlg);
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    }
    dlg.innerHTML = `<div class="panel-body muted">Opening the audit file for ${esc(id)}</div>`;
    if (!dlg.open) dlg.showModal();
    const f = await get(`/api/audit/${c}/file/${id}`);
    const p = f.preparer, a = f.auditor, m = a.model && a.model.auditor;
    const adj = (xs) => (xs || []).map((x) => `${usd(Math.abs(x.amount))} to ${esc(x.account)}`).join(", ") || "none";
    dlg.innerHTML = `<div class="panel-head"><h3>Audit file <span class="mono faint">${esc(f.item_id)}</span></h3>
        <span class="runrow"><span class="state v-${esc(a.verdict)}">${esc(VERDICT[a.verdict])}</span><form method="dialog"><button class="btn btn-ghost btn-sm">Close</button></form></span></div>
      <div class="panel-body stack">
        <div class="txn"><div><div class="desc">${esc(f.record.description || f.record.memo || "")}</div><div class="faint small">${esc(day(f.record.date))} · ${esc(STRATUM[f.stratum])}</div></div><div class="amt num">${usd(f.record.amount)}</div></div>
        <div class="two">
          <div><div class="label">Preparer · ${esc(p.tier)}</div><div><b>${esc(ACTION[p.action])}</b>${p.escalate_to ? " to " + esc(role(p.escalate_to)) : ""}</div>
            <div class="small muted">Ledger: ${p.ledger_ids.map((x) => cite(c, x)).join(" ") || "none"}</div><div class="small muted">Adjustments: ${adj(p.adjustments)}</div>
            ${(p.control_flags || []).map((x) => `<div class="small"><span class="state state-escalate">${esc(cap(x.flag.replace(/_/g, " ")))}</span></div>`).join("")}</div>
          <div><div class="label">Auditor · ${m ? "re-performed by a model, blind" : "re-computed in code"}</div>
            ${m ? `<div><b>${esc(ACTION[m.action])}</b></div><div class="small muted">Ledger: ${m.ledger_ids.map((x) => cite(c, x)).join(" ") || "none"}</div><div class="small muted">Adjustments: ${adj(m.adjustments)}</div><div class="small">${esc(m.basis)}</div>
              <div class="faint small">Case given: ${a.model.case_size.candidates} candidate entries, ${a.model.case_size.documents} documents, ${a.model.case_size.policy_rules} signed rules. Never the preparer's answer. ${usd(a.model.usage.cost_usd)}</div>` : ""}
            <div class="small">${esc(a.why)}</div></div>
        </div>
        <div><div class="label">Re-computation</div>${a.checks.map((k) => `<div class="ck"><span class="res ${k.result === "fail" ? "fail" : "faint"}">${esc(k.result)}</span><span>${esc(cap(k.check))} <span class="faint">${esc(k.detail)}</span></span></div>`).join("")}</div>
        ${f.rule ? `<div class="rule-text"><div class="rule-top"><span class="label">Authority · playbook v${esc(f.rule.playbook_version)}</span><span class="state ${f.rule.status === "approved" ? "" : "state-proposed"}">${esc(cap(f.rule.status || ""))}</span></div>
          <div>${esc(f.rule.text)} <span class="cite">${esc(f.rule.id)}</span></div>
          <div class="faint small">${esc(cap(f.rule.origin || ""))}, added in v${esc(f.rule.version_added ?? "?")}${f.rule.agrees_with_history != null ? `. Agrees with ${f.rule.agrees_with_history} past cases, conflicts with ${f.rule.conflicts_with_history}` : ""}</div></div>` : ""}
        <dl class="kv small"><dt>Approved by</dt><dd>${esc(cap(f.approved_by))}</dd>
          ${f.approvals.length ? `<dt>Approvals log</dt><dd>${f.approvals.map((x) => `${cite(c, x.id)} ${esc(x.approver)}, ${esc(x.status)}`).join("; ")}</dd>` : ""}
          ${f.ledger_lines.length ? `<dt>Ledger lines</dt><dd>${f.ledger_lines.map((l) => `${cite(c, l.id)} ${usd(l.amount)} ${esc(l.account)} ${esc(day(l.date))}`).join("<br>")}</dd>` : ""}
          ${f.documents.length ? `<dt>Documents</dt><dd>${f.documents.map((d) => `${cite(c, d.id)} ${esc(d.subject)}`).join("<br>")}</dd>` : ""}
          ${f.precedent_ids.length ? `<dt>Precedents</dt><dd class="cites">${f.precedent_ids.map((x) => cite(c, x)).join("")}</dd>` : ""}</dl>
      </div>`;
  }

  async function render() {
    try { sessionStorage.setItem("so-audit-client", client); } catch (e) {}
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === client)));
    dl.href = `/api/audit/${client}/download`;
    let r;
    try { r = await get(`/api/audit/${client}`); } catch (e) {
      summary.innerHTML = ""; view.innerHTML = `<div class="panel error">No audit for this client yet. Run <span class="mono">uv run python -m shadow.auditor --client ${esc(client)} --run runs/${esc(client)}_2026-04_corrected</span></div>`; return;
    }
    const t = r.team, rp = r.reperformance, sev = r.findings_by_severity;
    const tests = r.controls.length + r.consistency.length, passed = [...r.controls, ...r.consistency].filter((k) => k.status === "pass").length;
    summary.innerHTML = `<div class="headline">
      <div><span class="fig num">${pct(rp.agreement_rate)}</span><span class="small muted">agreement on ${rp.concluded} re-performed items, ${rp.model_reperformed} by a model that never saw the preparer's answer</span></div>
      <div><span class="fig num">${r.findings.length}</span><span class="small muted">${plural(r.findings.length, "finding")}: ${sev.high} high, ${sev.medium} medium, ${sev.low} low</span></div>
      <div><span class="fig num">${passed}/${tests}</span><span class="small muted">control and consistency tests passed${[...r.controls, ...r.consistency].some((k) => k.status === "not_testable") ? ", one not testable on this client's data" : ""}</span></div>
      <div><span class="fig num">${SO.cost(r.cost_usd)}</span><span class="small muted">to audit ${esc(r.client_name)}, ${esc(SO.period(r.period))}</span></div></div>`;

    const order = { disagree: 0, cannot_conclude: 1, cautious: 2, agree: 3 };
    const rows = r.sampled_items.slice().sort((a, b) => order[a.verdict] - order[b.verdict] || Math.abs(b.amount) - Math.abs(a.amount));
    view.innerHTML = `<div class="stack">
      <section class="panel"><div class="panel-body team">
        <div class="seat"><div class="label">Preparer</div><div class="big num">${t.preparer.items} items</div>
          <div class="small">Matcher ${t.preparer.matcher}, playbook rules ${t.preparer.rules}, model ${t.preparer.model}. Resolved ${t.preparer.self_resolved} itself, carried ${t.preparer.carried_forward} forward.</div></div>
        <div class="hand"><b class="num">${t.approver.escalated}</b><i>→</i><span>escalated</span></div>
        <div class="seat"><div class="label">Approver</div><div class="big num">${plural(t.approver.signed_rules, "signed rule")}</div>
          <div class="small">${esc(t.approver.roles.map((x) => cap(role(x).replace(/^the /, ""))).join(", ") || "No one")} decide what is escalated, and sign the playbook once instead of every line.</div></div>
        <div class="hand"><b class="num">${r.sample.population}</b><i>→</i><span>dispositions only</span></div>
        <div class="seat"><div class="label">Auditor</div><div class="big num">${t.auditor.sampled} sampled</div>
          <div class="small">${t.auditor.controls} control tests, ${t.auditor.model_reperformed} items re-performed blind. ${plural(t.auditor.routed_back, "finding")} routed back to a person.</div></div>
      </div></section>

      ${r.findings.length ? `<section class="panel"><div class="panel-head"><h3>Findings</h3><span class="faint small">Highest severity first. Findings in this run come before findings in the client's own history.</span></div>
        <div class="panel-body">${r.findings.map((f) => findingRow(f, client)).join("")}</div></section>` : ""}

      <section class="panel"><div class="panel-head"><h3>Re-performance</h3><span class="faint small">${esc(r.sample.rule)} Seed ${r.sample.seed}, materiality ${usd(r.sample.materiality, 0)}.</span></div>
        <div class="table-wrap"><table class="grid"><thead><tr><th>Item</th><th>Why sampled</th><th>What it is</th><th class="r">Amount</th><th>Preparer</th><th>Auditor</th><th>Verdict</th></tr></thead>
        <tbody>${rows.map((x) => `<tr class="pick" data-id="${esc(x.item_id)}"><td class="mono">${esc(x.item_id)}</td><td>${esc(STRATUM[x.stratum])}</td><td class="wrap">${esc(x.text)}</td><td class="r">${usd(x.amount)}</td>
          <td>${esc(ACTION[x.action])} <span class="tier">${esc(x.tier)}</span></td><td>${x.by_model ? "Model, blind" : "Code"}</td><td><span class="state v-${esc(x.verdict)}">${esc(VERDICT[x.verdict])}</span></td></tr>`).join("")}</tbody></table></div></section>

      <div class="cols">
        <section class="panel"><div class="panel-head"><h3>Control tests</h3><span class="faint small">Deterministic, $0.00</span></div><div>${r.controls.map((k) => checkRow(k, client)).join("")}</div></section>
        <section class="panel"><div class="panel-head"><h3>Same transaction, same meaning</h3><span class="faint small">Consistency across the run</span></div><div>${r.consistency.map((k) => checkRow(k, client)).join("")}</div></section>
      </div>
      <p class="faint small">${esc(r.independence)} Run ${esc(r.run_id)}, playbook v${esc(r.playbook_version)}${r.auditor_model ? ", auditor model " + esc(r.auditor_model) : ""}.</p>
    </div>`;
    view.querySelectorAll("tr.pick").forEach((tr) => tr.addEventListener("click", () => openFile(client, tr.dataset.id)));
  }

  let clients = [];
  try { clients = await get("/api/clients"); } catch (e) { clients = [{ id: "A", name: "Client A" }, { id: "B", name: "Client B" }]; }
  seg.innerHTML = clients.map((c) => `<button data-v="${esc(c.id)}">${esc(c.name)}</button>`).join("");
  seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { client = b.dataset.v; render(); }));
  if (!clients.some((c) => c.id === client)) client = clients[0] ? clients[0].id : "A";
  render();
})();
