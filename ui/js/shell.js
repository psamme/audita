/* Shared by every screen: stone toggle and the marble ground. */
(function () {
  const root = document.documentElement;
  const canvas = document.getElementById("slab");
  const SEED = 11, VEINING = 0.75;

  const NAV = [["index.html", "Experiment"], ["index.html#control", "Bank change"], ["queue.html", "Review queue"], ["playbook.html", "Playbook"], ["results.html", "Results"]];
  const here = location.pathname.split("/").pop() || "index.html";
  const nav = document.querySelector(".nav");
  if (nav) nav.innerHTML = NAV.map(([href, label]) => `<a href="${href}"${href === here ? ' aria-current="page"' : ""}>${label}</a>`).join("");

  let stone = matchMedia("(prefers-color-scheme: dark)").matches ? "nero" : "statuario";
  try { stone = localStorage.getItem("so-stone") || stone; } catch (e) {}

  function paint() {
    requestAnimationFrame(() => Marble.paint(canvas, { stone, veining: VEINING, seed: SEED }));
  }
  function apply() {
    root.setAttribute("data-stone", stone);
    document.querySelectorAll("#stone button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === stone)));
    try { localStorage.setItem("so-stone", stone); } catch (e) {}
    paint();
  }
  document.querySelectorAll("#stone button").forEach((b) => b.addEventListener("click", () => { stone = b.dataset.v; apply(); }));
  let t = 0;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(paint, 200); });
  apply();
})();
