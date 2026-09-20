/* Nav and stone for the company screens. A copy of the demo's shell with its own routes, so the
   demo's shell.js stays exactly as it is. */
(function () {
  const root = document.documentElement;
  const canvas = document.getElementById("slab");
  const SEED = 11, VEINING = 0.75;

  const NAV = [
    ["index.html", "Workspace"],
    ["setup.html", "Company"],
    ["import.html", "History"],
    ["playbook.html", "Playbook"],
    ["receipts.html", "New receipts"],
    ["reconcile.html", "Review"],
    ["../real.html?track=main", "Benchmark"],
  ];
  const here = location.pathname.split("/").pop() || "setup.html";
  const nav = document.querySelector(".nav");
  if (nav) {
    nav.innerHTML = NAV.map(([href, label]) =>
      `<a href="${href}"${href === here ? ' aria-current="page"' : ""}>${label}</a>`).join("");
  }

  const stone = "statuario";
  root.setAttribute("data-stone", stone);
  root.setAttribute("data-theme", "light");
  function paint() {
    requestAnimationFrame(() => Marble.paint(canvas, { stone, veining: VEINING, seed: SEED }));
  }
  let t = 0;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(paint, 200); });
  paint();
})();
