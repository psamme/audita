"""Client C (BenchRec) convention tier: band picking, the penny convention, subset sums, grading. No dataset needed."""

from benchrec.data import Record
from benchrec.induce import Residue, Resolution, _pick_band, _unique_subset, cand_penny, is_right


def _a(i, cents, day=100, refs="INV12345678"):
    return Record("A", f"a{i}", cents, day, "USD", "ACC", "DR", refs, "", allocation=f"alloc{i}", tokens=(refs,))


def _b(i, cents, day=100, text="PAYMENT INV12345678"):
    return Record("B", f"b{i}", cents, day, "USD", "ACC", "CR", text, "", squashed=text)


def test_band_stops_at_the_first_value_that_breaks_the_floor():
    scored = [(1, True)] * 100 + [(5, True)] * 60 + [(900, False)] * 10 + [(2000, True)] * 500
    assert _pick_band(scored, 0.98)[0] == 5  # the clean wide tail cannot rescue the sloppy middle


def test_band_is_none_when_the_narrowest_setting_already_fails():
    assert _pick_band([(2, True)] * 9 + [(2, False)] * 3, 0.98) is None


def test_penny_difference_needs_a_reference_and_a_unique_counterpart():
    rz = Residue([_a(1, 10_000), _a(2, 5_000, refs="ZZZ99999999")], [_b(1, 10_003), _b(2, 5_001, text="NO REF HERE")], [])
    got = list(cand_penny(rz, 50))
    assert [(r.b_ids, r.a_ids, r.param) for r in got] == [(("b1",), ("a1",), 3)]
    # a second bank line carrying the same reference makes it ambiguous: nobody is matched
    rz = Residue([_a(1, 10_000)], [_b(1, 10_003), _b(2, 10_004)], [])
    assert list(cand_penny(rz, 50)) == []


def test_unique_subset_refuses_two_ways_to_make_the_sum():
    assert _unique_subset([_a(1, 60), _a(2, 40), _a(3, 7)], 100) is not None
    assert _unique_subset([_a(1, 60), _a(2, 40), _a(3, 60), _a(4, 99)], 100) is None


def test_grading_is_pair_level_and_offsets_need_a_blank_key():
    truth = {"b1": frozenset({"x", "y"}), "b2": frozenset(), "b3": frozenset()}
    assert is_right(Resolution(("b1",), ("a",), frozenset({"x"}), "penny_difference", 1), truth)
    assert not is_right(Resolution(("b1",), ("a",), frozenset({"z"}), "penny_difference", 1), truth)
    assert is_right(Resolution(("b2", "b3"), (), frozenset(), "bank_side_offset", 0, verdict="no_ledger"), truth)
    assert not is_right(Resolution(("b1", "b2"), (), frozenset(), "bank_side_offset", 0, verdict="no_ledger"), truth)
