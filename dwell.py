"""Dwell time and window packing.

    days = ceil(H * completionism / discretionary_hours), floor of 2

H is an ESTIMATE of how many hours of interest a place holds, so a single number
out of that formula would be false precision. Every estimate here is returned as
a RANGE with a confidence, and `to_dict()` deliberately emits no `days` key —
there is no point value for a caller to grab by accident.

Where the range comes from:
  - `hours_low` + `hours_high` given: the range is the caller's own measured
    spread, and confidence is read from how tight it is.
  - single `hours_of_interest` given: the spread is derived from
    `hours_uncertainty` (default +/-25%), and confidence is CAPPED at medium
    because nobody measured it.

Window packing, per window:

    k_max = floor((L + overhead) / (dwell + overhead))

which is the inverse of `k * dwell + (k - 1) * overhead <= L`: k places need k
stays plus the k-1 transitions between them. More days per place means FEWER
places, so the low end of the dwell range produces the HIGH end of the place
count. That inversion is easy to get backwards, so `pack_window` does it.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

import validators as V

#: No place is worth a stay shorter than this once travel days are counted.
MIN_DWELL_DAYS = 2

#: Default +/- spread applied to a single unmeasured hours estimate.
DEFAULT_HOURS_UNCERTAINTY = 0.25

CONFIDENCE_ORDER = ("low", "medium", "high")


def _cap(confidence: str, ceiling: str) -> str:
    return confidence if CONFIDENCE_ORDER.index(confidence) <= CONFIDENCE_ORDER.index(ceiling) else ceiling


def dwell_days(hours: float, completionism: float, discretionary_hours: float) -> int:
    """The formula, with the floor applied. Internal: callers use `estimate`."""
    raw = ceil((hours * completionism) / discretionary_hours)
    return max(MIN_DWELL_DAYS, int(raw))


@dataclass(frozen=True)
class DwellEstimate:
    place: str
    days_low: int
    days_high: int
    confidence: str
    basis: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        # No `days` key by design — a range with a confidence is the answer.
        return {
            "place": self.place,
            "days_low": self.days_low,
            "days_high": self.days_high,
            "confidence": self.confidence,
            "formula": "days = ceil(hours * completionism / discretionary_hours), floor 2",
            "basis": self.basis,
        }


def estimate(
    place: Any,
    *,
    completionism: Any,
    discretionary_hours: Any,
    hours_of_interest: Any = None,
    hours_low: Any = None,
    hours_high: Any = None,
    hours_uncertainty: Any = DEFAULT_HOURS_UNCERTAINTY,
) -> DwellEstimate:
    """Days to spend in one place, as a range with a confidence."""
    place_v = V.validate_name(place, field="place")
    completion = V.validate_unit_interval(completionism, field="completionism")
    if completion == 0:
        raise V.ValidationError("completionism must be greater than 0 to estimate a dwell")
    discretionary = V.validate_positive_number(
        discretionary_hours, field="discretionary_hours", maximum=24
    )

    measured = hours_low is not None or hours_high is not None
    if measured:
        if hours_low is None or hours_high is None:
            raise V.ValidationError("supply BOTH hours_low and hours_high, or neither")
        low_h = V.validate_positive_number(hours_low, field="hours_low")
        high_h = V.validate_positive_number(hours_high, field="hours_high")
        if high_h < low_h:
            raise V.ValidationError("hours_high must be >= hours_low")
        spread = None  # the caller measured the spread; nothing is derived
    else:
        if hours_of_interest is None:
            raise V.ValidationError("supply hours_of_interest, or hours_low + hours_high")
        centre = V.validate_positive_number(hours_of_interest, field="hours_of_interest")
        spread = V.validate_unit_interval(hours_uncertainty, field="hours_uncertainty")
        low_h = centre * (1.0 - spread)
        high_h = centre * (1.0 + spread)
        if low_h <= 0:
            low_h = centre  # a 100% spread would otherwise put the low end at zero hours

    days_low = dwell_days(low_h, completion, discretionary)
    days_high = max(days_low, dwell_days(high_h, completion, discretionary))

    if days_low == days_high:
        confidence = "high"
    elif days_high <= days_low * 1.5:
        confidence = "medium"
    else:
        confidence = "low"
    if not measured:
        # Nobody measured the hours, so the answer cannot be high-confidence
        # however tight the arithmetic looks.
        confidence = _cap(confidence, "medium")
        if spread is not None and spread >= 0.4:
            confidence = "low"

    return DwellEstimate(
        place=place_v,
        days_low=days_low,
        days_high=days_high,
        confidence=confidence,
        basis={
            "hours_low": round(low_h, 2),
            "hours_high": round(high_h, 2),
            "hours_source": "measured-range" if measured else "derived-from-single-estimate",
            "completionism": completion,
            "discretionary_hours": discretionary,
            "min_dwell_days": MIN_DWELL_DAYS,
        },
    )


def max_places_in_window(window_days: Any, dwell_days_per_place: Any,
                         overhead_days: Any = 0) -> int:
    """k_max = floor((L + overhead) / (dwell + overhead)); 0 when nothing fits."""
    length = V.validate_non_negative_int(window_days, field="window_days")
    dwell = V.validate_non_negative_int(dwell_days_per_place, field="dwell_days")
    overhead = V.validate_non_negative_int(overhead_days, field="overhead_days")
    if dwell + overhead <= 0:
        raise V.ValidationError("dwell_days + overhead_days must be greater than 0")
    if length <= 0:
        return 0
    return max(0, (length + overhead) // (dwell + overhead))


def pack_window(window_days: Any, est: DwellEstimate, overhead_days: Any = 0) -> dict[str, Any]:
    """How many places fit in one window, as a range.

    Longer stays mean fewer places: days_high feeds places_low and vice versa.
    """
    overhead = V.validate_non_negative_int(overhead_days, field="overhead_days")
    length = V.validate_non_negative_int(window_days, field="window_days")
    places_low = max_places_in_window(length, est.days_high, overhead)
    places_high = max_places_in_window(length, est.days_low, overhead)
    return {
        "window_days": length,
        "overhead_days": overhead,
        "dwell_days_low": est.days_low,
        "dwell_days_high": est.days_high,
        "places_low": places_low,
        "places_high": places_high,
        "confidence": est.confidence,
        "formula": "k_max = floor((L + overhead) / (dwell + overhead))",
    }
