/* Step 1. Writes the client row and the roster.

   The senior checkbox is the one control on this page that silently decides whether the product
   works at all: escalations are addressed to a senior role, and with none of them the agent
   rejects every escalation it tries to make. So the form refuses to submit without one and says
   why, rather than letting it fail three screens later. */
(function () {
  const view = document.getElementById("view");
  const esc = SO.esc;
  let people = [
    { id: "", name: "", role: "bookkeeper", senior: false },
    { id: "", name: "", role: "controller", senior: true },
  ];
  let existing = null;

  const ROLE_SUGGESTIONS = ["bookkeeper", "staff accountant", "ar lead", "ap lead",
    "controller", "finance manager", "owner", "ops manager"];

  function peopleRows() {
    return people.map((p, i) => `
      <tr>
        <td><input class="input" data-f="id" data-i="${i}" value="${esc(p.id)}" placeholder="jdoe" aria-label="Short id"></td>
        <td><input class="input" data-f="name" data-i="${i}" value="${esc(p.name)}" placeholder="Jamie Doe" aria-label="Full name"></td>
        <td><input class="input" data-f="role" data-i="${i}" value="${esc(p.role)}" list="roles" aria-label="Role"></td>
        <td><label class="faint"><input type="checkbox" data-f="senior" data-i="${i}" ${p.senior ? "checked" : ""}> senior</label></td>
        <td><button class="btn btn-ghost btn-sm" data-rm="${i}" aria-label="Remove">Remove</button></td>
      </tr>`).join("");
  }

  function render() {
    const seniors = people.filter((p) => p.senior && p.id.trim());
    view.innerHTML = `
      ${existing ? `<div class="note good">${esc(existing.name)} is already set up. Saving again updates it.</div>` : ""}
      <form class="form" id="f">
        <section class="panel"><div class="panel-body">
          <div class="row2">
            <div class="field">
              <label for="name">Company name</label>
              <input class="input" id="name" required value="${esc(existing?.name || "")}" placeholder="Northwind Trading">
            </div>
            <div class="field">
              <label for="cid">Short id</label>
              <input class="input" id="cid" required maxlength="8" pattern="[A-Za-z0-9_]{1,8}"
                     value="${esc(existing?.client || "")}" ${existing ? "readonly" : ""} placeholder="NWIND">
              <div class="hint">Up to eight letters or digits. It prefixes every record id.</div>
            </div>
          </div>
          <div class="field">
            <label for="blurb">What the business does</label>
            <textarea class="input" id="blurb" rows="2"
              placeholder="Wholesale food distribution. Most receipts are customer payments against invoices; most payments are to suppliers and hauliers.">${esc(existing?.blurb || "")}</textarea>
            <div class="hint">One or two plain sentences. The model reads this when it writes your rules, so say what
              kinds of money move and why, not what the company sells.</div>
          </div>
          <div class="field">
            <label for="close">Days to close the books</label>
            <input class="input" id="close" type="number" min="0" max="45" style="max-width: 8rem"
                   value="${existing?.close_days ?? 5}">
            <div class="hint">How long after month end your books stay open. Used to spot entries posted late.</div>
          </div>
        </div></section>

        <section class="panel">
          <div class="panel-head"><h3>Chart of accounts</h3></div>
          <div class="panel-body">
            <div class="field">
              <textarea class="input" id="chart" rows="6" placeholder="1010, Operating cash&#10;1200, Accounts receivable&#10;6110, Bank fees&#10;6990, Cash over and short">${esc(chartText(existing?.chart))}</textarea>
              <div class="hint">One account per line, code first. A rule may only book to an account listed here.</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h3>Who works on the bank</h3></div>
          <div class="panel-body">
            <table class="people">
              <thead><tr><th>Id</th><th>Name</th><th>Role</th><th>Seniority</th><th></th></tr></thead>
              <tbody id="rows">${peopleRows()}</tbody>
            </table>
            <datalist id="roles">${ROLE_SUGGESTIONS.map((r) => `<option value="${r}">`).join("")}</datalist>
            <div class="actions" style="margin-top: 12px">
              <button type="button" class="btn btn-secondary btn-sm" id="add">Add someone</button>
            </div>
            <p class="${seniors.length ? "faint" : "note bad"}" style="margin-top:12px">
              ${seniors.length
                ? `Anything the agent will not decide alone goes to: ${esc(seniors.map((s) => s.role).join(", "))}.`
                : "Mark at least one person senior. Escalations are addressed to a senior role, and with none of them the agent has nowhere to send the things it should not decide by itself."}
            </p>
            <div class="field" style="margin-top:12px; max-width: 24rem">
              <label for="recon">Who normally clears the bank</label>
              <select class="select" id="recon">${people.filter((p) => p.id.trim()).map((p) =>
                `<option value="${esc(p.id)}" ${existing?.reconciler === p.id ? "selected" : ""}>${esc(p.name || p.id)}</option>`).join("")}</select>
              <div class="hint">Routine matches we work out for you are recorded against this person.</div>
            </div>
          </div>
        </section>

        <div class="actions">
          <button class="btn btn-primary" type="submit">${existing ? "Save changes" : "Create company"}</button>
          ${existing ? `<a class="btn btn-secondary" href="import.html">Next: your history</a>` : ""}
          <span id="msg" class="muted"></span>
        </div>
      </form>`;
    wire();
  }

  function chartText(chart) {
    if (!chart) return "";
    return Object.entries(chart).map(([k, v]) => `${k}, ${v}`).join("\n");
  }

  function wire() {
    view.querySelectorAll("[data-f]").forEach((el) => {
      el.addEventListener("input", () => {
        const p = people[+el.dataset.i];
        p[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value;
        if (el.dataset.f === "senior" || el.dataset.f === "id") render();
      });
    });
    view.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => {
      people.splice(+b.dataset.rm, 1); render();
    }));
    document.getElementById("add").addEventListener("click", () => {
      people.push({ id: "", name: "", role: "", senior: false }); render();
    });
    document.getElementById("f").addEventListener("submit", submit);
  }

  async function submit(e) {
    e.preventDefault();
    const msg = document.getElementById("msg");
    const roster = people.filter((p) => p.id.trim()).map((p) => ({
      id: p.id.trim(), name: p.name.trim(), role: (p.role || "staff").trim(), senior: !!p.senior,
    }));
    if (!roster.some((p) => p.senior)) {
      msg.className = "note bad";
      msg.textContent = "At least one person has to be senior.";
      return;
    }
    const payload = {
      id: document.getElementById("cid").value.trim(),
      name: document.getElementById("name").value.trim(),
      blurb: document.getElementById("blurb").value.trim(),
      chart_text: document.getElementById("chart").value,
      close_days: +document.getElementById("close").value || 5,
      users: roster,
      reconciler: document.getElementById("recon").value || null,
    };
    msg.className = "muted";
    msg.textContent = "Saving...";
    try {
      const out = existing
        ? await CO.post("/api/onboarding/users", { users: roster, reconciler: payload.reconciler })
        : await CO.post("/api/onboarding/company", payload);
      existing = out;
      msg.className = "note good";
      msg.textContent = "Saved.";
      setTimeout(() => { location.href = "import.html"; }, 600);
    } catch (err) {
      msg.className = "note bad";
      msg.textContent = err.message;
    }
  }

  (async function init() {
    try {
      const cur = await CO.get("/api/onboarding/company");
      if (cur && cur.created) {
        existing = cur;
        if (cur.users && cur.users.length) {
          people = cur.users.map((u) => ({ id: u.id, name: u.name, role: u.role, senior: !!u.senior }));
        }
      }
    } catch (e) { /* first run, or the server is not up yet */ }
    render();
  })();
})();
