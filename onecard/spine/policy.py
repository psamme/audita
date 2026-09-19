"""The policy book: versioned deterministic rules learned from human decisions. Live rules run as plain code.

Guardrails, all enforced here whatever a model drafts:
  narrow start      same customer, same reason, amount no higher than what was seen
  widening          scope becomes "any" only after two consistent decisions on different customers
  backtest          a draft that argues with any past human decision is narrowed or dropped
  materiality cap   no rule fires above it
  repeat guardrail  a third occurrence for one customer in a quarter asks once for confirmation
  probation         a new or widened rule is on probation for its first five uses
"""
import json

from .contract import MATERIALITY, POLICY_CODES, PROBATION_USES, TREATMENTS
from .store import Store


def quarter(day: str) -> str:
    return f"{day[:4]}Q{(int(day[5:7]) - 1) // 3 + 1}"


def ref(p: dict) -> str:
    return f"{p['code']}@v{p['version']}"


class PolicyBook:
    def __init__(self, store: Store):
        self.s = store

    # ---- reading ----
    def live(self) -> list[dict]:
        rows = self.s.q("SELECT doc FROM policies WHERE status IN ('probation', 'active') ORDER BY code, version")
        return [json.loads(r[0]) for r in rows]

    def versions(self, code: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.s.q("SELECT doc FROM policies WHERE code=? ORDER BY version", code)]

    def all(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.s.q("SELECT doc FROM policies ORDER BY code, version")]

    def get(self, policy_ref: str) -> dict | None:
        code, v = policy_ref.split("@v")
        doc = self.s.one("SELECT doc FROM policies WHERE code=? AND version=?", code, int(v))
        return json.loads(doc) if doc else None

    def decisions(self, reason: str | None = None) -> list[dict]:
        """Decisions the book may learn from. With memory off, only those since the last wipe."""
        since = self.s.note("policy", "since", "0000")
        out = [json.loads(r[0]) for r in self.s.q("SELECT doc FROM decisions WHERE day>=? ORDER BY decision_id", since)]
        return [d for d in out if reason is None or d["features"].get("reason") == reason]

    def occurrences(self, customer_id: str, reason: str, day: str) -> int:
        """Times this customer has presented this situation this quarter: human decisions plus rule firings."""
        q = quarter(day)
        n = sum(1 for d in self.decisions(reason) if d["features"].get("customer_id") == customer_id and quarter(d["day"]) == q)
        uses = self.s.q("SELECT day FROM policy_uses WHERE customer_id=? AND reason=?", customer_id, reason)
        return n + sum(1 for u in uses if quarter(u["day"]) == q)

    def use_count(self, code: str, version: int | None = None) -> int:
        if version is None:
            return self.s.one("SELECT COUNT(*) FROM policy_uses WHERE code=?", code)
        return self.s.one("SELECT COUNT(*) FROM policy_uses WHERE code=? AND version=?", code, version)

    # ---- firing ----
    @staticmethod
    def in_scope(p: dict, customer_id: str, amount: int, uses_before: int) -> str | None:
        """None when the rule covers the case, else why it does not."""
        c = p["condition"]
        if p["scope"]["customers"] != "any" and customer_id not in p["scope"]["customers"]:
            return "outside_customer_scope"
        if customer_id in c.get("except_customers", []):
            return "outside_customer_scope"
        if c.get("max_amount") is not None and amount > c["max_amount"]:
            return "above_learned_amount"
        if c.get("min_amount") is not None and amount < c["min_amount"]:
            return "below_learned_amount"
        if c.get("max_uses_per_customer_per_quarter") is not None and uses_before >= c["max_uses_per_customer_per_quarter"]:
            return "over_use_limit"
        return None

    def match(self, customer_id: str, reason: str, amount: int, day: str) -> dict | None:
        """{'verdict': 'fire', 'policy': p} or {'verdict': 'escalate', 'why': ..., 'policy': p} or None."""
        uses = self.occurrences(customer_id, reason, day)
        fires, near = [], []
        for p in self.live():
            if p["scope"]["reason"] != reason:
                continue
            why = self.in_scope(p, customer_id, amount, uses)
            (near if why else fires).append((p, why))
        if len({p["action"]["treatment"] for p, _ in fires}) > 1:
            return {"verdict": "escalate", "why": "conflicting_policies", "policy": fires[0][0]}
        if fires:
            p = fires[0][0]
            c, kind = p["condition"], TREATMENTS[p["action"]["treatment"]]["kind"]
            if amount > MATERIALITY[kind]:
                return {"verdict": "escalate", "why": "above_materiality_cap", "policy": p}
            if uses >= 2 and not c.get("repeat_confirmed") and kind == "pnl":
                return {"verdict": "escalate", "why": "repeat_guardrail", "policy": p, "uses_before": uses}
            seen = {d["features"]["amount"] for d in self.decisions(reason) if d["decision_id"] in p["source_decisions"]}
            if p["status"] == "probation" and c.get("max_amount") and amount > 0.9 * c["max_amount"] and amount not in seen:
                return {"verdict": "escalate", "why": "probation_edge", "policy": p}
            return {"verdict": "fire", "policy": p}
        if near:
            p, why = near[0]
            return {"verdict": "escalate", "why": why, "policy": p, "uses_before": uses}
        return None

    def record_use(self, p: dict, card_id: str, customer_id: str, day: str, amount: int):
        if self.s.one("SELECT COUNT(*) FROM policy_uses WHERE code=? AND card_id=? AND amount=?", p["code"], card_id, amount):
            return
        self.s.x("INSERT INTO policy_uses VALUES (?,?,?,?,?,?,?)", p["code"], p["version"], card_id, customer_id, day,
                 amount, p["scope"]["reason"])
        if p["status"] == "probation" and self.use_count(p["code"], p["version"]) >= PROBATION_USES:
            p["status"] = "active"
            self.s.x("UPDATE policies SET status='active', doc=? WHERE code=? AND version=?", json.dumps(p), p["code"], p["version"])

    # ---- learning ----
    def backtest(self, draft: dict) -> dict:
        """Replay the draft over every past human decision with the same reason."""
        fired = agreed = 0
        conflicts = []
        for d in self.decisions(draft["scope"]["reason"]):
            f = d["features"]
            if self.in_scope(draft, f["customer_id"], f["amount"], f.get("uses_before", 0)) is None:
                fired += 1
                if d["treatment"] == draft["action"]["treatment"]:
                    agreed += 1
                else:
                    conflicts.append(d["decision_id"])
        return {"fired": fired, "agreed": agreed, "conflicts": len(conflicts), "conflict_decisions": conflicts}

    def draft(self, decision: dict) -> list[dict]:
        """Deterministic drafts from one new decision: the rule for its own treatment, plus a narrower version of any
        rule the decision argued with. These are the widest drafts the guardrails allow; a model may only tighten."""
        f, treatment = decision["features"], decision["treatment"]
        reason, drafts = f["reason"], []
        if treatment not in TREATMENTS or reason in (None, "UNKNOWN") or treatment == "unapplied_cash":
            return drafts
        code = POLICY_CODES.get((reason, treatment), f"{reason}-{treatment}".upper())
        prev = (self.versions(code) or [None])[-1]
        same = [d for d in self.decisions(reason) if d["treatment"] == treatment]
        bound = TREATMENTS[treatment]["bound"]
        if f.get("trigger") == "repeat_guardrail" and bound == "min":
            same = []  # a repeat-driven chase says nothing about amounts; it narrows the lenient rule below instead
        if same:
            customers = sorted({d["features"]["customer_id"] for d in same})
            amounts = [d["features"]["amount"] for d in same]
            cond = dict(prev["condition"]) if prev else {}
            cond.update({"max_amount": max(amounts) if bound == "max" else None,
                         "min_amount": min(amounts) if bound == "min" else None})
            if f.get("trigger") == "repeat_guardrail":
                cond["repeat_confirmed"] = True
            wide = len(customers) >= 2 and treatment not in ("oldest_first", "contract_discount")
            for scope_customers in (["any"] if wide else []) + [customers]:
                d = self._doc(code, prev, scope_customers, reason, cond, treatment, decision,
                              [x["decision_id"] for x in same])
                d["backtest"] = self.backtest(d)
                if not d["backtest"]["conflicts"]:
                    drafts.append(d)
                    break
        # the decision argued with a live rule for the same reason
        for p in self.live():
            if p["scope"]["reason"] != reason or p["action"]["treatment"] == treatment or p["code"] == code:
                continue
            cond = dict(p["condition"])
            why = self.in_scope(p, f["customer_id"], f["amount"], 0)
            if why == "outside_customer_scope":
                continue  # a different customer and a different answer say nothing about this rule
            if f.get("trigger") == "repeat_guardrail":
                cond["max_uses_per_customer_per_quarter"] = f.get("uses_before", 2)
            elif why is None:
                cond["except_customers"] = sorted(set(cond.get("except_customers", [])) | {f["customer_id"]})
            else:
                floor = p.get("bounds", {}).get("conflict_at")
                new_floor = f["amount"] if floor is None else (min(floor, f["amount"]) if TREATMENTS[p["action"]["treatment"]]["bound"] == "max" else max(floor, f["amount"]))
                if new_floor == floor:
                    continue
                d = self._doc(p["code"], p, p["scope"]["customers"], reason, cond, p["action"]["treatment"], decision,
                              p["source_decisions"], conflict=True)
                d["bounds"] = {"conflict_at": new_floor}
                d["status"] = p["status"]
                d["backtest"] = self.backtest(d)
                drafts.append(d)
                continue
            d = self._doc(p["code"], p, p["scope"]["customers"], reason, cond, p["action"]["treatment"], decision,
                          p["source_decisions"], conflict=True)
            d["status"] = p["status"]
            d["backtest"] = self.backtest(d)
            if not d["backtest"]["conflicts"]:
                drafts.append(d)
        return drafts

    def _doc(self, code, prev, customers, reason, cond, treatment, decision, sources, conflict=False) -> dict:
        widened = prev is not None and prev["scope"]["customers"] != "any" and customers == "any"
        return {"code": code, "version": (prev["version"] + 1) if prev else 1,
                "status": "probation" if (prev is None or widened) else prev["status"],
                "scope": {"customers": customers, "reason": reason},
                "condition": {k: v for k, v in cond.items() if v is not None},
                "action": {"treatment": treatment, "entry": TREATMENTS[treatment]["label"]},
                "reason": decision["reason_text"] if not conflict or not prev else f"{prev['reason']} Then: {decision['reason_text']}",
                "source_decisions": sorted(set(sources)),
                "conflict_decisions": sorted(set((prev or {}).get("conflict_decisions", []) + ([decision["decision_id"]] if conflict else []))),
                "bounds": (prev or {}).get("bounds", {}), "created": decision["day"], "changed_by": decision["decision_id"]}

    @staticmethod
    def clamp(model_draft: dict | None, det: dict) -> dict:
        """Intersect a model's draft with the guardrail draft. The model may tighten and explain, never widen."""
        if not model_draft:
            return det
        out = json.loads(json.dumps(det))
        if isinstance(model_draft.get("reason"), str) and model_draft["reason"].strip():
            out["reason"] = model_draft["reason"].strip()[:300]
        mc, dc = model_draft.get("condition") or {}, out["condition"]
        for k, pick in (("max_amount", min), ("max_uses_per_customer_per_quarter", min), ("min_amount", max)):
            if isinstance(mc.get(k), int) and mc[k] > 0:
                dc[k] = pick(dc[k], mc[k]) if dc.get(k) is not None else (mc[k] if k != "max_amount" else dc.get(k))
        ms = (model_draft.get("scope") or {}).get("customers")
        if isinstance(ms, list) and ms and all(isinstance(x, str) for x in ms) and out["scope"]["customers"] != "any" \
                and set(ms) <= set(out["scope"]["customers"]):
            out["scope"]["customers"] = sorted(ms)
        out["model_draft"] = True
        return {k: v for k, v in out.items() if v is not None}

    def approve(self, draft: dict, approver: str):
        draft = {**draft, "approved_by": approver}
        if draft["backtest"]["conflicts"]:
            raise ValueError("a draft that argues with a past decision cannot go live")
        self.s.x("UPDATE policies SET status='superseded' WHERE code=? AND status IN ('probation', 'active')", draft["code"])
        for old in self.versions(draft["code"]):
            if old["status"] != "superseded":
                old["status"] = "superseded"
                self.s.x("UPDATE policies SET doc=? WHERE code=? AND version=?", json.dumps(old), old["code"], old["version"])
        self.s.x("INSERT OR REPLACE INTO policies VALUES (?,?,?,?)", draft["code"], draft["version"], draft["status"], json.dumps(draft))

    def wipe(self, day: str):
        """Configuration B: memory off. The book and the alias table start each month empty, and past decisions stop
        counting as precedent. Old versions are kept as 'wiped' so the run stays inspectable."""
        for p in self.all():
            if "~" in p["code"]:
                continue
            p["status"] = "wiped"
            self.s.x("UPDATE policies SET status='wiped', code=?, doc=? WHERE code=? AND version=?",
                     f"{p['code']}~{day[:7]}", json.dumps(p), p["code"], p["version"])
        self.s.x("UPDATE policy_uses SET reason = reason || '~wiped', code = code || '~' || ? WHERE code NOT LIKE '%~%'", day[:7])
        self.s.x("DELETE FROM aliases")
        self.s.set_note("policy", "since", day)
