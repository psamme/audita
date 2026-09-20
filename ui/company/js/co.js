/* Shared helpers for the company screens. Extends the demo's SO namespace rather than replacing
   it, so every formatter and rendered part stays shared. */
(function () {
  const esc = SO.esc;

  async function req(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: { accept: "application/json", ...(body ? { "content-type": "application/json" } : {}) },
      body: body === undefined ? undefined : (typeof body === "string" ? body : JSON.stringify(body)),
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text.slice(0, 400) }; }
    if (!res.ok) {
      const err = new Error(detailOf(data) || `${res.status} ${res.statusText}`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function detailOf(data) {
    if (!data) return "";
    const d = data.detail !== undefined ? data.detail : data;
    if (typeof d === "string") return d;
    if (d && d.message) return d.message;
    return "";
  }

  const get = (p) => req("GET", p);
  const post = (p, b) => req("POST", p, b);
  const postRaw = async (p, raw) => {
    const res = await fetch(p, { method: "POST", headers: { "content-type": "text/csv" }, body: raw });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(detailOf(data) || res.statusText);
    return data;
  };

  /* Poll a job until it stops. onTick gets every record so the screen can show the phase. */
  async function watch(jobId, onTick) {
    for (;;) {
      const rec = await get(`/api/jobs/${jobId}`);
      if (onTick) onTick(rec);
      if (["done", "error", "lost"].includes(rec.state)) return rec;
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  const elapsed = (rec) => {
    const start = new Date(rec.started_at).getTime();
    const end = rec.finished_at ? new Date(rec.finished_at).getTime() : Date.now();
    const s = Math.max(0, Math.round((end - start) / 1000));
    return s < 90 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
  };

  /* Honest progress: a named phase and time spent against a typical range, never a fake bar. */
  function jobLine(rec) {
    if (!rec) return "";
    if (rec.state === "error") return `<div class="note bad">That did not finish: ${esc(rec.error || "unknown error")}</div>`;
    if (rec.state === "lost") return `<div class="note bad">The server restarted while this was running. Start it again.</div>`;
    if (rec.state === "done") return `<div class="note good">Finished in ${elapsed(rec)}.</div>`;
    const est = rec.estimate_s ? ` · usually about ${Math.round(rec.estimate_s / 60) || 1} min` : "";
    return `<div class="note"><span class="spin" aria-hidden="true"></span> ${esc(rec.phase || rec.state)} · ${elapsed(rec)}${est}</div>`;
  }

  function levelPill(level) {
    const cls = { ok: "state-book", warn: "state-carry", block: "state-escalate" }[level] || "state";
    const word = { ok: "ready", warn: "worth knowing", block: "stops you" }[level] || level;
    return `<span class="state ${cls}">${word}</span>`;
  }

  function checks(list) {
    if (!list || !list.length) return "";
    const order = { block: 0, warn: 1, ok: 2 };
    const rows = [...list].sort((a, b) => order[a.level] - order[b.level]).map((c) => `
      <tr class="r lvl-${c.level}">
        <td>${levelPill(c.level)}</td>
        <td><b>${esc(c.check)}</b><div class="muted">${esc(c.detail)}</div>
          ${c.fix ? `<div class="faint fix">${esc(c.fix)}</div>` : ""}</td>
      </tr>`).join("");
    return `<div class="table-wrap"><table class="grid tight"><tbody>${rows}</tbody></table></div>`;
  }

  function empty(title, body) {
    return `<section class="panel"><div class="panel-body"><h3>${esc(title)}</h3><p class="muted">${body}</p></div></section>`;
  }

  function err(e) {
    return `<section class="panel"><div class="panel-body"><h3>Something went wrong</h3>
      <p class="muted">${esc(e.message || e)}</p>
      <p class="faint">Refresh to reconnect, or <a href="../queue.html?track=stage&present=1">open the prepared review queue</a>.</p>
      </div></section>`;
  }

  window.CO = { get, post, postRaw, watch, jobLine, elapsed, checks, levelPill, empty, err, detailOf };
})();
