/* Pieces every screen shares: wording for outcomes, rationale splitting,
   citation chips that open the underlying record, and the playbook diff. */
(function () {
  const { usd, esc, day } = SO;
  const ROLE = { controller: "the controller", ar_lead: "the AR lead", ap_lead: "the AP lead", owner: "the owner", ops_manager: "the ops manager" };
  const role = (r) => ROLE[r] || (r ? "the " + String(r).replace(/_/g, " ") : "a person");
  const period = (p) => new Date(p + "-15T12:00:00").toLocaleDateString("en-GB", { month: "short", year: "numeric" });
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");

  function outcome(res) {
    if (res.action === "escalate") return { cls: "state-escalate", text: "Escalated to " + role(res.escalate_to), short: cap(role(res.escalate_to).replace(/^the /, "")) };
    if (res.action === "carry_forward") return { cls: "state-carry", text: "Carried forward", short: "Carried forward" };
    if (res.action === "book") return { cls: "state-book", text: "Booked", short: "Booked" };
    if (res.action === "match_adjust") return { cls: "state-book", text: "Matched with adjustment", short: "Matched, adjusted" };
    return { cls: "", text: "Matched", short: "Matched" };
  }

  function verdict(res, chart) {
    const adj = (res.adjustments || []).map((a) => `${usd(Math.abs(a.amount))} to ${a.account}${chart[a.account] ? " " + chart[a.account] : ""}`).join(" and ");
    if (res.action === "escalate") return `Stop and ask ${role(res.escalate_to)}.`;
    if (res.action === "match_adjust") return `Match and book ${adj}.`;
    if (res.action === "book") return `Book ${adj}.`;
    if (res.action === "carry_forward") return "Carry it forward to next period.";
    return "Match it.";
  }

  // The investigator ends its rationale with "Questions for the X: (1) ... (2) ...".
  // Pull those out so they read as questions; anything unexpected stays as prose.
  function splitRationale(text) {
    const m = (text || "").match(/^([\s\S]*?)\bQuestions? for [^:]{1,40}:\s*([\s\S]+)$/);
    if (!m) return { body: (text || "").trim(), questions: [] };
    return { body: m[1].trim(), questions: m[2].split(/\(\d+\)\s*/).map((s) => s.trim()).filter(Boolean) };
  }
  function leadAndRest(body) {
    // split on sentence ends only, so "$12.40" and "A-R-015." style tokens stay whole
    const sentences = body.split(/(?<=[.!?])\s+(?=[A-Z(])/);
    return { lead: sentences.slice(0, 2).join(" "), rest: sentences.slice(2).join(" ") };
  }

  const cite = (client, id) => `<a class="cite" href="/api/record/${esc(client)}/${esc(id)}" data-rec="${esc(client)}/${esc(id)}">${esc(id)}</a>`;

  function reasoning(res) {
    const split = splitRationale(res.rationale);
    // runs made after the contract gained `questions` carry them as a field; older runs only have prose
    const questions = res.questions && res.questions.length ? res.questions : split.questions;
    const { lead, rest } = leadAndRest(split.body);
    return `<div class="why"><p>${esc(lead)}</p>${rest ? `<details><summary>Full reasoning</summary><p>${esc(rest)}</p></details>` : ""}</div>
      ${questions.length ? `<div><div class="label">Asks ${esc(role(res.escalate_to))}</div><ol class="asks">${questions.map((q) => `<li>${esc(cap(q))}</li>`).join("")}</ol></div>` : ""}`;
  }

  function ruleBlock(rule) {
    if (!rule) return "";
    const bt = rule.backtest, n = bt ? bt.support + bt.conflicts : 0;
    return `<div class="rule-text">
      <div class="rule-top"><span class="label">Playbook rule</span><span class="state ${rule.status === "proposed" ? "state-proposed" : ""}">${esc(cap(rule.status || "approved"))}</span></div>
      <div>${esc(rule.text)} <span class="cite">${esc(rule.id)}</span></div>
      ${bt && n ? `<div class="faint small">Agrees with ${bt.support} of ${n} past case${n === 1 ? "" : "s"}</div>` : ""}
    </div>`;
  }

  function diffBlock(diff) {
    if (!diff) return `<p class="muted">The playbook did not change.</p>`;
    const rows = [];
    for (const r of diff.removed || []) rows.push(`<div class="diff-row diff-del"><span class="sign">−</span><span class="txt">${esc(r.text)} <span class="cite">${esc(r.id)}</span></span></div>`);
    for (const c of diff.changed || []) {
      rows.push(`<div class="diff-row diff-del"><span class="sign">−</span><span class="txt">${esc(c.before.text)}</span></div>`);
      rows.push(`<div class="diff-row diff-add"><span class="sign">+</span><span>${esc(c.after.text)} <span class="cite">${esc(c.after.id)}</span></span></div>`);
    }
    for (const r of diff.added || []) rows.push(`<div class="diff-row diff-add"><span class="sign">+</span><span>${esc(r.text)} <span class="cite">${esc(r.id)}</span></span></div>`);
    return rows.length ? `<div class="diff">${rows.join("")}</div>` : `<p class="muted">The playbook did not change.</p>`;
  }

  // Citation chips open the record they point at, in place.
  const LABELS = { posted_at: "Posted", invoice_id: "Invoice", due_date: "Due", counterparty: "Counterparty", escalate_to: "Escalated to" };
  async function openRecord(path) {
    let dlg = document.getElementById("record");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "record"; dlg.className = "panel record";
      document.body.appendChild(dlg);
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    }
    dlg.innerHTML = `<div class="panel-body muted">Opening ${esc(path.split("/")[1])}</div>`;
    if (!dlg.open) dlg.showModal();
    try {
      const res = await fetch("/api/record/" + path);
      if (!res.ok) throw new Error();
      const rec = await res.json();
      const body = rec.body; const skip = new Set(["body", "table", "meta", "chart"]);
      const rows = Object.entries(rec).filter(([k, v]) => !skip.has(k) && v !== null && v !== "" && typeof v !== "object")
        .map(([k, v]) => `<dt>${esc(LABELS[k] || cap(k.replace(/_/g, " ")))}</dt><dd>${typeof v === "number" && /amount/.test(k) ? usd(v) : /date|posted_at/.test(k) && /^\d{4}-\d\d-\d\d$/.test(v) ? day(v) : esc(v)}</dd>`).join("");
      dlg.innerHTML = `<div class="panel-head"><h3>${esc(cap(String(rec.table || "record").replace(/_/g, " ")))} <span class="mono faint">${esc(rec.id)}</span></h3><form method="dialog"><button class="btn btn-ghost btn-sm">Close</button></form></div>
        <div class="panel-body stack"><dl class="kv num">${rows}</dl>${body ? `<pre class="doc">${esc(body)}</pre>` : ""}</div>`;
    } catch (e) {
      dlg.innerHTML = `<div class="panel-head"><h3>Record not available</h3><form method="dialog"><button class="btn btn-ghost btn-sm">Close</button></form></div><div class="panel-body muted">The server is not running, so ${esc(path.split("/")[1])} cannot be opened.</div>`;
    }
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-rec]");
    if (!a) return;
    e.preventDefault();
    openRecord(a.dataset.rec);
  });

  Object.assign(SO, { role, cap, period, outcome, verdict, splitRationale, leadAndRest, cite, reasoning, ruleBlock, diffBlock, openRecord });
})();
