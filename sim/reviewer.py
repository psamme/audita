"""Simulated humans for the experiments. Sim-side: reads the ground-truth policy and the answer key.

- review_queue: the client's reviewer works the escalation queue for part of the month. Where the agent escalated
  something the team would simply have handled, the reviewer resolves it and says why in a few words (the key's note).
  Silent errors are NOT corrected: a human only sees what lands in the queue.
- interview: the controller answers the open questions on proposed rules, from the written policy.
"""
import json
import re

from shadow import correct, db, llm, playbook

POLICIES = (db.ROOT / "sim" / "POLICIES.md").read_text()

CONTROLLER = """You are the person who signs off the books at this client. A new reconciliation system has read your \
history and is asking you to confirm how your team handles something. Answer the way a busy controller would: one to \
three plain sentences, concrete amounts, account numbers and who it goes to. Answer only from your team's policy below. \
If the policy does not cover it, say those should come to you (or the role your policy implies). Do not explain the \
system to itself and do not add anything the question did not ask.

Your team's policy:
{policy}"""


def policy_for(client: str) -> str:
    parts = re.split(r"\n## ", POLICIES)
    own = next(p for p in parts if p.startswith(f"Client {client}"))
    return own + "\n\n" + next(p for p in parts if p.startswith("Universal"))


def interview(client: str, track: str, limit: int = 12, usage: llm.Usage | None = None) -> list[dict]:
    out = []
    pb = playbook.load(client, track)
    asked = [r for r in pb["rules"] if r["status"] == "proposed" and r.get("open_question")]
    asked.sort(key=lambda r: -(r.get("backtest", {}).get("support", 0)))
    for rule in asked[:limit]:
        q = f"Rule as I understood it: {rule['text']}\nMy question: {rule['open_question']}"
        a = llm.call(CONTROLLER.format(policy=policy_for(client)), [{"role": "user", "content": q}],
                     schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False},
                     max_tokens=2000, usage=usage)
        text = json.loads(a.text)["answer"]
        out.append(correct.answer(client, track, rule["id"], text, usage=usage) | {"rule_id": rule["id"]})
    return out


def review_queue(client: str, track: str, run_id: str, key_path, date_to: str, limit: int = 15,
                 usage: llm.Usage | None = None) -> list[dict]:
    key = json.loads(key_path.read_text())
    items = [json.loads(l) for l in (db.RUNS / run_id / "resolutions.jsonl").read_text().splitlines()]
    queue = [it for it in items if it["resolution"]["action"] == "escalate" and it["record"]["date"] <= date_to
             and it["item_id"] in key and key[it["item_id"]]["action"] != "escalate"]
    out = []
    for it in queue[:limit]:
        k = key[it["item_id"]]
        human = {f: k[f] for f in ("action", "ledger_ids", "adjustments", "escalate_to")}
        out.append(correct.correct(client, track, it, human, k["note"] or "handled the usual way", run_id=run_id, usage=usage))
    return out
