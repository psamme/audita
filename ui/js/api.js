/* Reads docs/API.md routes. Until shadow/server.py is up, falls back to ui/fixtures/
   and flags the page so example data is never mistaken for a real run. */
(function () {
  const FIXTURES = { "/api/clients": "fixtures/clients.json", "/api/experiment": "fixtures/experiment.json" };
  let usedFixture = false;

  // Rehearsal: ?track=dev (or any named non-main track) makes both POST paths (and playbook reads) use the dev track, so a
  // practice run never writes the main playbook. Off by default; ?track=main turns it off.
  // It survives navigation within the tab and is always announced by a banner.
  let track = "";
  try {
    const p = new URLSearchParams(location.search).get("track");
    const clean = (v) => (v && v !== "main" && /^[a-z0-9_-]{1,24}$/i.test(v) ? v : "");
    if (p) sessionStorage.setItem("so-track", clean(p));
    track = clean(sessionStorage.getItem("so-track"));
  } catch (e) { const v = new URLSearchParams(location.search).get("track"); track = v && v !== "main" && /^[a-z0-9_-]{1,24}$/i.test(v) ? v : ""; }
  // any track other than main is a rehearsal: "dev" for practice, or a named one such as "curve"
  const rehearsal = track !== "";
  const withTrack = (path) => (rehearsal ? path + (path.includes("?") ? "&" : "?") + "track=" + track : path);
  const body = (obj) => JSON.stringify(rehearsal ? { ...obj, track } : obj);

  function banner(id, html) {
    if (document.getElementById(id)) return;
    const el = document.createElement("div");
    el.id = id; el.className = "banner"; el.setAttribute("role", "status"); el.innerHTML = html;
    document.body.prepend(el);
  }
  if (rehearsal) banner("banner-rehearsal", `<b>REHEARSAL (${track} track)</b><span>Corrections and answers here do not touch the main playbook.</span><a href="?track=main">Turn off</a>`);

  async function get(path) {
    try {
      const res = await fetch(path, { headers: { accept: "application/json" } });
      if (res.ok && (res.headers.get("content-type") || "").includes("json")) return await res.json();
    } catch (e) { /* server not running */ }
    const file = FIXTURES[path.split("?")[0]];
    if (!file) throw new Error("No data for " + path + ". Start the server: uv run uvicorn shadow.server:app --port 8787");
    usedFixture = true;
    banner("banner-fixture", `<b>FIXTURE DATA, NOT LIVE</b><span>The server is not running, so this page shows a saved example.</span>`);
    return (await fetch(file)).json();
  }

  const usd = (n, digits) => (n < 0 ? "−" : "") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits ?? 2, maximumFractionDigits: digits ?? 2 });
  const cost = (n) => (n === 0 ? "$0.00" : usd(n, n < 0.1 ? 3 : 2));
  const day = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  window.SO = { get, track, rehearsal, withTrack, body, usd, cost, day, esc, get usedFixture() { return usedFixture; } };
})();
