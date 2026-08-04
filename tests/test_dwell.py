"""Dwell time (always a range) and window packing (k_max, with the inversion)."""

from __future__ import annotations

import pytest

# =====================================================================
# The formula and its floor
# =====================================================================

def test_formula_matches_the_spec(travel_vault):
    import dwell
    # 40 * 0.6 / 8 = 3.0
    assert dwell.dwell_days(40, 0.6, 8) == 3
    # 45 * 0.6 / 8 = 3.375 -> ceil 4
    assert dwell.dwell_days(45, 0.6, 8) == 4


def test_floor_of_two_days_applies(travel_vault):
    import dwell
    # 4 * 0.5 / 8 = 0.25 -> ceil 1, floored to 2
    assert dwell.dwell_days(4, 0.5, 8) == 2
    assert dwell.MIN_DWELL_DAYS == 2


def test_estimate_never_returns_a_bare_point_value(travel_vault):
    """A range with a confidence is the answer; there is no `days` key to grab."""
    import dwell
    payload = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8, hours_of_interest=40,
    ).to_dict()
    assert "days" not in payload
    assert payload["days_low"] and payload["days_high"]
    assert payload["confidence"] in ("low", "medium", "high")
    assert payload["days_low"] <= payload["days_high"]


def test_derived_spread_widens_the_range_around_the_point(travel_vault):
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8, hours_of_interest=40,
    )
    # +/-25% of 40h -> 30h..50h -> ceil(2.25)=3 .. ceil(3.75)=4
    assert (est.days_low, est.days_high) == (3, 4)
    assert est.basis["hours_source"] == "derived-from-single-estimate"


def test_a_derived_estimate_can_never_claim_high_confidence(travel_vault):
    """Nobody measured the hours, so the arithmetic looking tight proves nothing."""
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.5, discretionary_hours=8, hours_of_interest=4,
    )
    assert est.days_low == est.days_high == 2  # both ends hit the floor
    assert est.confidence == "medium", "the cap must survive an exact-looking range"


def test_a_measured_range_can_reach_high_confidence(travel_vault):
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8,
        hours_low=40, hours_high=40,
    )
    assert est.basis["hours_source"] == "measured-range"
    assert est.confidence == "high"


def test_a_wide_derived_spread_drops_to_low_confidence(travel_vault):
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8,
        hours_of_interest=40, hours_uncertainty=0.5,
    )
    assert est.confidence == "low"


def test_a_wide_measured_range_is_low_confidence(travel_vault):
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8,
        hours_low=20, hours_high=120,
    )
    assert est.days_high > est.days_low * 1.5
    assert est.confidence == "low"


def test_bad_inputs_are_rejected(travel_vault):
    import dwell
    import validators as V
    with pytest.raises(V.ValidationError):  # completionism outside 0..1
        dwell.estimate("A", completionism=1.5, discretionary_hours=8, hours_of_interest=40)
    with pytest.raises(V.ValidationError):  # a day has no zero discretionary hours
        dwell.estimate("A", completionism=0.5, discretionary_hours=0, hours_of_interest=40)
    with pytest.raises(V.ValidationError):  # neither hours form supplied
        dwell.estimate("A", completionism=0.5, discretionary_hours=8)
    with pytest.raises(V.ValidationError):  # half a measured range
        dwell.estimate("A", completionism=0.5, discretionary_hours=8, hours_low=10)
    with pytest.raises(V.ValidationError):  # reversed measured range
        dwell.estimate("A", completionism=0.5, discretionary_hours=8,
                       hours_low=50, hours_high=10)


# =====================================================================
# Window packing
# =====================================================================

def test_k_max_matches_the_formula(travel_vault):
    import dwell
    # floor((21 + 1) / (4 + 1)) = 4
    assert dwell.max_places_in_window(21, 4, 1) == 4
    # floor((21 + 1) / (3 + 1)) = 5
    assert dwell.max_places_in_window(21, 3, 1) == 5
    # no overhead: floor(21 / 3) = 7
    assert dwell.max_places_in_window(21, 3, 0) == 7


def test_k_max_is_consistent_with_the_stay_plus_transition_budget(travel_vault):
    """k places need k stays plus the k-1 transitions between them."""
    import dwell
    length, stay, overhead = 21, 4, 1
    k = dwell.max_places_in_window(length, stay, overhead)
    assert k * stay + (k - 1) * overhead <= length
    assert (k + 1) * stay + k * overhead > length


def test_nothing_fits_in_an_empty_window(travel_vault):
    import dwell
    assert dwell.max_places_in_window(0, 3, 1) == 0
    assert dwell.max_places_in_window(2, 5, 1) == 0


def test_longer_stays_mean_fewer_places(travel_vault):
    """The inversion: days_high feeds places_low, not places_high."""
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8, hours_of_interest=40,
    )
    packed = dwell.pack_window(21, est, overhead_days=1)
    assert (packed["dwell_days_low"], packed["dwell_days_high"]) == (3, 4)
    assert packed["places_low"] == 4   # from the LONGER stay
    assert packed["places_high"] == 5  # from the SHORTER stay
    assert packed["places_low"] < packed["places_high"]


def test_packing_carries_the_confidence_through(travel_vault):
    import dwell
    est = dwell.estimate(
        "Placeholder City", completionism=0.6, discretionary_hours=8,
        hours_low=40, hours_high=40,
    )
    assert dwell.pack_window(21, est, 1)["confidence"] == "high"


def test_zero_dwell_and_zero_overhead_is_rejected(travel_vault):
    import dwell
    import validators as V
    with pytest.raises(V.ValidationError):
        dwell.max_places_in_window(21, 0, 0)
