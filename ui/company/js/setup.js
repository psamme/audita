/* Step 1. Writes the client row and the roster.

   The form is drawn once and then left alone. Editing a field updates state and, at most, the one
   derived thing that depends on it (the sentence naming who gets escalations, and the list of
   people who could be the usual reconciler). Re-rendering the form while someone is typing in it
   destroys focus and discards anything they had entered but not saved.

   The senior checkbox is the one control here that silently decides whether the product works at
   all: escalations are addressed to a senior role, and with none of them the agent rejects every
   escalation it tries to make. So the form says so, and refuses to submit without one. */
(function () {
  const view = document.getElementById("view");
  const esc = SO.esc;

  const ROLE_SUGGESTIONS = ["bookkeeper", "staff accountant", "ar lead", "ap lead",
    "controller", "finance manager", "owner", "ops manager"];

  const state = {
    created: false,
    client: "",
    name: "",
    blurb: "",
    close_days: 5,
    chart_text: "",
    reconciler: "",
    people: [
      { id: "", name: "", role: "bookkeeper", senior: false },
      { id: "", name: "", role: "controller", senior: true },
    ],
  };

  if(new URLSearchParams(location.search).get('example')==='1') Object.assign(state, {
    name:'Example Company',client:'EXAMPLE',blurb:'Customer receipts against invoices. Finance reviews unusual deductions.',
    chart_text:'1200, Accounts receivable\n6990, Small payment differences\n2000, Accounts payable',reconciler:'clerk',
    people:[{id:'clerk',name:'Jamie',role:'bookkeeper',senior:false},{id:'lead',name:'Riley',role:'finance lead',senior:true}]
  });

  const chartText = (chart) =>
    chart ? Object.entries(chart).map(([k, v]) => `${k}, ${v}`).join("\n") : "";

  const named = () => state.people.filter((p) => p.id.trim());
  const seniors = () => named().filter((p) => p.senior);

  // --- one-time render ------------------------------------------------------------------------
  function render() {
    view.innerHTML = `
      ${state.created ? `<div class="note good">${esc(state.name)} is already set up.
        Changes here update it.</div>` : ""}
      <form class="form" id="f" autocomplete="off">
        <section class="panel"><div class="panel-body">
          <div class="row2">
            <div class="field">
              <label for="name">Company name</label>
              <input class="input" id="name" required value="${esc(state.name)}"
                     placeholder="Northwind Trading">
            </div>
            <div class="field">
              <label for="cid">Workspace ID</label>
              <input class="input" id="cid" required maxlength="8" pattern="[A-Za-z0-9_]{1,8}"
                     value="${esc(state.client)}" ${state.created ? "readonly" : ""}
                     placeholder="NWIND">
              <div class="hint">Created from your company name. You can change it before setup.</div>
            </div>
          </div>
          <div class="field">
            <label for="blurb">What the business does</label>
            <textarea class="input" id="blurb" rows="2"
              placeholder="Wholesale food distribution. Most receipts are customer payments against invoices; most payments are to suppliers and hauliers.">${esc(state.blurb)}</textarea>
            <div class="hint">One or two plain sentences. The model reads this when it writes your
              rules, so say what kinds of money move and why, not what the company sells.</div>
          </div>
          <div class="field narrow">
            <label for="close">Days to close the books</label>
            <input class="input" id="close" type="number" min="0" max="45" value="${state.close_days}">
            <div class="hint">How long after month end your books stay open. Used to spot entries
              posted late.</div>
          </div>
        </div></section>

        <section class="panel">
          <div class="panel-head"><h3>Chart of accounts</h3></div>
          <div class="panel-body">
            <div class="field">
              <textarea class="input" id="chart" rows="6"
                placeholder="1010, Operating cash&#10;1200, Accounts receivable&#10;6110, Bank fees&#10;6990, Cash over and short">${esc(state.chart_text)}</textarea>
              <div class="hint">One account per line, code first. A rule may only book to an
                account listed here.</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h3>Who works on the bank</h3></div>
          <div class="panel-body">
            <table class="people">
              <thead><tr>
                <th>Id</th><th>Name</th><th>Role</th><th>Seniority</th><th></th>
              </tr></thead>
              <tbody id="rows"></tbody>
            </table>
            <div class="actions" style="margin-top: 12px">
              <button type="button" class="btn btn-secondary btn-sm" id="add">Add someone</button>
            </div>
            <p id="seniorline"></p>
            <div class="field">
              <label for="recon">Who normally clears the bank</label>
              <select class="select" id="recon" style="max-width: 24rem"></select>
              <div class="hint">Routine matches we work out for you are recorded against this
                person, so they read as ordinary day-to-day work rather than a decision.</div>
            </div>
          </div>
        </section>

        <div class="actions">
          <button class="btn btn-primary" type="submit" id="save">
            ${state.created ? "Save changes" : "Create company"}</button>
          ${state.created ? `<a class="btn btn-secondary" href="import.html">Next: your history</a>` : ""}
          <span id="msg" class="muted"></span>
        </div>
      </form>`;

    bindField("name", "name");
    document.getElementById('name').addEventListener('input', e => {
      if (!state.created && !document.getElementById('cid').dataset.edited) {
        state.client=e.target.value.toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,8);
        document.getElementById('cid').value=state.client;
      }
    });
    document.getElementById('cid').addEventListener('input', e=>e.target.dataset.edited='1');
    bindField("cid", "client");
    bindField("blurb", "blurb");
    bindField("chart", "chart_text");
    bindField("close", "close_days", (v) => +v || 0);
    document.getElementById("add").addEventListener("click", addPerson);
    document.getElementById("recon").addEventListener("change", (e) => {
      state.reconciler = e.target.value;
    });
    document.getElementById("f").addEventListener("submit", submit);
    renderPeople();
  }

  function bindField(id, key, cast) {
    const el = document.getElementById(id);
    el.addEventListener("input", () => { state[key] = cast ? cast(el.value) : el.value; });
  }

  // --- the roster, the only part that is ever redrawn ------------------------------------------
  // Two different example names, so the blank rows do not look like the same person twice.
  const EXAMPLES = [["jdoe", "Jamie Doe"], ["rsingh", "Ravi Singh"], ["mchen", "Mia Chen"]];

  function renderPeople(focus) {
    const rows = document.getElementById("rows");
    closeCombo();
    rows.innerHTML = state.people.map((p, i) => `
      <tr>
        <td><input class="input" data-f="id" data-i="${i}" value="${esc(p.id)}"
                   placeholder="${EXAMPLES[i % EXAMPLES.length][0]}" aria-label="Person ${i + 1} id"></td>
        <td><input class="input" data-f="name" data-i="${i}" value="${esc(p.name)}"
                   placeholder="${EXAMPLES[i % EXAMPLES.length][1]}" aria-label="Person ${i + 1} name"></td>
        <td><div class="combo">
          <input class="input" data-f="role" data-i="${i}" value="${esc(p.role)}"
                 placeholder="bookkeeper" aria-label="Person ${i + 1} role" autocomplete="off"
                 role="combobox" aria-autocomplete="list" aria-expanded="false"
                 aria-controls="rolelist-${i}">
          <button type="button" class="combo-toggle" tabindex="-1" aria-hidden="true">
            <svg viewBox="0 0 10 6"><path d="M1 1l4 4 4-4"/></svg></button>
          <ul class="combo-list" id="rolelist-${i}" role="listbox"
              aria-label="Role suggestions" hidden></ul>
        </div></td>
        <td><label><input type="checkbox" data-f="senior" data-i="${i}"
                   ${p.senior ? "checked" : ""}> senior</label></td>
        <td><button type="button" class="btn btn-ghost btn-sm" data-rm="${i}"
                    aria-label="Remove person ${i + 1}">Remove</button></td>
      </tr>`).join("");

    rows.querySelectorAll("[data-f]").forEach((el) => {
      const p = state.people[+el.dataset.i];
      const field = el.dataset.f;
      el.addEventListener("input", () => {
        p[field] = el.type === "checkbox" ? el.checked : el.value;
        // Only the two derived bits change, and neither is an input, so nothing steals focus.
        refreshDerived();
      });
      if (field === "senior") el.addEventListener("change", () => { p.senior = el.checked; refreshDerived(); });
    });
    rows.querySelectorAll(".combo").forEach(wireCombo);
    rows.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => {
      state.people.splice(+b.dataset.rm, 1);
      if (!state.people.length) state.people.push({ id: "", name: "", role: "", senior: false });
      renderPeople();
      refreshDerived();
    }));

    if (focus) {
      const el = rows.querySelector(`[data-f="id"][data-i="${focus}"]`);
      if (el) el.focus();
    }
    refreshDerived();
  }

  /* Role used to be a native <datalist>. Two things were wrong with that. The field looked exactly
     like the free-text Id and Name beside it, so nothing said a list was there until it had focus,
     and the browser filters a datalist against whatever is already in the box, so opening a row
     that came prefilled with "bookkeeper" offered one suggestion out of eight. Clicking the field
     here always offers the whole list and typing narrows it. A role that is not on the list is
     still accepted, because these are suggestions and not a fixed set. */
  let openCombo = null;

  function closeCombo() {
    if (!openCombo) return;
    openCombo.list.hidden = true;
    openCombo.wrap.classList.remove("open");
    openCombo.input.setAttribute("aria-expanded", "false");
    openCombo.input.removeAttribute("aria-activedescendant");
    openCombo = null;
  }

  document.addEventListener("mousedown", (e) => {
    if (openCombo && !openCombo.wrap.contains(e.target)) closeCombo();
  });

  function wireCombo(wrap) {
    const input = wrap.querySelector("[data-f='role']");
    const list = wrap.querySelector(".combo-list");
    const toggle = wrap.querySelector(".combo-toggle");
    let shown = [];
    let active = -1;
    let committing = false;

    function paint() {
      list.innerHTML = shown.map((r, n) =>
        `<li role="option" id="${list.id}-o${n}" aria-selected="${n === active}">${esc(r)}</li>`
      ).join("");
      if (active < 0) return input.removeAttribute("aria-activedescendant");
      input.setAttribute("aria-activedescendant", `${list.id}-o${active}`);
      list.children[active].scrollIntoView({ block: "nearest" });
    }

    // all=true is the click and arrow-key path: show everything, whatever is already typed.
    function open(all) {
      const q = input.value.trim().toLowerCase();
      shown = (all || !q) ? ROLE_SUGGESTIONS.slice()
                          : ROLE_SUGGESTIONS.filter((r) => r.includes(q));
      if (!shown.length) return closeCombo();
      closeCombo();
      active = shown.indexOf(q);
      openCombo = { wrap, input, list };
      list.hidden = false;
      wrap.classList.add("open");
      input.setAttribute("aria-expanded", "true");
      paint();
    }

    // State is written in one place, on the [data-f] binding above, so picking goes through it.
    function commit(role) {
      committing = true;
      input.value = role;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      committing = false;
      closeCombo();
    }

    function move(d) {
      if (list.hidden) return open(true);
      active = (active + d + shown.length) % shown.length;
      paint();
    }

    input.addEventListener("click", () => { if (list.hidden) open(true); });
    input.addEventListener("input", () => { if (!committing) open(false); });
    // Deferred, because during focusout the focus has not landed yet: pressing the chevron blurs
    // the field on the way to the button, and closing there would make every press reopen.
    input.addEventListener("focusout", () => setTimeout(() => {
      if (openCombo && openCombo.wrap === wrap && !wrap.contains(document.activeElement)) closeCombo();
    }, 0));
    toggle.addEventListener("click", () => {
      const reopen = list.hidden;
      input.focus();
      if (reopen) open(true); else closeCombo();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter" && !list.hidden) {
        // Enter takes the highlighted role. Without this it would submit the form instead.
        e.preventDefault();
        if (active >= 0) commit(shown[active]); else closeCombo();
      } else if (e.key === "Escape" && !list.hidden) { e.preventDefault(); closeCombo(); }
      else if (e.key === "Tab") closeCombo();
    });
    list.addEventListener("mousedown", (e) => e.preventDefault()); // keep the caret in the field
    list.addEventListener("click", (e) => {
      const li = e.target.closest("li");
      if (li) commit(shown[[...list.children].indexOf(li)]);
    });
  }

  function addPerson() {
    state.people.push({ id: "", name: "", role: "", senior: false });
    renderPeople(state.people.length - 1);
  }

  /* The sentence about escalation and the reconciler list are the only things that depend on the
     roster. Updating them in place is what lets the form stay put while someone types. */
  function refreshDerived() {
    const line = document.getElementById("seniorline");
    const sr = seniors();
    const tickedButUnnamed = state.people.some((p) => p.senior && !p.id.trim());
    if (sr.length) {
      line.className = "faint";
      line.textContent = "Anything the agent will not decide alone goes to: " +
        [...new Set(sr.map((s) => s.role || s.id))].join(", ") + ".";
    } else if (tickedButUnnamed) {
      line.className = "faint";
      line.textContent = "Give the person you ticked as senior an id, and escalations will go to " +
        "them.";
    } else {
      line.className = "note bad";
      line.textContent = "Mark at least one person senior. Escalations are addressed to a senior " +
        "role, and with none of them the agent has nowhere to send the things it should not " +
        "decide by itself.";
    }

    const sel = document.getElementById("recon");
    const options = named();
    const want = state.reconciler;
    sel.innerHTML = options.length
      ? options.map((p) => `<option value="${esc(p.id)}">${esc(p.name || p.id)}</option>`).join("")
      : `<option value="">add someone above first</option>`;
    if (options.some((p) => p.id === want)) sel.value = want;
    else state.reconciler = sel.value || "";
  }

  // --- save -----------------------------------------------------------------------------------
  async function submit(e) {
    e.preventDefault();
    const msg = document.getElementById("msg");
    const roster = named().map((p) => ({
      id: p.id.trim(), name: p.name.trim(), role: (p.role || "staff").trim(), senior: !!p.senior,
    }));
    if (!roster.length) return fail(msg, "Add at least one person.");
    if (!roster.some((p) => p.senior)) return fail(msg, "At least one person has to be senior.");
    const dupes = roster.map((p) => p.id).filter((id, i, a) => a.indexOf(id) !== i);
    if (dupes.length) return fail(msg, `Two people share the id "${dupes[0]}".`);

    msg.className = "muted";
    msg.textContent = "Saving...";
    document.getElementById("save").disabled = true;
    try {
      const out = state.created
        ? await CO.post("/api/onboarding/users",
            { users: roster, reconciler: state.reconciler || null, force: state.forceRoster })
        : await CO.post("/api/onboarding/company", {
            id: state.client.trim(), name: state.name.trim(), blurb: state.blurb.trim(),
            chart_text: state.chart_text, close_days: state.close_days ?? 5,
            users: roster, reconciler: state.reconciler || null });
      msg.className = "note good";
      msg.textContent = "Saved. Taking you to your history...";
      setTimeout(() => { location.href = "import.html"; }, 700);
      return out;
    } catch (err) {
      document.getElementById("save").disabled = false;
      fail(msg, err.message);
      // Removing someone the history refers to is allowed, but not by accident: the second save
      // goes through, and the message above says what it costs.
      if (/appear in your imported history/.test(err.message)) state.forceRoster = true;
    }
  }

  function fail(msg, text) {
    msg.className = "note bad";
    msg.textContent = text;
  }

  // --- start ----------------------------------------------------------------------------------
  (async function init() {
    try {
      const cur = await CO.get("/api/onboarding/company");
      if (cur && cur.created) {
        Object.assign(state, {
          created: true, client: cur.client, name: cur.name || "", blurb: cur.blurb || "",
          close_days: cur.close_days ?? 5, chart_text: chartText(cur.chart),
          reconciler: cur.reconciler || "",
        });
        if (cur.users && cur.users.length) {
          state.people = cur.users.map((u) => ({
            id: u.id, name: u.name || "", role: u.role || "", senior: !!u.senior }));
        }
      }
    } catch (e) {
      // first run, or the server is not up; the empty form is the right thing to show
    }
    render();
  })();
})();
