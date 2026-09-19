/* Reads docs/API.md routes. Until shadow/server.py is up, falls back to ui/fixtures/
   and flags the page so example data is never mistaken for a real run. */
(function () {
  const FIXTURES = { "/api/clients": "fixtures/clients.json", "/api/experiment": "fixtures/experiment.json" };
  let usedFixture = false;

  async function get(path) {
    try {
      const res = await fetch(path, { headers: { accept: "application/json" } });
      if (res.ok && (res.headers.get("content-type") || "").includes("json")) return await res.json();
    } catch (e) { /* server not running */ }
    const file = FIXTURES[path.split("?")[0]];
    if (!file) throw new Error("No data for " + path + ". Start the server: uv run uvicorn shadow.server:app --port 8787");
    usedFixture = true;
    document.documentElement.setAttribute("data-fixtures", "true");
    return (await fetch(file)).json();
  }

  const usd = (n, digits) => (n < 0 ? "−" : "") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits ?? 2, maximumFractionDigits: digits ?? 2 });
  const cost = (n) => (n === 0 ? "$0.00" : usd(n, n < 0.1 ? 3 : 2));
  const day = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  window.SO = { get, usd, cost, day, esc, get usedFixture() { return usedFixture; } };
})();
