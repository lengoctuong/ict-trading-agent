from __future__ import annotations

from collections.abc import Sequence

from ..enums import Direction, FactType
from ..facts import ObservableFact, PriceGeometry
from ..market import OHLCBar
from .common import normalize_to_tick, stable_fact_id, validate_triplet


class FVGGeometryDetector:
    name = "FVGGeometryDetector"
    version = "0.1.0"

    def __init__(self, *, tick_size: float) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be positive")
        self.tick_size = tick_size

    def detect_triplet(
        self,
        left: OHLCBar,
        middle: OHLCBar,
        right: OHLCBar,
    ) -> tuple[ObservableFact, ...]:
        validate_triplet(left, middle, right)
        left_high = normalize_to_tick(left.high, self.tick_size)
        left_low = normalize_to_tick(left.low, self.tick_size)
        right_high = normalize_to_tick(right.high, self.tick_size)
        right_low = normalize_to_tick(right.low, self.tick_size)
        facts: list[ObservableFact] = []
        if right_low > left_high:
            facts.append(
                self._fact(
                    middle,
                    right,
                    Direction.BULLISH,
                    low=left_high,
                    high=right_low,
                )
            )
        if right_high < left_low:
            facts.append(
                self._fact(
                    middle,
                    right,
                    Direction.BEARISH,
                    low=right_high,
                    high=left_low,
                )
            )
        return tuple(facts)

    def detect(self, bars: Sequence[OHLCBar]) -> tuple[ObservableFact, ...]:
        facts: list[ObservableFact] = []
        for index in range(1, len(bars) - 1):
            facts.extend(self.detect_triplet(bars[index - 1], bars[index], bars[index + 1]))
        return tuple(facts)

    def _fact(
        self,
        middle: OHLCBar,
        right: OHLCBar,
        direction: Direction,
        *,
        low: float,
        high: float,
    ) -> ObservableFact:
        size = high - low
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.FVG_GEOMETRY.value,
                middle.symbol,
                middle.timeframe.value,
                middle.open_time.isoformat(),
                direction.value,
            ),
            fact_type=FactType.FVG_GEOMETRY,
            symbol=middle.symbol,
            timeframe=middle.timeframe,
            occurred_at=middle.open_time,
            confirmed_at=right.close_time,
            available_at=right.close_time,
            direction=direction,
            geometry=PriceGeometry(low=low, high=high),
            metrics={
                "size_points": size,
                "ce": low + size / 2,
            },
            detector_name=self.name,
            detector_version=self.version,
        )

