"""Deterministic tier-0 matcher: exact amount -> reference link -> date, with abstention.

A bank line (B) is only ever compared with ledger records (A) of the same currency,
account and exact amount. Inside such an amount block we draw edges and look at
connected components, tier by tier:

  "ref"   edge = a ledger reference token appears inside the bank narrative
  "date"  edge = same value date (records no earlier tier used)
  "near"  edge = value dates within `near_window` days

A component is emitted only when it is balanced (k bank lines, k ledger records,
k <= the tier's max_k) and survives the abstain guards:

  rivals    other unexplained same-amount records close in date that could equally
            well take part in the match -> the winner is not clear, abstain
  siblings  another record on the same side with the identical reference string on
            the same day -> looks like one leg of a split / one-to-many, abstain

k == 1 is a unique mutual candidate. k > 1 is a set of interchangeable lines; they
are paired off in order and each reports the union of the allocations.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from .data import Record

MIN_TOKEN_FLOOR = 6


@dataclass(frozen=True)
class Thresholds:
    min_token_len: int = 8  # shortest ledger reference token trusted as a link
    ref_window: int = 10  # max value-date gap (days) for a reference link
    ref_max_k: int = 1
    ref_max_rivals: int = 0
    date_max_k: int = 1  # 0 disables the tier
    date_max_rivals: int = 0
    near_window: int = 0  # 0 disables the tier
    near_max_rivals: int = 0
    rival_window: int = 3  # days around the match in which a same-amount record counts as a rival
    sibling_guard: bool = True  # applies to the date and near tiers

    def as_dict(self):
        return asdict(self)


@dataclass(slots=True)
class Match:
    b_id: str
    a_id: str
    allocations: frozenset
    tier: str
    k: int
    reason: str


class Blocks:
    """Amount blocks with reference links and sibling flags precomputed once."""

    def __init__(self, ledger: list[Record], bank: list[Record]):
        ref_count = Counter((r.side, r.refs, r.day) for r in ledger + bank if r.refs.strip())
        self.has_sibling = {
            r.id for r in ledger + bank if ref_count[(r.side, r.refs, r.day)] > 1
        }
        by_key = defaultdict(lambda: ([], []))
        for a in ledger:
            by_key[(a.currency, a.account, a.cents)][0].append(a)
        for b in bank:
            by_key[(b.currency, b.account, b.cents)][1].append(b)
        self.blocks = []
        for As, Bs in by_key.values():
            if As and Bs:
                As.sort(key=lambda r: (r.day, r.id))
                Bs.sort(key=lambda r: (r.day, r.id))
                self.blocks.append((As, Bs, self._ref_links(As, Bs)))

    @staticmethod
    def _ref_links(As, Bs):
        """(i, j, token_len): longest reference token of As[i] found in Bs[j]'s text."""
        by_token = defaultdict(list)
        for i, a in enumerate(As):
            for tok in a.tokens:
                if len(tok) >= MIN_TOKEN_FLOOR and any(c.isdigit() for c in tok):
                    by_token[tok].append(i)
        best = {}
        for tok, idxs in by_token.items():
            for j, b in enumerate(Bs):
                if tok in b.squashed:
                    for i in idxs:
                        if len(tok) > best.get((i, j), 0):
                            best[(i, j)] = len(tok)
        return [(i, j, n) for (i, j), n in best.items()]


def _components(edges):
    """Connected components of a bipartite edge list, as (a_indexes, b_indexes)."""
    adj = defaultdict(list)
    for i, j in edges:
        adj[(0, i)].append((1, j))
        adj[(1, j)].append((0, i))
    seen = set()
    for start in adj:
        if start in seen:
            continue
        seen.add(start)
        stack, sides = [start], ([], [])
        while stack:
            node = stack.pop()
            sides[node[0]].append(node[1])
            for nxt in adj[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        yield sorted(sides[0]), sorted(sides[1])


def _match_block(As, Bs, links, has_sibling, th: Thresholds, out: list):
    free_a, free_b = set(range(len(As))), set(range(len(Bs)))

    def run_tier(tier, edges, max_k, max_rivals, why, guard_siblings):
        comps = [
            (ca, cb) for ca, cb in _components(edges) if len(ca) == len(cb) <= max_k
        ]
        # records inside a balanced component are explained; everything else still free is a rival
        explained_a = {i for ca, _ in comps for i in ca}
        explained_b = {j for _, cb in comps for j in cb}
        loose_a = [As[i].day for i in free_a - explained_a]
        loose_b = [Bs[j].day for j in free_b - explained_b]
        for ca, cb in comps:
            k = len(cb)
            if guard_siblings and (
                any(As[i].id in has_sibling for i in ca) or any(Bs[j].id in has_sibling for j in cb)
            ):
                continue
            lo = min(As[ca[0]].day, Bs[cb[0]].day) - th.rival_window
            hi = max(As[ca[-1]].day, Bs[cb[-1]].day) + th.rival_window
            rivals = sum(lo <= d <= hi for d in loose_a) + sum(lo <= d <= hi for d in loose_b)
            if rivals > max_rivals:
                continue
            allocs = frozenset(As[i].allocation for i in ca)
            for i, j in zip(ca, cb):
                uniq = "unique both ways" if k == 1 else f"{k} interchangeable lines"
                reason = f"amount exact; {why}; date gap {Bs[j].day - As[i].day:+d}d; {uniq}; {rivals} rivals"
                out.append(Match(Bs[j].id, As[i].id, allocs, tier, k, reason))
            free_a.difference_update(ca)
            free_b.difference_update(cb)

    ref_edges = [
        (i, j)
        for i, j, n in links
        if n >= th.min_token_len and abs(Bs[j].day - As[i].day) <= th.ref_window
    ]
    if ref_edges:
        run_tier("ref", ref_edges, th.ref_max_k, th.ref_max_rivals,
                 "ledger reference found in bank narrative", False)

    for tier, window, max_k, max_rivals in (
        ("date", 0, th.date_max_k, th.date_max_rivals),
        ("near", th.near_window, 1 if th.near_window else 0, th.near_max_rivals),
    ):
        if max_k == 0 or not free_a or not free_b:
            continue
        edges = [(i, j) for i in free_a for j in free_b if abs(Bs[j].day - As[i].day) <= window]
        if edges:
            why = "same value date" if window == 0 else f"value dates within {window}d"
            run_tier(tier, edges, max_k, max_rivals, why, th.sibling_guard)


def match(blocks: Blocks, th: Thresholds) -> list[Match]:
    out = []
    for As, Bs, links in blocks.blocks:
        _match_block(As, Bs, links, blocks.has_sibling, th, out)
    return out
