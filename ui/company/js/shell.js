/* Nav and stone for the company screens. A copy of the demo's shell with its own routes, so the
   demo's shell.js stays exactly as it is. */
(function () {
  const root = document.documentElement;
  const canvas = document.getElementById("slab");
  const SEED = 11, VEINING = 0.75;

  SO.renderNavigation();

  const steps = document.createElement("nav");
  steps.className = "company-steps";
  steps.setAttribute("aria-label", "Company setup progress");
  const page = location.pathname.split('/').pop();
  steps.innerHTML = [["setup.html","1. Company"],["import.html","2. History"],["playbook.html","3. Policies"],["receipts.html","4. New receipts"],["reconcile.html","5. Review"]].map(([url,label]) => `<a href="${url}"${page===url ? ' aria-current="step"' : ''}>${label}</a>`).join('');
  document.querySelector('main').prepend(steps);

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
