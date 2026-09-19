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


BAND_ANSWER = {"type": "object", "additionalProperties": False, "required": ["kind", "limit"], "properties": {
    "kind": {"type": "string", "enum": ["limit", "review", "usual", "not_amount"]}, "limit": {"type": ["number", "null"]}}}


def band_answer(client: str, question: dict, usage: llm.Usage | None = None) -> dict:
    """The controller answers a band question the way the UI offers it: state the limit, yes review, no usual, or not about the amount."""
    a = llm.call(CONTROLLER.format(policy=policy_for(client)) + "\nYou are answering a multiple-choice card. kind=limit with the number if "
                 "your policy has a limit for this (a real controller knows their limit); kind=review if an item at the asked value "
                 "should go to someone; kind=usual if your team handles it without review and you have no number to give; "
                 "kind=not_amount if how these are handled does not depend on the amount at all.",
                 [{"role": "user", "content": question["text"]}], schema=BAND_ANSWER, max_tokens=1000, usage=usage)
    return json.loads(a.text)


def band_interview(client: str, track: str, limit: int = 8, usage: llm.Usage | None = None, on_answer=None) -> list[dict]:
    out, asked = [], set()
    for _ in range(limit):
        qs = [q for q in playbook.band_questions(playbook.load(client, track)) if q["rule_id"] + q["condition"] not in asked]
        if not qs:
            break
        q = qs[0]
        asked.add(q["rule_id"] + q["condition"])       # one question per band is enough to show the mechanism
        ans = band_answer(client, q, usage)
        kind = ans["kind"] if not (ans["kind"] == "limit" and ans["limit"] is None) else "usual"
        res = correct.answer_band(client, track, q["rule_id"], q["condition"], q["value"], review={"review": True, "usual": False}.get(kind),
                                  limit=ans["limit"] if kind == "limit" else None, not_amount=kind == "not_amount")
        out.append({"question": q["text"], "answer": ans, "band": res.get("band"), "held": res.get("held"), "new_version": res.get("new_version")})
        if on_answer:
            on_answer("band", q["text"], f"the limit is {ans['limit']}" if kind == "limit" else kind)
    return out


def interview(client: str, track: str, limit: int = 12, usage: llm.Usage | None = None, on_answer=None) -> list[dict]:
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
        if on_answer:
            on_answer("open_question", rule["open_question"], text)
    return out


def review_queue(client: str, track: str, run_id: str, key_path, date_to: str, limit: int = 15,
                 usage: llm.Usage | None = None, on_answer=None) -> list[dict]:
    key = json.loads(key_path.read_text())
    items = [json.loads(l) for l in (db.RUNS / run_id / "resolutions.jsonl").read_text().splitlines()]
    queue = [it for it in items if it["resolution"]["action"] == "escalate" and it["record"]["date"] <= date_to
             and it["item_id"] in key and key[it["item_id"]]["action"] != "escalate"]
    out = []
    for it in queue[:limit]:
        k = key[it["item_id"]]
        human = {f: k[f] for f in ("action", "ledger_ids", "adjustments", "escalate_to")}
        out.append(correct.correct(client, track, it, human, k["note"] or "handled the usual way", run_id=run_id, usage=usage))
        if on_answer:
            on_answer("correction", it["item_id"], k["note"])
    return out
