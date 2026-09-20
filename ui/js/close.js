/* Month-end close: one screen, read straight from /api/close/<client>. No model call behind it. */
(function () {
  const { get, withTrack, usd, esc, day } = SO;
  const view = document.getElementById("view"), seg = document.getElementById("client");
  let client = new URLSearchParams(location.search).get("client") === "B" ? "B" : "A";
  const selectedPeriod = new URLSearchParams(location.search).get("period") || (SO.track === "stage" ? "2026-05" : "2026-04");
  const month = (p) => new Date(p + "-15T12:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" });

  function row(r) {
    const go = r.href ? `<a class="cite" href="${esc(r.href)}">${r.status === "OPEN" || r.status === "HELD" ? "Work it" : "Open"}</a>` : "";
    return `<tr class="${r.status === "INFO" ? "info" : ""}">
      <td><span class="st st-${esc(r.status)}">${esc(r.status)}</span></td>
      <td class="what">${esc(r.label)}</td>
      <td class="r num">${r.count == null ? "" : r.count.toLocaleString("en-US")}</td>
      <td class="r num mono">${r.amount == null ? "" : usd(r.amount)}</td>
      <td class="detail" title="${esc(r.detail)}">${esc(r.detail)}</td>
      <td class="r">${go}</td>
    </tr>`;
  }

  function render(s) {
    document.getElementById("title").textContent = `${s.name}, ${month(s.period)}`;
    view.innerHTML = `<div class="stack">
      <div class="panel"><div class="headline-panel">
        <div class="headline">${esc(s.headline)}
          <span class="sub">As of ${esc(day(s.as_of))}. Built from ${s.runs.length} run${s.runs.length === 1 ? "" : "s"} already on file, the books and the playbook. No model call, $0.00.</span>
        </div>
      </div></div>
      <div class="panel"><div class="table-wrap"><table class="grid checks">
        <colgroup><col class="c-status"><col class="c-check"><col class="c-n"><col class="c-usd"><col><col class="c-go"></colgroup>
        <thead><tr><th>Status</th><th>Check</th><th class="r">Items</th><th class="r">Amount</th><th>Detail</th><th></th></tr></thead>
        <tbody>${s.rows.map(row).join("")}</tbody>
      </table></div></div>
      <div class="panel guarantees">${s.guarantees.map((g) => `<div>${esc(g)}</div>`).join("")}
        <div class="faint small">Amounts on cleared and open rows are gross (absolute). The first row and "not yet explained" are net bank movement. Click a row to read its full detail.</div>
      </div>
    </div>`;
    view.querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("a")) tr.classList.toggle("wide"); }));
  }

  async function load() {
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === client)));
    try { render(await get(withTrack(`/api/close/${client}?period=${encodeURIComponent(selectedPeriod)}`))); }
    catch (e) { view.innerHTML = `<div class="panel error">${esc(e.message)}</div>`; }
  }

  (async function init() {
    let names = { A: "Client A", B: "Client B" };
    try { (await get("/api/clients")).forEach((c) => { names[c.id] = c.name; }); } catch (e) {}
    seg.innerHTML = ["A", "B"].map((c) => `<button data-v="${c}">${esc(names[c])}</button>`).join("");
    seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      client = b.dataset.v; history.replaceState(null, "", "?client=" + client); load();
    }));
    load();
  })();
})();
