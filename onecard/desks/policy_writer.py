"""Policy writer. A model turns one human decision into a structured rule. The engine then intersects that draft with
the widest rule the guardrails allow (PolicyBook.clamp) and backtests it, so the model can explain and tighten a rule
but never widen one. With the model off, the guardrail draft is used as it stands.
"""
import json

SYSTEM = """You write finance policy for Kestrel Inference from a controller's decision. You are given the decision (what
was seen, what was decided, the controller's typed reason) and DRAFT, the widest rule the guardrails allow.
Return the rule you would write. `reason` is one or two plain sentences a controller would recognise as their own policy,
citing thresholds only if the decision supports them. You may tighten the draft (lower max_amount, add
max_uses_per_customer_per_quarter, fewer customers) when the controller's reason implies it. You cannot widen it.
Amounts are integer cents. Use null for a condition you do not want to set."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reason", "condition"],
          "properties": {"reason": {"type": "string"},
                         "condition": {"type": "object", "additionalProperties": False,
                                       "required": ["max_amount", "max_uses_per_customer_per_quarter"],
                                       "properties": {"max_amount": {"type": ["integer", "null"]},
                                                      "max_uses_per_customer_per_quarter": {"type": ["integer", "null"]}}}}}


def tighten(ctx, decision: dict, det: dict) -> dict:
    if not ctx.model.on:
        return det
    prompt = json.dumps({"DECISION": {k: decision[k] for k in ("question", "treatment", "reason_text", "features")},
                         "DRAFT": {k: det[k] for k in ("code", "version", "scope", "condition", "action")},
                         "PAST_DECISIONS_SAME_REASON": [
                             {"treatment": d["treatment"], "amount": d["features"]["amount"], "customer": d["features"]["customer_id"],
                              "why": d["reason_text"]} for d in ctx.book.decisions(det["scope"]["reason"])]}, indent=1)
    out = ctx.book.clamp(ctx.model.ask("policy_writer", SYSTEM, prompt, SCHEMA, strong=True), det)
    out["backtest"] = ctx.book.backtest(out)
    return out if not out["backtest"]["conflicts"] else det
