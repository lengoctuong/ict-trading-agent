from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256

from ..market import OHLCBar, bars_are_contiguous


def normalize_to_tick(value: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    tick = Decimal(str(tick_size))
    units = (Decimal(str(value)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(units * tick)


def validate_triplet(left: OHLCBar, middle: OHLCBar, right: OHLCBar) -> None:
    if not (left.is_closed and middle.is_closed and right.is_closed):
        raise ValueError("detectors only consume closed bars")
    if not (bars_are_contiguous(left, middle) and bars_are_contiguous(middle, right)):
        raise ValueError("detector triplet must contain contiguous same-timeframe bars")


def stable_fact_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return "fact-" + sha256(raw.encode("utf-8")).hexdigest()[:24]


def stable_candidate_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return "candidate-" + sha256(raw.encode("utf-8")).hexdigest()[:24]
