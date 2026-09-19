"""Loading and normalising the BenchRec cash reconciliation files."""

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "benchrec"
TRAIN = DATA_DIR / "BenchRec_cash_v1.0_train.csv"
EVAL = DATA_DIR / "BenchRec_cash_v1.0_eval.csv"
SOLUTION = DATA_DIR / "BenchRec_cash_v1.0_solution.csv"
BASELINE = DATA_DIR / "MatcherByChatGPT_submission.csv"

# "[USD_2023-03-05_ACC#1_..., USD_2023-03-05_ACC#1_...]" -- unquoted, so split on the allocation prefix
_ALLOC_SPLIT = re.compile(r",(?=[A-Z]{3}_\d{4}-\d{2}-\d{2}_)")
_TOKEN = re.compile(r"[A-Z0-9]+")


@dataclass(slots=True)
class Record:
    side: str  # "A" ledger, "B" bank
    id: str
    cents: int
    day: int  # value date as ordinal
    currency: str
    account: str
    dc: str
    refs: str
    attrs: str
    allocation: str = ""  # A only
    match_id: str = ""  # train only
    rule: str = ""  # train only
    target: frozenset = frozenset()  # B only, train only
    tokens: tuple = field(default=())
    squashed: str = ""


def parse_targets(raw: str) -> frozenset:
    raw = raw.strip()
    if not raw:
        return frozenset()
    if raw.startswith("[") and raw.endswith("]"):
        return frozenset(p for p in _ALLOC_SPLIT.split(raw[1:-1]) if p)
    return frozenset([raw])


def _record(row: dict, side: str) -> Record:
    p = side + "_"
    refs = row[p + "transactionReferences"].upper()
    attrs = row[p + "transactionAttributes"].upper()
    rec = Record(
        side=side,
        id=row[p + "id"],
        cents=round(float(row[p + "amount"]) * 100),
        day=date.fromisoformat(row[p + "valueDate"]).toordinal(),
        currency=row[p + "currencyCode"],
        account=row[p + "account"],
        dc=row[p + "debitOrCredit"],
        refs=refs,
        attrs=attrs,
        match_id=row["matchId"],
        rule=row["matchRule"],
    )
    if side == "A":
        rec.allocation = row["A_allocation"]
        # ledger reference tokens; the bank narrative often embeds one of them
        rec.tokens = tuple(dict.fromkeys(_TOKEN.findall(refs)))
    else:
        rec.target = parse_targets(row["targetAllocation"])
        rec.squashed = " ".join(_TOKEN.findall(refs + " " + attrs))
    return rec


def load_records(path: Path) -> tuple[list[Record], list[Record]]:
    ledger, bank = [], []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row["A_id"]:
                ledger.append(_record(row, "A"))
            elif row["B_id"]:
                bank.append(_record(row, "B"))
    return ledger, bank


def load_solution(path: Path = SOLUTION) -> dict[str, frozenset]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {r["B_id"]: parse_targets(r["targetAllocation"]) for r in csv.DictReader(fh)}


def load_baseline(path: Path = BASELINE) -> dict[str, frozenset]:
    """The shipped reference submission: B_id -> predicted allocation set (empty = abstained)."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            out[r["B_id"]] = frozenset(json.loads(r["targetAllocation"] or "[]"))
    return out
