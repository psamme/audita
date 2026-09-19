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

  function diffBlock(diff, opts) {
    if (!diff) return `<p class="muted">The playbook did not change.</p>`;
    const rows = [];
    for (const r of diff.removed || []) rows.push(`<div class="diff-row diff-del"><span class="sign">−</span><span class="txt">${esc(r.text)} <span class="cite">${esc(r.id)}</span></span></div>`);
    for (const c of diff.changed || []) {
      const moved = Object.keys(c.after.bands || {}).filter((k) => JSON.stringify((c.before.bands || {})[k]) !== JSON.stringify(c.after.bands[k]));
      if (c.before.text !== c.after.text) {
        rows.push(`<div class="diff-row diff-del"><span class="sign">−</span><span class="txt">${esc(c.before.text)}</span></div>`);
        rows.push(`<div class="diff-row diff-add"><span class="sign">+</span><span>${esc(c.after.text)} <span class="cite">${esc(c.after.id)}</span></span></div>`);
      } else if (moved.length) {
        rows.push(`<div class="diff-row"><span class="sign"></span><span>${esc(c.after.text)} <span class="cite">${esc(c.after.id)}</span></span></div>`);
      }
      for (const k of moved) {
        const before = (c.before.bands || {})[k], after = c.after.bands[k];
        const max = Math.max(...[before && before.lo, before && before.hi, after.lo, after.hi].filter((v) => v != null).map(Math.abs)) * 1.5;
        if (before) rows.push(`<div class="diff-row diff-del"><span class="sign">−</span><div class="diff-band">${bandBar(before, { max, client: opts && opts.client })}</div></div>`);
        rows.push(`<div class="diff-row diff-add"><span class="sign">+</span><div class="diff-band">${bandBar(after, { max, client: opts && opts.client })}</div></div>`);
      }
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

  // Why it stopped. Set on every escalation by the pipeline.
  const REASON = { in_band: "Inside the unknown band", no_rule: "No rule covers this", conflicting_precedents: "History disagrees with itself",
    fraud_shaped: "Shaped like fraud", thin_precedent: "Too few past cases" };
  const reasonPill = (res) => (res && res.reason && REASON[res.reason] ? `<span class="state">${esc(REASON[res.reason])}</span>` : "");

  // A threshold is a band, not a number: the rule acts on its own up to `lo` (the largest case history
  // shows handled that way), stays out from `hi` (the smallest case handled another way), and asks a
  // person in between. Each end cites the past item that put it there. side "lower" mirrors it.
  function bandBar(band, opts) {
    opts = opts || {};
    const money = (v) => usd(Math.abs(v));
    const upper = band.side !== "lower";
    // upper: acts from zero up to lo, asks up to hi (open ended when hi is unknown)
    // lower: stays out up to lo, asks up to hi, acts from hi upward (open at the left when lo is unknown)
    const open = upper ? band.hi == null : band.lo == null;
    // a person stated the number: lo and hi are a cent apart, so there is a line and nothing left to ask
    const closed = band.lo != null && band.hi != null && Math.abs(band.hi - band.lo) <= Math.max(0.011, Math.abs(band.lo) * 0.001);
    const marks = [band.lo, band.hi, opts.value].filter((v) => v != null).map(Math.abs);
    const max = opts.max || Math.max(...marks) * (upper && (open || closed) ? 1.7 : 1.25) || 1;
    const pos = (v) => Math.min(100, (Math.abs(v) / max) * 100);
    const lo = band.lo == null ? 0 : pos(band.lo), hi = band.hi == null ? 100 : pos(band.hi);
    const c = opts.client;
    const end = (at, value, precedent) => `<span class="band-end ${at < 8 ? "at-start" : at > 92 ? "at-end" : ""}" style="left: ${at}%"><b class="num">${money(value)}</b>${precedent && c ? cite(c, precedent) : ""}</span>`;
    return `<div class="band" data-max="${max}">
      <div class="band-track">
        <span class="band-seg acts ${upper ? "" : "right"}" style="left: ${upper ? 0 : hi}%; width: ${upper ? lo : 100 - hi}%"></span>
        ${closed ? "" : `<span class="band-seg asks ${open ? (upper ? "open" : "open-left") : ""}" style="left: ${lo}%; width: ${hi - lo}%"></span>`}
        ${opts.value != null ? `<span class="band-mark" style="left: ${pos(opts.value)}%"><i></i><b class="num">${money(opts.value)}</b></span>` : ""}
      </div>
      <div class="band-ends">
        ${band.lo != null ? end(lo, band.lo, band.lo_precedent) : ""}
        ${closed || band.hi == null ? "" : end(hi, band.hi, band.hi_precedent)}
        ${open && !closed ? `<span class="band-end far ${upper ? "" : "far-left"}"><b>No ${upper ? "larger" : "smaller"} one seen</b></span>` : ""}
      </div>
      <div class="legend"><span><i class="band-key acts"></i>Rule applies, $0.00</span>${closed ? "" : `<span><i class="band-key asks"></i>Asks a person</span>`}<span><i class="band-key out"></i>Rule does not apply</span></div>
    </div>`;
  }

  // Questions-to-trust: one client, one line. x is answers given (ordered), y is the share of items
  // resolved without a model or a person. Stepped, because each answer is a discrete event.
  function curveChart(c, id) {
    const pts = (c.points || []).slice().sort((x, y) => x.k - y.k);
    if (!pts.length) return "";
    const W = 640, H = 260, L = 44, R = 16, T = 16, Bm = 34, kMax = Math.max(1, pts[pts.length - 1].k);
    const x = (k) => L + (k / kMax) * (W - L - R), y = (v) => T + (1 - v) * (H - T - Bm);
    let d = `M ${x(pts[0].k)} ${y(pts[0].auto_resolve_rate)}`;
    for (let i = 1; i < pts.length; i++) d += ` H ${x(pts[i].k)} V ${y(pts[i].auto_resolve_rate)}`;
    const target = c.target_auto_resolve_rate, reached = c.questions_to_trust;
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    const step = Math.max(1, Math.ceil(kMax / 8));
    return `<div class="curve" id="${esc(id)}">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of items resolved automatically after each answer">
        ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" class="grid"/><text x="${L - 8}" y="${y(t) + 4}" text-anchor="end" class="axis">${Math.round(t * 100)}%</text>`).join("")}
        ${pts.filter((p) => p.k % step === 0).map((p) => `<text x="${x(p.k)}" y="${H - 12}" text-anchor="middle" class="axis">${p.k}</text>`).join("")}
        ${target != null ? `<line x1="${L}" x2="${W - R}" y1="${y(target)}" y2="${y(target)}" class="target"/><text x="${L + 6}" y="${y(target) - 6}" text-anchor="start" class="axis">Trust line ${Math.round(target * 100)}%</text>` : ""}
        <path d="${d}" class="line" fill="none"/>
        ${pts.map((p, i) => `<circle cx="${x(p.k)}" cy="${y(p.auto_resolve_rate)}" r="${p.k === reached ? 6 : 4}" class="dot ${p.k === reached ? "reached" : ""}" data-i="${i}"/>`).join("")}
        ${pts.map((p, i) => `<rect x="${x(p.k) - 14}" y="${T}" width="28" height="${H - T - Bm}" fill="transparent" class="hit" data-i="${i}"/>`).join("")}
      </svg>
      <div class="curve-x axis-label">Answers given by a person</div>
      <div class="tip" hidden></div>
    </div>`;
  }
  function wireCurve(id, c) {
    const root = document.getElementById(id); if (!root) return;
    const pts = (c.points || []).slice().sort((x, y) => x.k - y.k), tip = root.querySelector(".tip");
    const KIND = { induction: "Induced from the trail", band: "Yes or no on a band", open_question: "An answered question", correction: "A queue correction" };
    root.querySelectorAll(".hit").forEach((h) => {
      h.addEventListener("mouseenter", () => {
        const p = pts[+h.dataset.i];
        tip.innerHTML = `<b>After ${p.k} answer${p.k === 1 ? "" : "s"}</b><span>${esc(KIND[p.answer_kind] || "")}${p.answer ? ": " + esc(String(p.answer).slice(0, 120)) : ""}</span>
          <span class="num">${(p.auto_resolve_rate * 100).toFixed(1)}% resolved on its own · ${p.wrong_matches} wrong match${p.wrong_matches === 1 ? "" : "es"} · ${p.left_for_model_or_human} left over${p.est_llm_cost_usd != null ? " · est. " + usd(p.est_llm_cost_usd) : ""}</span>`;
        tip.hidden = false;
        tip.style.left = Math.min(70, Math.max(0, (+h.getAttribute("x") / 640) * 100 - 10)) + "%";
      });
      h.addEventListener("mouseleave", () => { tip.hidden = true; });
    });
  }

  Object.assign(SO, { reasonPill, bandBar, curveChart, wireCurve, role, cap, period, outcome, verdict, splitRationale, leadAndRest, cite, reasoning, ruleBlock, diffBlock, openRecord });
})();
