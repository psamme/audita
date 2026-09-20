/* Shared by every screen: stone toggle and the marble ground. */
(function () {
  const root = document.documentElement;
  const canvas = document.getElementById("slab");
  const SEED = 11, VEINING = 0.75;

  const NAV = [["index.html", "Experiment"], ["index.html#control", "Bank change"], ["queue.html", "Review queue"], ["playbook.html", "Playbook"], ["close.html", "Close"], ["audit.html", "Audit"], ["real.html", "Real ledger"], ["results.html", "Results"]];
  const here = location.pathname.split("/").pop() || "index.html";
  const nav = document.querySelector(".nav");
  if (SO.track === "stage") document.querySelector(".mark").href = "queue.html?track=stage";
  const extraNav = NAV;
  const primaryNav = SO.presenting ? [["queue.html?track=stage&present=1", "1. Review queue"], ["playbook.html?client=A&track=stage&period=2026-05&present=1", "2. Learned policy"], ["real.html?track=stage&present=1", "3. Benchmark"]] : NAV;
  if (nav) nav.innerHTML = primaryNav.map(([href, label]) => `<a href="${href}"${href.split('?')[0] === here ? ' aria-current="page"' : ""}>${label}</a>`).join("");

  if (nav && SO.presenting) {
    const more = document.createElement("details"); more.className = "demo-more";
    more.innerHTML = '<summary>More</summary><div>' + extraNav.map(([href,label]) => `<a href="${href}">${label}</a>`).join('') + '<a href="presenter.html">Presenter guide</a><a href="recorded-demo.html">Recorded fallback</a></div>';
    nav.append(more);
  }

  // Only the combined app provides company onboarding.
  if (nav) fetch("/api/onboarding/company").then((r) => {
    if (r.ok && (r.headers.get("content-type") || "").includes("json")) {
      const link = document.createElement("a");
      link.href = "/company/index.html?track=main"; link.textContent = "Workspace";
      (nav.querySelector(".demo-more > div") || nav).append(link);
    }
  }).catch(() => {});

  // Sam picked the white stone (Statuario, medium veining, whole page) as the look. Dark stays a toggle,
  // but the OS theme no longer decides, so the demo opens the same way on any machine.
  let stone = "statuario";
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
