"""Run Kestrel's cash desks over the simulated calendar.

  python run.py                         configuration C, seed 7, model off, all three months, then the scoreboard
  python run.py --all                   configurations A, B and C side by side, scoreboard and the one chart
  python run.py --all --seeds 7 8 9     three seeds, range shown
  python run.py --until 2026-01-31      one month
  python run.py --demo                  configuration C up to the hero card, which is left waiting for serve.py
  python run.py --model live            let rung 5, the policy writer and the close memo call a model (logged for replay)
  python run.py --model replay          replay logged model calls with no network
"""
import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from desks.llm import Model  # noqa: E402
from spine.contract import Config  # noqa: E402
from spine.orchestrator import END, Ctx, Run  # noqa: E402
from world import metrics  # noqa: E402
from world.controller import Controller  # noqa: E402
from world.generate import World  # noqa: E402


def world_dir(seed: int) -> Path:
    root = ROOT / "data" / f"seed-{seed}"
    if not (root / "public" / "customers.csv").exists():
        World(seed).build().write(root)
    return root


def hero_card(seed: int) -> tuple[str, str]:
    """(card id, date) of the demo's hero payment. Read by the harness, never by a desk."""
    lines = json.loads((world_dir(seed) / "truth" / "truth.json").read_text())["lines"]
    line_id, line = next((k, v) for k, v in lines.items() if v.get("hero"))
    return "C-" + line_id[2:], line["date"]


def run_one(seed: int, config: str, model: str = "off", until: str = END, hold=(), audit_model: bool = False,
            name: str | None = None, quiet: bool = False) -> Path:
    root, out = world_dir(seed), ROOT / "runs" / f"seed-{seed}"
    out.mkdir(parents=True, exist_ok=True)
    db = out / f"{name or config}.db"
    db.unlink(missing_ok=True)
    ctx = Ctx(root / "public", db, Config.get(config), Model(model, out / "model_calls.jsonl"), seed)
    t0 = time.time()
    Run(ctx, human=Controller(root / "truth"), hold=hold, audit_model=audit_model,
        log=(lambda *_: None) if quiet else print).advance(until)
    ctx.store.set_note("run", "meta", {"seed": seed, "config": config, "model": model, "until": until,
                                       "seconds": round(time.time() - t0, 1)})
    ctx.store.commit()
    return db


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", type=int, nargs="+")
    ap.add_argument("--config", choices=["A", "B", "C"], default="C")
    ap.add_argument("--all", action="store_true", help="run A, B and C")
    ap.add_argument("--model", choices=["off", "live", "replay"], default="off")
    ap.add_argument("--audit-model", action="store_true", help="the audit desk also re-performs with the strongest model")
    ap.add_argument("--until", default=END)
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()

    if a.demo:
        hero, day = hero_card(a.seed)
        db = run_one(a.seed, "C", a.model, until=day, hold=[hero], name="demo")
        print(f"demo run stopped on {day} with {hero} waiting for a human -> {db}\nnow: python serve.py --run demo")
        return

    seeds, configs = a.seeds or [a.seed], ["A", "B", "C"] if a.all else [a.config]
    jobs = [(s, c) for s in seeds for c in configs]
    if len(jobs) > 1 and a.model != "live":
        with ProcessPoolExecutor(max_workers=min(len(jobs), 6)) as pool:
            list(pool.map(run_one, *zip(*[(s, c, a.model, a.until, (), a.audit_model) for s, c in jobs])))
    else:
        for s, c in jobs:
            run_one(s, c, a.model, a.until, audit_model=a.audit_model)
    for s in seeds:
        board = metrics.scoreboard(s, configs, ROOT)
        print(metrics.render(board))
    if len(seeds) > 1:
        print(metrics.render_range([metrics.load(s, ROOT) for s in seeds]))


if __name__ == "__main__":
    main()
