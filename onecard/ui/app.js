/* One Payment, Every Desk: five screens over the review API. No framework; every node is built with h(), so
   text from emails and bank lines is never parsed as HTML. */

const view = document.getElementById("view");
const usd = c => (c < 0 ? "-" : "") + "$" + (Math.abs(c) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = x => (x === null || x === undefined) ? "n/a" : (100 * x).toFixed(1) + "%";
const MONTHS = { "2026-01": "January", "2026-02": "February", "2026-03": "March" };
const DESKS = { cash_app: "Cash application", bank_rec: "Bank reconciliation", forecast: "Forecast", audit: "Audit" };
const RULES = { no_unknown_residual: "Every residual is explained", amounts_tie: "Amounts tie", entry_matches_claim: "Entry matches claim",
  one_invoice_one_settlement: "One invoice, one settlement", payer_owns_invoice: "Payer owns the invoice",
  same_reason_everywhere: "Same reason everywhere", forecast_agrees: "Forecast agrees", right_month: "Right month",
  evidence_complete: "Evidence complete" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
async function api(path, body) {
  const res = await fetch("/api" + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}
const panel = (title, right, ...body) => h("section", { class: "panel" },
  h("div", { class: "panel-head" }, h("h3", {}, title), right ? h("div", { class: "muted small" }, right) : null),
  h("div", { class: "panel-body stack" }, body));
const cite = id => id.startsWith("policy:") ? h("a", { class: "cite", href: "#/policies" }, id.slice(7))
  : id.startsWith("card:") ? h("a", { class: "cite", href: "#/card/" + id.slice(5) }, id.slice(5)) : h("span", { class: "cite" }, id);
const cardLink = id => id ? h("a", { class: "cite", href: "#/card/" + id }, id) : h("span", { class: "faint" }, "none");
const state = (label, cls) => h("span", { class: "state " + (cls || "") }, label);
function cardState(c) {
  if (c.status === "escalated") return state("Waiting for a human", "state-escalate");
  if (c.status !== "posted") return state(c.status, "state-proposed");
  return c.human_touch ? state("Posted after a human decision", "state-book") : state("Posted, touchless");
}
function entryTable(lines) {
  return h("table", { class: "entry" }, h("tbody", {}, lines.filter(l => l.debit || l.credit).map(l => h("tr", {},
    h("td", {}, (l.credit ? "  " : "") + l.account.replaceAll("_", " ") + (l.invoice_id ? " " + l.invoice_id : "")),
    h("td", { class: "r" }, l.debit ? usd(l.debit) : ""), h("td", { class: "r" }, l.credit ? usd(l.credit) : "")))));
}

/* ---- review queue ---------------------------------------------------------------------------------------------- */
async function screenQueue(selected) {
  const queue = await api("/queue");
  if (!queue.length) {
    return [h("div", { class: "lede" }, h("div", { class: "label" }, "Review queue"), h("h2", {}, "Nothing is waiting for a human."),
      h("p", {}, "Every escalated card has an answer. Finish the calendar with the scripted controller, or browse the cards.")),
      h("div", { class: "row" }, continueButton(), h("a", { class: "btn btn-secondary", href: "#/cards" }, "Browse cards"))];
  }
  const current = queue.find(q => q.card_id === selected) || queue[0];
  const detail = await api("/cards/" + current.card_id);
  const list = h("div", { class: "panel" }, queue.map(q => h("button", { class: "qrow", "aria-pressed": String(q.card_id === current.card_id),
    onclick: () => { location.hash = "#/queue/" + q.card_id; } },
    h("div", { class: "row spread" }, h("b", {}, usd(q.amount)), h("span", { class: "mono small" }, q.date)),
    h("div", { class: "small" }, q.text), h("div", { class: "small", style: "opacity:.7" }, q.item ? q.item.why_text : "Rule draft waiting for approval"))));
  return [h("div", { class: "lede" }, h("div", { class: "label" }, "Review queue"),
    h("p", {}, "The desks ask only when evidence and policy run out. Each option shows the entry it would post.")),
    h("div", { class: "qlayout" }, list, reviewPane(detail))];
}

function continueButton() {
  const b = h("button", { class: "btn btn-secondary", onclick: async () => {
    b.disabled = true; b.textContent = "Running the calendar…";
    try { await api("/continue", {}); location.hash = "#/scoreboard"; render(); } catch (e) { b.textContent = e.message; }
  } }, "Run to the end of March");
  return b;
}

function reviewPane(detail) {
  const card = detail.card, item = card.review, pane = h("div", { class: "stack" });
  pane.append(bankLine(card));
  if (!item) { pane.append(draftsPane(detail)); return pane; }
  let chosen = null;
  const reason = h("input", { class: "input", placeholder: "Why? The reason is saved with the decision and becomes the rule's text.", "aria-label": "Reason" });
  const submit = h("button", { class: "btn btn-primary", disabled: true }, "Decide");
  const error = h("div", { class: "small muted" });
  const options = item.options.map(o => {
    const b = h("button", { class: "option", "aria-pressed": "false", onclick: () => {
      chosen = o.id; options.forEach(x => x.setAttribute("aria-pressed", String(x === b))); submit.disabled = !reason.value.trim(); } },
      h("div", { class: "name" }, o.label),
      o.entry ? entryTable(o.entry) : h("div", { class: "small muted" }, "No entry preview: the desks rework the card with this answer."),
      o.effects ? h("div", { class: "small muted" }, "Receivables " + usd(o.effects.receivables) + ". Forecast: " + o.effects.forecast + ".") : null);
    return b;
  });
  reason.addEventListener("input", () => { submit.disabled = !(chosen && reason.value.trim()); });
  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      const out = await api(`/cards/${card.card_id}/resolve`, { option_id: chosen, reason: reason.value, approver: "controller" });
      pane.replaceChildren(bankLine(out.card.card), draftsPane(out.card));
    } catch (e) { error.textContent = e.message; submit.disabled = false; }
  });
  pane.append(panel("The question", item.why_text, h("div", { class: "question" }, item.question),
    h("div", { class: "options" }, options), reason, h("div", { class: "row" }, submit, error)));
  if (item.similar.length) pane.append(panel("Similar past decisions", null, h("div", { class: "table-wrap" }, h("table", { class: "grid" },
    h("thead", {}, h("tr", {}, ["Decision", "Card", "Answer", "Amount", "Reason"].map(t => h("th", {}, t)))),
    h("tbody", {}, item.similar.map(s => h("tr", {}, h("td", { class: "mono" }, s.decision_id), h("td", {}, cardLink(s.card_id)),
      h("td", {}, s.treatment.replaceAll("_", " ")), h("td", { class: "r" }, usd(s.amount)), h("td", { class: "wrap" }, s.reason_text))))))));
  pane.append(claimsRow(card), evidencePanel(detail));
  return pane;
}

function draftsPane(detail) {
  const card = detail.card;
  if (!detail.pending_drafts.length) {
    return panel("Decision saved", null, h("div", { class: "row" }, cardState(card), cardLink(card.card_id)),
      h("p", { class: "muted" }, card.status === "posted" ? "The card was reworked with the answer on it and posted." : "The card has another question. It is back in the queue."),
      h("div", { class: "row" }, h("button", { class: "btn btn-secondary", onclick: () => { location.hash = "#/queue"; render(); } }, "Next in queue")));
  }
  const p = detail.pending_drafts[0], status = h("div", { class: "small muted" });
  const approve = h("button", { class: "btn btn-primary", onclick: async () => {
    approve.disabled = true;
    try { await api("/policies/approve", { decision_id: p.decision_id, approver: "controller" }); location.hash = "#/card/" + card.card_id; }
    catch (e) { status.textContent = e.message; approve.disabled = false; }
  } }, "Approve and rework the card");
  return panel("Rule draft from " + p.decision_id, "nothing is live until you approve",
    p.drafts.map(d => h("div", { class: "draft" }, policyHead(d), policyFacts(d))), h("div", { class: "row" }, approve, status));
}

/* ---- one card -------------------------------------------------------------------------------------------------- */
function bankLine(card) {
  const b = card.bank_line;
  return h("section", { class: "panel txn" },
    h("div", {}, h("div", { class: "label" }, `${card.card_id} · ${b.date} · ${card.kind}`), h("div", { class: "desc" }, b.text)),
    h("div", { style: "text-align:right" }, h("div", { class: "amt" }, usd(b.amount)), h("div", { style: "margin-top:8px" }, cardState(card))));
}

function claimBody(c, card) {
  const out = [];
  if (c.desk === "cash_app") {
    out.push(h("dl", { class: "kv" }, h("dt", {}, "Customer"), h("dd", {}, c.customer_id || "unknown"),
      h("dt", {}, "Ladder rung"), h("dd", {}, (c.rung ?? "n/a") + (c.basis ? " · " + c.basis.replaceAll("_", " ") : "") + (c.model ? " · model" : ""))));
    const settles = Object.entries(c.settles || {});
    if (settles.length) out.push(h("table", { class: "entry" }, h("tbody", {}, settles.map(([k, v]) => h("tr", {}, h("td", {}, "settles " + k), h("td", { class: "r" }, usd(v)))))));
    for (const r of c.residuals || []) out.push(h("div", { class: "resid" + (r.treatment ? "" : " open") },
      h("div", { class: "row spread" }, h("b", {}, usd(r.amount) + " " + r.side), h("span", { class: "mono small" }, r.treatment ? r.reason : "UNKNOWN")),
      h("div", { class: "small muted" }, r.treatment ? r.treatment.replaceAll("_", " ") : "no explanation, no policy" + (r.candidate ? ` (looks like ${r.candidate})` : "")),
      r.basis ? h("div", { class: "small faint" }, r.basis) : null,
      h("div", { class: "row" }, r.policy ? cite("policy:" + r.policy) : null, r.decision ? h("span", { class: "cite" }, r.decision) : null)));
    if (c.payout) out.push(h("dl", { class: "kv" }, Object.entries(c.payout).filter(([k]) => k !== "id").flatMap(([k, v]) => [h("dt", {}, k.replaceAll("_", " ")), h("dd", { class: "num" }, usd(v))])));
    if (c.note) out.push(h("div", { class: "small muted" }, c.note));
  } else if (c.desk === "bank_rec") {
    out.push(h("dl", { class: "kv" }, h("dt", {}, "Matched entry"), h("dd", { class: "mono" }, (c.matched_entries || []).join(", ") || "none yet"),
      h("dt", {}, "Difference"), h("dd", { class: "num" }, c.difference === null ? "n/a" : usd(c.difference)),
      h("dt", {}, "Bank's own words"), h("dd", {}, (c.bank_side || []).map(b => `${usd(b.amount)} ${b.reason}`).join(", ") || "nothing")));
    for (const d of c.reversed_duplicates || []) out.push(h("div", { class: "resid" }, h("b", {}, "Duplicate reversed"),
      h("div", { class: "small muted" }, `${d.entry_id} repeated ${d.duplicate_of}; reversed by ${d.reversal}`)));
  } else if (c.desk === "forecast") {
    out.push(h("dl", { class: "kv" }, h("dt", {}, "Fulfils"), h("dd", { class: "mono" }, (c.fulfils || []).join(", ") || "nothing expected"),
      h("dt", {}, "Expected"), h("dd", {}, c.expected || "n/a"), h("dt", {}, "Actual"), h("dd", {}, c.actual),
      h("dt", {}, "Miss"), h("dd", {}, c.miss_days === null ? "n/a" : `${c.miss_days} days` + (c.cause ? ` · ${c.cause}` : "")),
      h("dt", {}, "How"), h("dd", {}, (c.how || "").replaceAll("_", " "))));
  } else if (c.desk === "audit") {
    out.push(h("div", { class: "row" }, state(c.agree ? "Agrees" : "Disagrees", c.agree ? "" : "state-escalate"), h("span", { class: "small muted" }, "sampled: " + c.sampled_because)),
      h("div", { class: "small muted" }, "Rebuilt from raw records and the posted entries only (" + c.method + "). It never saw the other desks' claims."),
      (c.problems || []).map(p => h("div", { class: "resid open small" }, p)));
  }
  out.push(h("div", { class: "row" }, (c.evidence || []).map(cite)));
  return out;
}
const claimsRow = card => h("div", { class: "cols" }, card.claims.map(c => h("section", { class: "panel claim" },
  h("div", { class: "panel-head" }, h("h3", {}, DESKS[c.desk] || c.desk)), h("div", { class: "panel-body" }, claimBody(c, card)))));

function evidencePanel(detail) {
  const rows = detail.evidence.filter(e => e.doc).map(e => {
    const d = e.doc;
    const text = e.kind === "email" ? `${d.date}  ${d.from}\n${d.subject}\n\n${d.body}` : e.kind === "contract" ? d.text
      : e.kind === "inv" ? `${d.invoice_id}  ${d.customer_id}  issued ${d.issue_date}  due ${d.due_date}  ${usd(d.amount)}  ${d.description}`
      : e.kind === "bank" ? `${d.date}  ${usd(d.amount)}  ${d.text}`
      : e.kind === "policy" ? `${d.code} v${d.version} (${d.status}): ${d.reason}`
      : e.kind === "decision" ? `${d.question}\n→ ${d.treatment.replaceAll("_", " ")}: "${d.reason_text}" (${d.approver})` : JSON.stringify(d);
    return h("details", { open: e.kind === "email" || e.kind === "decision" }, h("summary", {}, cite(e.id)), h("div", { class: "pre", style: "margin:8px 0 12px" }, text));
  });
  return panel("Evidence", detail.evidence.length + " records pinned", rows);
}

async function screenCard(id) {
  const detail = await api("/cards/" + id), card = detail.card;
  const checks = h("ul", { class: "checks" }, card.checks.map(k => h("li", { class: k.ok ? "" : "bad" },
    h("span", { class: "tick" }, k.ok ? "ok" : "!!"), h("span", {}, (RULES[k.rule] || k.rule) + (k.detail ? ": " + k.detail : "")))));
  const out = [bankLine(card)];
  if (card.status === "escalated") out.push(h("div", { class: "row" }, h("a", { class: "btn btn-primary", href: "#/queue/" + card.card_id }, "Open in the review queue")));
  out.push(claimsRow(card), h("div", { class: "cols" }, panel("Checker", "nothing posts until these pass", checks),
    panel("Ledger", detail.entries.length ? detail.entries.map(e => e.entry_id).join(", ") : "nothing posted",
      detail.entries.map(e => h("div", { class: "stack", style: "gap:6px" },
        h("div", { class: "small muted" }, `${e.entry_id} · ${e.date} · ${e.source} · prepared ${e.prepared_by}, approved ${e.approved_by}` + (e.reverses ? ` · reverses ${e.reverses}` : "")),
        entryTable(e.lines))))));
  if (detail.decisions.length) out.push(panel("Human decisions", null, detail.decisions.map(d => h("div", { class: "resid" },
    h("div", {}, d.question), h("div", {}, h("b", {}, d.treatment.replaceAll("_", " ")), ` · "${d.reason_text}"`),
    h("div", { class: "small faint" }, `${d.decision_id} · ${d.day} · ${d.approver} · asked because: ${d.saw.why.replaceAll("_", " ")}`)))));
  if (detail.events.length) out.push(panel("History", null, h("ul", { class: "checks" }, detail.events.map(e =>
    h("li", {}, h("span", { class: "tick faint" }, "·"), h("span", {}, `${e.day} ${e.kind.replaceAll("_", " ")}` +
      (e.doc.failed ? `: ${e.doc.failed.join(", ")} → ${e.doc.reopened.join(", ")}` : e.doc.question ? `: ${e.doc.question}` : e.doc.treatment ? `: ${e.doc.treatment}` : "")))))));
  out.push(evidencePanel(detail));
  return out;
}

/* ---- cards list ------------------------------------------------------------------------------------------------ */
async function screenCards(period, filter) {
  period = period || "2026-01"; filter = filter || "all";
  const cards = (await api("/cards?period=" + period)).filter(c => filter === "all" || (filter === "human" && c.human_touch)
    || (filter === "policy" && c.policies.length) || (filter === "reopened" && c.rounds) || (filter === "open" && c.status !== "posted"));
  const seg = (items, cur, href) => h("div", { class: "seg" }, items.map(([v, t]) => h("button", { "aria-pressed": String(v === cur), onclick: () => { location.hash = href(v); } }, t)));
  return [h("div", { class: "row spread" }, seg(Object.entries(MONTHS), period, v => `#/cards/${v}/${filter}`),
    seg([["all", "All"], ["human", "Human touched"], ["policy", "Closed by a rule"], ["reopened", "Reopened"], ["open", "Not posted"]], filter, v => `#/cards/${period}/${v}`)),
    h("section", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Card", "Date", "Bank line", "Amount", "Rung", "State"].map(t => h("th", {}, t)))),
      h("tbody", {}, cards.map(c => h("tr", {}, h("td", {}, cardLink(c.card_id)), h("td", { class: "mono" }, c.date), h("td", { class: "wrap" }, c.text),
        h("td", { class: "r" }, usd(c.amount)), h("td", {}, c.rung ?? ""), h("td", {}, h("div", { class: "row" }, cardState(c), c.policies.map(p => cite("policy:" + p)))))))))),
    h("div", { class: "small muted" }, cards.length + " cards")];
}

/* ---- policy book ----------------------------------------------------------------------------------------------- */
const policyHead = p => h("div", { class: "row spread" }, h("div", { class: "row" }, h("b", { class: "mono" }, `${p.code.split("~")[0]} v${p.version}`),
  state(p.status, p.status === "active" ? "state-book" : p.status === "probation" ? "state-proposed" : "state-carry")),
  h("span", { class: "small muted" }, p.created));
function policyFacts(p, decisions) {
  const cond = Object.entries(p.condition).map(([k, v]) => k.includes("amount") ? `${k.replaceAll("_", " ")} ${usd(v)}` : `${k.replaceAll("_", " ")} ${v}`);
  return h("div", { class: "stack", style: "gap:8px" }, h("div", {}, `"${p.reason}"`),
    h("dl", { class: "kv" }, h("dt", {}, "Scope"), h("dd", {}, p.scope.customers === "any" ? "any customer" : p.scope.customers.join(", ")),
      h("dt", {}, "When"), h("dd", {}, `${p.scope.reason}` + (cond.length ? " · " + cond.join(" · ") : "")),
      h("dt", {}, "Then"), h("dd", {}, p.action.entry),
      h("dt", {}, "Backtest"), h("dd", {}, `fired on ${p.backtest.fired} past decisions, agreed with ${p.backtest.agreed}, conflicts ${p.backtest.conflicts}`),
      h("dt", {}, "From"), h("dd", { class: "row" }, p.source_decisions.map(d => decisions && decisions[d] ? h("a", { class: "cite", href: "#/card/" + decisions[d].card_id }, d) : h("span", { class: "cite" }, d))),
      p.uses ? [h("dt", {}, "Used"), h("dd", { class: "row" }, p.uses.length ? p.uses.map(u => cardLink(u.card_id)) : "not yet")] : null));
}
async function screenPolicies() {
  const { policies, decisions } = await api("/policies");
  const codes = Object.keys(policies).sort();
  if (!codes.length) return [h("div", { class: "lede" }, h("h2", {}, "The policy book is empty."), h("p", {}, "No human decision has become a rule yet in this run."))];
  return [h("div", { class: "lede" }, h("div", { class: "label" }, "Policy book"),
    h("p", {}, "Every rule came from a human decision, was backtested against all past decisions, and runs as plain code. Old versions stay.")),
    codes.map(code => panel(code, policies[code].length + " versions, " + policies[code].reduce((n, p) => n + p.uses.length, 0) + " uses",
      h("div", { class: "versions" }, [...policies[code]].reverse().map(p => h("div", { class: "version" + (["active", "probation"].includes(p.status) ? "" : " old") },
        h("div", { class: "v" }, "v" + p.version), h("div", { class: "stack", style: "gap:8px" }, policyHead(p), policyFacts(p, decisions)))))))];
}

/* ---- close ----------------------------------------------------------------------------------------------------- */
async function screenClose(period) {
  const [closes, findings] = await Promise.all([api("/close"), api("/findings")]);
  const periods = Object.keys(closes);
  if (!periods.length) return [h("div", { class: "lede" }, h("h2", {}, "No month has closed yet."), h("p", {}, "The close desk runs on the last day of the month."))];
  period = closes[period] ? period : periods[periods.length - 1];
  const r = closes[period];
  return [h("div", { class: "row spread" }, h("div", { class: "seg" }, periods.map(p => h("button", { "aria-pressed": String(p === period), onclick: () => { location.hash = "#/close/" + p; } }, MONTHS[p]))),
    state(r.locked ? "Locked" : "Not locked", r.locked ? "state-book" : "state-escalate")),
    h("div", { class: "tiles" }, ["cash", "ar", "stripe_clearing", "customer_credits", "deferred_revenue"].map(a =>
      h("a", { class: "balance", href: `#/account/${a}/${period}` }, h("span", { class: "label" }, a.replaceAll("_", " ")), h("span", { class: "v" }, usd(r.balances[a] || 0)),
        h("span", { class: "small muted" }, "every line behind this")))),
    h("div", { class: "cols" }, panel("Checklist", "the month does not lock until this passes", h("ul", { class: "checks" }, r.checklist.map(c =>
      h("li", { class: c.ok ? "" : "bad" }, h("span", { class: "tick" }, c.ok ? "ok" : "!!"), h("span", {}, c.item, c.detail ? h("div", { class: "small faint" }, c.detail) : null))))),
      panel("Close memo", null, h("p", {}, r.memo),
        h("div", { class: "label" }, "Human decisions this month"), r.human_decisions.length ? h("ul", { class: "checks" }, r.human_decisions.map(d =>
          h("li", {}, h("span", { class: "tick faint" }, "·"), h("span", {}, cardLink(d.card), " ", d.answer.replaceAll("_", " "), h("div", { class: "small faint" }, d.why))))) : h("div", { class: "muted small" }, "none"),
        h("div", { class: "label" }, "Policy changes"), r.policy_changes.length ? r.policy_changes.map(p => h("div", { class: "small" }, p)) : h("div", { class: "muted small" }, "none"))),
    r.contradictions.length ? panel("Contradictions left in the books", r.contradictions.length, h("ul", { class: "checks" }, r.contradictions.map(c =>
      h("li", { class: "bad" }, h("span", { class: "tick" }, "!!"), h("span", {}, c.card_id ? cardLink(c.card_id) : null, " ", (RULES[c.rule] || c.rule.replaceAll("_", " ")) + ": " + c.detail))))) : null,
    panel("Audit findings", `${r.audit.sampled} cards re-performed`, findings.filter(f => f.period === period).length ? h("ul", { class: "checks" },
      findings.filter(f => f.period === period).map(f => h("li", {}, h("span", { class: "tick" }, f.severity === "high" ? "!!" : "·"),
        h("span", {}, f.control.replaceAll("_", " ") + ": " + f.detail, f.card_id ? [" ", cardLink(f.card_id)] : null)))) : h("div", { class: "muted small" }, "no findings"))];
}

async function screenAccount(account, period) {
  const a = await api(`/accounts/${account}` + (period ? `?period=${period}` : ""));
  return [h("div", { class: "lede" }, h("div", { class: "label" }, a.account.replaceAll("_", " ") + (period ? " through " + MONTHS[period] : "")), h("h2", { class: "num" }, usd(a.balance)),
    h("p", {}, "Every ledger line behind the balance. Lines that came from a bank payment link to its card.")),
    h("section", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Date", "Entry", "Card", "Source", "Memo", "Debit", "Credit"].map(t => h("th", {}, t)))),
      h("tbody", {}, a.lines.map(l => h("tr", {}, h("td", { class: "mono" }, l.date), h("td", { class: "mono" }, l.entry_id), h("td", {}, l.card_id ? cardLink(l.card_id) : ""),
        h("td", {}, l.source), h("td", { class: "wrap" }, l.memo), h("td", { class: "r" }, l.debit ? usd(l.debit) : ""), h("td", { class: "r" }, l.credit ? usd(l.credit) : "")))))))];
}

/* ---- scoreboard ------------------------------------------------------------------------------------------------ */
async function screenScoreboard() {
  let board;
  try { board = await api("/scoreboard"); } catch (e) { return [h("div", { class: "lede" }, h("h2", {}, "No scoreboard yet."), h("p", {}, e.message))]; }
  const svg = await (await fetch("/api/chart.svg")).text();
  const chart = h("div", { class: "chart" });
  chart.innerHTML = svg;  // our own generated file; inline so the point tooltips work
  const names = { A: "A  Independent desks", B: "B  Cards, no memory", C: "C  Full" }, configs = Object.keys(board.configs).sort();
  const metric = (label, f) => h("tr", {}, h("td", { class: "wrap" }, label), configs.flatMap(c => Object.keys(MONTHS).map(p =>
    h("td", { class: "r" }, board.configs[c].months[p] ? f(board.configs[c].months[p]) : ""))));
  const table = h("table", { class: "grid" }, h("thead", {},
    h("tr", {}, h("th", {}, ""), configs.map(c => h("th", { colspan: 3 }, names[c]))),
    h("tr", {}, h("th", {}, "Metric"), configs.flatMap(() => Object.values(MONTHS).map(m => h("th", { class: "r" }, m.slice(0, 3)))))),
    h("tbody", {}, metric("Touchless rate, all cards", m => pct(m.touchless_rate)), metric("Touchless rate, customer receipts only", m => pct(m.touchless_rate_receipts)),
      metric("Wrong postings", m => `${m.wrong_postings} (${usd(m.wrong_dollars)})`), metric("Contradictions at close", m => m.contradictions),
      metric("Balance error at close", m => usd(m.balance_error_total)), metric("Human decisions", m => m.human_decisions),
      metric("Escalation precision", m => pct(m.escalation_precision)), metric("Escalation recall (human or learned rule)", m => pct(m.escalation_recall)),
      metric("Lucky guesses on needs-a-human cards", m => m.needs_human_by_authority.none),
      metric("Policy agrees with hidden policy", m => pct(m.policy_accuracy.agree_rate)), metric("Policy contradicts hidden policy", m => pct(m.policy_accuracy.wrong_rate)),
      metric("Audit: planted breaches caught", m => `${m.audit.caught}/${m.audit.planted}`), metric("Audit: false findings", m => m.audit.false_findings),
      metric("Model calls", m => `${m.model_calls} ($${m.model_cost_usd.toFixed(2)})`)));
  const misses = configs.flatMap(c => board.configs[c].misses.map(w => ({ c, ...w })));
  const fc = board.configs.C || board.configs[configs[0]];
  return [h("div", { class: "lede" }, h("div", { class: "label" }, `Scoreboard · seed ${board.seed}`),
    h("p", {}, "Measured against a hidden answer key. B wipes the policy book every month, so the gap between B and C is what memory does; A has no shared card and no checker.")),
    h("section", { class: "panel" }, h("div", { class: "panel-body" }, chart)),
    h("section", { class: "panel" }, h("div", { class: "table-wrap" }, table)),
    panel("Forecast, four weeks ahead", `${fc.forecast.weeks.length} weeks scored`, h("p", {}, `Mean absolute error on weekly customer receipts: ${usd(fc.forecast.learned_mean_abs_error)} with learned payer lags, ${usd(fc.forecast.fixed_mean_abs_error)} with fixed due dates.`)),
    panel("Every miss", misses.length + " across all configurations", misses.length ? h("ul", { class: "checks" }, misses.map(w =>
      h("li", { class: "bad" }, h("span", { class: "tick" }, w.c), h("span", {}, `${MONTHS[w.period]} `, w.card_id ? cardLink(w.card_id) : "no card", ` ${usd(w.dollars)}: ${w.cause}`)))) : h("div", { class: "muted" }, "none")),
    panel("What this does not show", null, h("ul", { class: "limits" },
      h("li", {}, h("b", {}, "Synthetic world, scripted human. "), "Kestrel Inference is invented, and the controller in batch runs answers from a hidden policy file. A real controller is less consistent."),
      h("li", {}, h("b", {}, "The rate counts every card. "), "Outflows and Stripe payouts are clean by construction and pad the all-cards rate; the receipts-only row is the harder number."),
      h("li", {}, h("b", {}, "C does not rise every month. "), "The generator plants more and harder traps each month, and the guardrails ask again for a larger amount, a third repeat, or a new customer. Read C against B, not against its own January."),
      h("li", {}, h("b", {}, "Model off unless stated. "), "With the model off, email-only cases (a parent paying for a subsidiary, a free-text allocation thread) go to the human, which lowers escalation precision."),
      h("li", {}, h("b", {}, "Not built: "), "late remittance after close (trap 12), foreign currency, the QuickBooks mirror. Desks are plain Python functions calling one model wrapper, not Agent SDK subagents over MCP.")))];
}

/* ---- router ---------------------------------------------------------------------------------------------------- */
const routes = { queue: screenQueue, cards: screenCards, card: screenCard, policies: screenPolicies, close: screenClose, account: screenAccount, scoreboard: screenScoreboard };
async function render() {
  const [name, ...args] = (location.hash.replace(/^#\//, "") || "queue").split("/");
  for (const a of document.querySelectorAll("#nav a")) a.setAttribute("aria-current", a.getAttribute("href") === "#/" + (name === "card" ? "cards" : name === "account" ? "close" : name) ? "page" : "false");
  try {
    const nodes = await (routes[name] || screenQueue)(...args.map(decodeURIComponent));
    view.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
    const info = await api("/run");
    document.getElementById("runinfo").textContent = `seed ${info.seed} · run ${info.run} · configuration ${info.meta.config} · model ${info.meta.model} · through ${info.day || "start"}`;
  } catch (e) { view.replaceChildren(h("div", { class: "panel" }, h("div", { class: "panel-body muted" }, "Could not load: " + e.message))); }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", render);
render();
