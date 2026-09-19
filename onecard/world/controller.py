"""The scripted controller: the stand-in human for batch runs. It answers review questions the way Kestrel's controller
would, from the hidden policy and from what a person would find by picking up the phone (the answer key's facts about
who paid for what). It lives on the World side of the wall: the desks never see it, only its answers.
"""
import json
from pathlib import Path

APPROVER = "controller:mchen"


class Controller:
    def __init__(self, truth_dir: str | Path):
        d = Path(truth_dir)
        self.lines = json.loads((d / "truth.json").read_text())["lines"]
        self.policy = json.loads((d / "hidden_policy.json").read_text())
        self.answered = []

    def __call__(self, item: dict, card: dict):
        """(option_id, reason_text, approver) for one review item."""
        truth = self.lines[card["bank_line"]["line_id"]]
        pick = {"residual": self._residual, "ambiguous_allocation": self._allocation,
                "unknown_payer": self._payer}.get(item["type"], self._fallback)
        option, why = pick(item, card, truth)
        self.answered.append({"card_id": card["card_id"], "type": item["type"], "option": option["id"], "treatment": option["treatment"]})
        return option["id"], why, APPROVER

    def _by_treatment(self, item, treatment):
        return next((o for o in item["options"] if o["treatment"] == treatment), None)

    def _residual(self, item, card, truth):
        amount, reason = item["features"]["amount"], item["features"]["reason"]
        fact = next((r for r in truth.get("residuals", []) if r["amount"] == amount), None)
        if fact:  # the controller knows what this money is
            rule = self.policy.get(fact["reason"], {})
            say = {rule.get("treatment"): rule.get("say"), rule.get("else"): rule.get("say_else")}.get(fact["treatment"])
            option = self._by_treatment(item, fact["treatment"])
            if option:
                return option, say or "That is what the contract says."
        rule = self.policy.get(reason)
        if rule:  # the desk split the money differently from the facts: answer its question from policy alone
            within = amount <= rule.get("max_amount", amount)
            option = self._by_treatment(item, rule["treatment"] if within else rule["else"])
            if option:
                return option, rule["say"] if within else rule["say_else"]
        return self._fallback(item, card, truth)

    def _allocation(self, item, card, truth):
        want = sorted(truth.get("settles", {}))
        option = next((o for o in item["options"] if sorted(o.get("allocation", [])) == want), None)
        if option:
            say = self.policy["AMBIGUOUS_ALLOCATION"]["say"] if option["treatment"] == "oldest_first" \
                else "Called their AP team: this payment is for these invoices."
            return option, say
        return self._fallback(item, card, truth)

    def _payer(self, item, card, truth):
        option = next((o for o in item["options"] if o.get("payer") == truth.get("customer_id")), None)
        if option:
            return option, "Their parent company pays on their behalf. Apply it to the subsidiary."
        return self._fallback(item, card, truth)

    def _fallback(self, item, card, truth):
        """Nothing offered matches the facts: park the money rather than accept a wrong entry."""
        for treatment in ("leave_open_chase", "unapplied_cash"):
            option = self._by_treatment(item, treatment)
            if option:
                return option, "None of these is right. Park it until we have spoken to the customer."
        return item["options"][-1], "None of these is right."
