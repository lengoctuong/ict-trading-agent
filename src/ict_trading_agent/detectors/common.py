from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256

from ..market import BarAdjacencyPolicy, OHLCBar, bars_are_contiguous


def normalize_to_tick(value: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    tick = Decimal(str(tick_size))
    units = (Decimal(str(value)) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return float(units * tick)


def validate_triplet(
    left: OHLCBar,
    middle: OHLCBar,
    right: OHLCBar,
    adjacency_policy: BarAdjacencyPolicy | None = None,
) -> None:
    if not (left.is_closed and middle.is_closed and right.is_closed):
        raise ValueError("detectors only consume closed bars")
    if not (
        bars_are_contiguous(left, middle, adjacency_policy)
        and bars_are_contiguous(middle, right, adjacency_policy)
    ):
        raise ValueError("detector triplet must contain contiguous same-timeframe bars")


def stable_fact_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return "fact-" + sha256(raw.encode("utf-8")).hexdigest()[:24]


def stable_candidate_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return "candidate-" + sha256(raw.encode("utf-8")).hexdigest()[:24]
