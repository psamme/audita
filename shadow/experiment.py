"""Same transaction, two clients. The only thing that differs is what each client's own history taught the playbook."""
import json
from concurrent.futures import ThreadPoolExecutor

from shadow import db, pipeline, playbook as pbmod

CACHE = db.RUNS / "experiment.json"


def _one(client: str, track: str, version: int | None) -> dict:
    from sim.experiment_data import inject   # demo fixture, identical for both clients
    ids = inject(client)
    rid = f"experiment_{client}" + (f"_v{version}" if version else "")
    pipeline.run(client, "2026-05", "playbook", track, version=version, only={ids["bank_line"]}, run_id=rid, label="same-transaction experiment")
    item = json.loads((db.RUNS / rid / "resolutions.jsonl").read_text().splitlines()[0])
    pb = pbmod.load(client, track, version)
    item["rule"] = next((r for r in pb["rules"] if r["id"] == item["resolution"].get("rule_id")), None)
    con = db.connect(client, readonly=True)
    wanted = set(item["resolution"].get("precedent_ids") or []) | set((item["rule"] or {}).get("precedent_ids", [])[:6])
    item["precedents"] = [c for c in pbmod.cases(con, "2026-04") if c["id"] in wanted][:8]
    item["playbook_version"] = pb["version"]
    return item


def run(track: str = "main", fresh: bool = False, version: int | None = None) -> dict:
    """version pins both clients to that playbook version (1 = as induced, before any human input); default latest."""
    cache = CACHE.with_name(f"experiment_v{version}.json") if version else CACHE
    if cache.exists() and not fresh:
        return json.loads(cache.read_text())
    from sim.experiment_data import TXN
    with ThreadPoolExecutor(2) as ex:
        a, b = ex.map(lambda c: _one(c, track, version), ("A", "B"))
    out = {"transaction": TXN, "results": {"A": a, "B": b}}
    cache.write_text(json.dumps(out, indent=1, default=str))
    return out


def bank_change(track: str = "main", fresh: bool = False) -> dict:
    """The payment that ties exactly and still must not clear: changed payee bank details (client B)."""
    cache = CACHE.with_name("experiment_bank_change.json")
    if cache.exists() and not fresh:
        return json.loads(cache.read_text())
    from sim.experiment_data import inject_bank_change
    ids = inject_bank_change()
    pipeline.run("B", "2026-05", "playbook", track, only={ids["bank_line"]}, run_id="experiment_bank_change", label="control demo")
    item = json.loads((db.RUNS / "experiment_bank_change" / "resolutions.jsonl").read_text().splitlines()[0])
    cache.write_text(json.dumps(item, indent=1, default=str))
    return item


if __name__ == "__main__":
    import sys
    r = run(track=sys.argv[1] if len(sys.argv) > 1 else "main", fresh=True)
    for c, it in r["results"].items():
        res = it["resolution"]
        print(c, it["tier"], res["action"], res["adjustments"], res["escalate_to"], res["rule_id"], "|", res["rationale"][:300])
