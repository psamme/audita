/* Shared navigation and the light marble background. */
(function () {
  const root = document.documentElement;
  const canvas = document.getElementById("slab");
  const SEED = 11, VEINING = 0.75;

  SO.renderNavigation();

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
