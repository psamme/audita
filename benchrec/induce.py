"""Induce a playbook of conventions from how the bank's own analysts resolved what the engine left.

Reads the TRAIN split only. Nothing in this module opens solution.csv.

The train file records who closed every match group (`matchedBy`) and under which rule. One
operator only ever applies numbered rules the day after value date: that is the bank's matching
engine. The other two are people. Their groups are the precedents. From them we measure a small
set of conventions, each with a band in the product's vocabulary:

  applies_to   the convention executes on its own up to here. Chosen by back-test: the widest
               setting whose agreement with the analysts on train stays at or above FLOOR.
  asks_to      the widest value any analyst precedent shows. Between applies_to and asks_to the
               item goes to a person, with the convention attached as the suggestion.
  beyond       does not apply.

Each end of a band cites a real precedent matchId. A convention whose back-test never reaches
FLOOR does not execute at all (the execution floor); everything it would touch is escalated.

Conventions run on the residue of the deterministic matcher, in a fixed order, and consume the
records they use.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from .data import Record

FLOOR = 0.98  # agreement with analyst precedents needed before a convention may execute
MIN_LINK = 8
DAY_WINDOW = 3  # candidate search window in days, same as the matcher's rival window
MAX_SUBSET = 10
ORDER = ("penny_difference", "split_settlement", "combined_deposit", "same_amount_batch",
         "late_value_date", "bank_side_offset")

TEXT = {
    "penny_difference": (
        "Small amount differences are accepted when the reference ties the two lines",
        "When a ledger reference appears in the bank narrative and the value dates are close, the analysts "
        "close the pair even though the amounts differ by a few cents.",
        "cents"),
    "split_settlement": (
        "One bank line settles several ledger entries",
        "A single bank movement is matched to several ledger entries whose amounts add up to it exactly, "
        "when they carry a reference found in the bank narrative.",
        "ledger entries"),
    "combined_deposit": (
        "Several bank lines settle one ledger entry",
        "Several bank movements carrying the same ledger reference are matched to the one ledger entry "
        "they add up to.",
        "bank lines"),
    "same_amount_batch": (
        "Batches of identical amounts on one day are closed together",
        "When the same amount appears the same number of times on both sides on one value date, with no "
        "stray line of that amount nearby, the analysts close the whole batch under one match.",
        "lines per side"),
    "late_value_date": (
        "An exact amount with no reference is accepted within a few days",
        "With no reference to go on, an exact amount that has exactly one unexplained counterpart nearby "
        "in time is matched across a value-date gap.",
        "days"),
    "bank_side_offset": (
        "A bank debit and credit that cancel need no ledger entry",
        "Two bank lines of equal and opposite amount are closed against each other with no ledger side.",
        "days apart"),
}


@dataclass(slots=True)
class Resolution:
    b_ids: tuple
    a_ids: tuple
    allocations: frozenset  # empty for a bank-side offset
    convention: str
    param: int
    verdict: str = "cleared"  # cleared | escalated | no_ledger
    reason: str = ""


@dataclass
class Convention:
    id: str
    title: str
    statement: str
    unit: str
    support: int = 0  # analyst precedent groups that show it
    applies_to: int | None = None
    asks_to: int | None = None
    applies_precedent: str = ""
    asks_precedent: str = ""
    precedents: list = field(default_factory=list)
    backtest: dict = field(default_factory=dict)
    executes: bool = False
    note: str = ""

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def link_tokens(a: Record):
    return [t for t in a.tokens if len(t) >= MIN_LINK and any(c.isdigit() for c in t)]


def linked(a: Record, b: Record) -> bool:
    return any(t in b.squashed for t in link_tokens(a))


class Residue:
    """What the matcher left: free ledger records and unresolved bank lines, indexed by day."""

    def __init__(self, ledger, bank, matches):
        used_a = {m.a_id for m in matches}
        done_b = {m.b_id for m in matches}
        self.free_a = {a.id: a for a in ledger if a.id not in used_a}
        self.open_b = {b.id: b for b in bank if b.id not in done_b}
        self.n_open_start = len(self.open_b)
        self._index()

    def _index(self):
        self.a_by_day = defaultdict(list)
        self.b_by_day = defaultdict(list)
        for a in self.free_a.values():
            self.a_by_day[a.day].append(a)
        for b in self.open_b.values():
            self.b_by_day[b.day].append(b)

    def near_a(self, day, window):
        return [a for d in range(day - window, day + window + 1) for a in self.a_by_day.get(d, ())
                if a.id in self.free_a]

    def near_b(self, day, window):
        return [b for d in range(day - window, day + window + 1) for b in self.b_by_day.get(d, ())
                if b.id in self.open_b]

    def consume(self, res: Resolution):
        for i in res.a_ids:
            self.free_a.pop(i, None)
        for i in res.b_ids:
            self.open_b.pop(i, None)


def _same_book(a, b):
    return a.currency == b.currency and a.account == b.account


def _unique_subset(items, target):
    """The one subset of >= 2 items summing to target, or None when there is none or several."""
    total = sum(x.cents for x in items)
    if total == target and len(items) >= 2:
        return tuple(items)
    if len(items) > MAX_SUBSET:
        return None
    found = None
    for k in range(2, len(items)):
        for combo in combinations(items, k):
            if sum(x.cents for x in combo) == target:
                if found is not None:
                    return None
                found = combo
    return found


# ---- candidate generators: each yields Resolutions at the widest setting, with the measured parameter ----

def cand_penny(rz: Residue, widest: int):
    pairs = defaultdict(list)
    back = defaultdict(list)
    for b in rz.open_b.values():
        for a in rz.near_a(b.day, DAY_WINDOW):
            if _same_book(a, b) and a.cents != b.cents and abs(a.cents - b.cents) <= widest and linked(a, b):
                pairs[b.id].append(a)
                back[a.id].append(b)
    for b_id, As in pairs.items():
        if len(As) == 1 and len(back[As[0].id]) == 1:
            a, b = As[0], rz.open_b[b_id]
            yield Resolution((b.id,), (a.id,), frozenset([a.allocation]), "penny_difference",
                             abs(a.cents - b.cents))


def cand_split(rz: Residue, widest: int):
    for b in list(rz.open_b.values()):
        # analysts' splits usually net a negative line against a gross one, so signs are free
        As = [a for a in rz.a_by_day.get(b.day, ()) if a.id in rz.free_a and _same_book(a, b) and linked(a, b)]
        if 2 <= len(As) <= max(widest, MAX_SUBSET):
            combo = _unique_subset(As, b.cents)
            if combo and len(combo) <= widest:
                yield Resolution((b.id,), tuple(a.id for a in combo),
                                 frozenset(a.allocation for a in combo), "split_settlement", len(combo))


def cand_combined(rz: Residue, widest: int):
    for a in list(rz.free_a.values()):
        if not link_tokens(a):
            continue
        Bs = [b for b in rz.b_by_day.get(a.day, ()) if b.id in rz.open_b and _same_book(a, b) and linked(a, b)]
        if 2 <= len(Bs) <= max(widest, MAX_SUBSET):
            combo = _unique_subset(Bs, a.cents)
            if combo and len(combo) <= widest:
                yield Resolution(tuple(b.id for b in combo), (a.id,), frozenset([a.allocation]),
                                 "combined_deposit", len(combo))


def cand_batch(rz: Residue, widest: int):
    blocks = defaultdict(lambda: ([], []))
    for a in rz.free_a.values():
        blocks[(a.currency, a.account, a.cents)][0].append(a)
    for b in rz.open_b.values():
        blocks[(b.currency, b.account, b.cents)][1].append(b)
    for As, Bs in blocks.values():
        if not As or not Bs:
            continue
        days = Counter(b.day for b in Bs)
        for day, k in days.items():
            if not 2 <= k <= widest:
                continue
            a_here = [a for a in As if a.day == day]
            if len(a_here) != k:
                continue
            stray = sum(1 for a in As if a.day != day and abs(a.day - day) <= DAY_WINDOW) + \
                sum(1 for b in Bs if b.day != day and abs(b.day - day) <= DAY_WINDOW)
            if stray:
                continue
            b_here = [b for b in Bs if b.day == day]
            yield Resolution(tuple(b.id for b in b_here), tuple(a.id for a in a_here),
                             frozenset(a.allocation for a in a_here), "same_amount_batch", k)


def cand_late(rz: Residue, widest: int):
    blocks = defaultdict(lambda: ([], []))
    for a in rz.free_a.values():
        blocks[(a.currency, a.account, a.cents)][0].append(a)
    for b in rz.open_b.values():
        blocks[(b.currency, b.account, b.cents)][1].append(b)
    for As, Bs in blocks.values():
        if len(As) == 1 and len(Bs) == 1 and abs(As[0].cents) >= 100:
            a, b = As[0], Bs[0]
            gap = abs(b.day - a.day)
            if gap <= widest:
                yield Resolution((b.id,), (a.id,), frozenset([a.allocation]), "late_value_date", gap)


def cand_offset(rz: Residue, widest: int):
    by_amt = defaultdict(list)
    for b in rz.open_b.values():
        by_amt[(b.currency, b.account, abs(b.cents))].append(b)
    ledger_amts = {(a.currency, a.account, abs(a.cents)) for a in rz.free_a.values()}
    for key, Bs in by_amt.items():
        if len(Bs) == 2 and Bs[0].cents == -Bs[1].cents and Bs[0].cents != 0 and key not in ledger_amts:
            gap = abs(Bs[0].day - Bs[1].day)
            if gap <= widest:
                yield Resolution((Bs[0].id, Bs[1].id), (), frozenset(), "bank_side_offset", gap, verdict="no_ledger")


GENERATORS = {
    "penny_difference": cand_penny, "split_settlement": cand_split, "combined_deposit": cand_combined,
    "same_amount_batch": cand_batch, "late_value_date": cand_late, "bank_side_offset": cand_offset,
}


# ---- what the analyst precedents show (support, widest value, citations) ----

def analyst_groups(ledger, bank, analysts):
    groups = defaultdict(lambda: ([], []))
    for a in ledger:
        groups[a.match_id][0].append(a)
    for b in bank:
        groups[b.match_id][1].append(b)
    return {g: v for g, v in groups.items() if g in analysts}


def precedent_params(groups):
    """convention id -> list of (param, matchId) measured on analyst-closed groups."""
    out = defaultdict(list)
    for g, (As, Bs) in groups.items():
        sa, sb = sum(a.cents for a in As), sum(b.cents for b in Bs)
        if len(As) == 1 and len(Bs) == 1:
            a, b = As[0], Bs[0]
            if a.cents != b.cents and linked(a, b) and abs(b.day - a.day) <= DAY_WINDOW:
                out["penny_difference"].append((abs(a.cents - b.cents), g))
            if a.cents == b.cents and not linked(a, b) and a.day != b.day:
                out["late_value_date"].append((abs(b.day - a.day), g))
        elif len(Bs) == 1 and len(As) >= 2 and sa == sb:
            out["split_settlement"].append((len(As), g))
        elif len(As) == 1 and len(Bs) >= 2 and sa == sb:
            out["combined_deposit"].append((len(Bs), g))
        elif len(As) == len(Bs) >= 2 and len({x.cents for x in As + Bs}) == 1:
            out["same_amount_batch"].append((len(Bs), g))
        elif not As and len(Bs) == 2 and sb == 0:
            out["bank_side_offset"].append((abs(Bs[0].day - Bs[1].day), g))
    return out


def is_right(res: Resolution, truth: dict) -> bool:
    """Pair level, the repo's headline definition: what we attached is a subset of the labelled allocation."""
    if res.verdict == "no_ledger":
        return all(not truth.get(b) for b in res.b_ids)
    return all(truth.get(b) and res.allocations <= truth[b] for b in res.b_ids)


TAIL = 50  # a band stops widening as soon as the last TAIL lines it added agree less than the floor


def _pick_band(scored, floor):
    """Widest parameter reached before agreement breaks. scored: [(param, right)] per bank line.

    Walks the parameter upwards. At each value both the cumulative agreement and the agreement of the
    trailing TAIL lines must hold the floor; the first value that fails ends the band, so a precise
    narrow setting cannot carry a sloppy wide one.
    """
    by = defaultdict(list)
    for p, ok in scored:
        by[p].append(ok)
    best, seen = None, []
    for p in sorted(by):
        seen += by[p]
        tail = seen[-max(TAIL, len(by[p])):]
        if sum(seen) / len(seen) < floor or sum(tail) / len(tail) < floor:
            break
        best = (p, len(seen), sum(seen))
    return best


def _percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def induce(ledger, bank, matches, analysts, floor=FLOOR):
    """Measure every convention on the train residue. Returns [Convention] in execution order."""
    truth = {b.id: b.target for b in bank}
    groups = analyst_groups(ledger, bank, analysts)
    seen = precedent_params(groups)
    rz = Residue(ledger, bank, matches)
    playbook = []
    for cid in ORDER:
        title, statement, unit = TEXT[cid]
        conv = Convention(cid, title, statement, unit)
        params = seen.get(cid, [])
        conv.support = len(params)
        if not params:
            conv.note = "No analyst precedent shows this; it does not execute."
            playbook.append(conv)
            continue
        # asks_to is what the analysts themselves did at the 99th percentile: beyond that a precedent is an outlier
        conv.asks_to = _percentile([p for p, _ in params], 0.99)
        conv.asks_precedent = min((x for x in params if x[0] >= conv.asks_to), key=lambda x: x[0])[1]
        cands = list(GENERATORS[cid](rz, conv.asks_to))
        scored = [(r.param, is_right(r, truth)) for r in cands for _ in r.b_ids]
        band = _pick_band(scored, floor)
        conv.backtest = {
            "at_widest": {"bank_lines": len(scored), "agree": sum(ok for _, ok in scored),
                          "agreement": (sum(ok for _, ok in scored) / len(scored)) if scored else None},
        }
        if band:
            conv.applies_to, n, c = band
            conv.executes = True
            conv.backtest["at_applies_to"] = {"bank_lines": n, "agree": c, "agreement": c / n}
            conv.applies_precedent = max((x for x in params if x[0] <= conv.applies_to),
                                         key=lambda x: x[0], default=("", ""))[1]
        else:
            conv.note = (f"Back-tested agreement with the analysts never reaches {floor:.0%}. It does not execute; "
                         "everything it would touch is sent to a person with this convention as the suggestion.")
        conv.precedents = [g for _, g in sorted(params)[:: max(1, len(params) // 6)]][:6]
        playbook.append(conv)
        # consume what this convention will actually clear, so later conventions see the same residue as at run time
        for r in cands:
            if conv.executes and r.param <= conv.applies_to:
                rz.consume(r)
    return playbook


def apply(ledger, bank, matches, playbook):
    """Run the playbook on a residue. Returns (resolutions, escalations)."""
    rz = Residue(ledger, bank, matches)
    cleared, asked = [], []
    for conv in playbook:
        if conv.asks_to is None:
            continue
        for r in list(GENERATORS[conv.id](rz, conv.asks_to)):
            if any(b not in rz.open_b for b in r.b_ids) or any(a not in rz.free_a for a in r.a_ids):
                continue
            if conv.executes and r.param <= conv.applies_to:
                r.reason = f"{conv.title}: {r.param} {conv.unit}, inside the band the analysts' history supports"
                cleared.append(r)
                rz.consume(r)
            else:
                r.verdict = "escalated"
                why = ("held back by its own history" if not conv.executes else
                       f"{r.param} {conv.unit} is past {conv.applies_to}, where agreement with the analysts holds")
                r.reason = f"{conv.title}: {why}"
                asked.append(r)
                for b in r.b_ids:  # a person will look at it; later conventions leave it alone
                    rz.open_b.pop(b, None)
    rest = []
    a_amounts = Counter((a.currency, a.account, a.cents) for a in rz.free_a.values())
    for b in rz.open_b.values():
        n = a_amounts.get((b.currency, b.account, b.cents), 0)
        reason = ("no_ledger_candidate" if n == 0 else "one_same_amount_candidate_outside_every_band" if n == 1
                  else "several_same_amount_candidates")
        rest.append(Resolution((b.id,), (), frozenset(), "", 0, verdict="escalated", reason=reason))
    return cleared, asked, rest
