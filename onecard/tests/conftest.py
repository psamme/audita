"""Shared fixtures: one finished run per configuration, built once per test session in a temp folder."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desks.llm import Model  # noqa: E402
from spine.contract import Config  # noqa: E402
from spine.orchestrator import END, Ctx, Run  # noqa: E402
from world import metrics  # noqa: E402
from world.controller import Controller  # noqa: E402
from world.generate import World  # noqa: E402

SEED = 7


@pytest.fixture(scope="session")
def world(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("world") / f"seed-{SEED}"
    World(SEED).build().write(root)
    return root


def finished(world: Path, db: Path, config: str, until: str = END, hold=()) -> Ctx:
    ctx = Ctx(world / "public", db, Config.get(config), Model("off"), SEED)
    Run(ctx, human=Controller(world / "truth"), hold=hold, log=lambda *_: None).advance(until)
    ctx.store.set_note("run", "meta", {"seed": SEED, "config": config, "model": "off", "until": until})
    ctx.store.commit()
    return ctx


@pytest.fixture(scope="session")
def runs(world, tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("runs")
    ctxs = {c: finished(world, out / f"{c}.db", c) for c in "ABC"}
    return {c: {"ctx": ctx, "score": metrics.measure(out / f"{c}.db", world)} for c, ctx in ctxs.items()}
