from __future__ import annotations

from ..candidates import ConceptCandidate
from ..enums import (
    CandidateType,
    Direction,
    FactType,
    LiquiditySide,
    StructureBreakType,
)
from ..facts import ObservableFact, PriceGeometry
from ..market import OHLCBar
from .common import normalize_to_tick, stable_candidate_id, stable_fact_id
from .levels import ReferenceLevel, validate_reference_for_bar


class PriceBreakDetector:
    name = "PriceBreakDetector"
    version = "0.1.2"

    def __init__(self, *, tick_size: float) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be positive")
        self.tick_size = tick_size

    def detect(
        self,
        bar: OHLCBar,
        reference: ReferenceLevel,
        *,
        previous_close: float | None = None,
    ) -> ObservableFact | None:
        """Detect a favourable close, optionally requiring a cross-TF crossing.

        A same-timeframe reference is structurally consumed after its first
        confirmed break.  A lower-timeframe close cannot consume a higher-timeframe
        reference, so it must cross from the non-broken side to avoid emitting the
        same interaction once per bar while price remains beyond the level.
        """

        validate_reference_for_bar(bar, reference)
        if reference.fact_type != FactType.SWING_POINT:
            raise ValueError("structure breaks require a confirmed swing reference")
        level = normalize_to_tick(reference.price, self.tick_size)
        close = normalize_to_tick(bar.close, self.tick_size)
        if reference.side == LiquiditySide.BUY_SIDE and close > level:
            direction = Direction.BULLISH
            distance = close - level
        elif reference.side == LiquiditySide.SELL_SIDE and close < level:
            direction = Direction.BEARISH
            distance = level - close
        else:
            return None

        same_timeframe = reference.timeframe == bar.timeframe
        if not same_timeframe and previous_close is not None:
            prior_close = normalize_to_tick(previous_close, self.tick_size)
            crossed_from_non_broken_side = (
                prior_close <= level
                if reference.side == LiquiditySide.BUY_SIDE
                else prior_close >= level
            )
            if not crossed_from_non_broken_side:
                return None

        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.PRICE_BREAK.value,
                reference.reference_fact_id,
                bar.timeframe.value,
                bar.open_time.isoformat(),
            ),
            fact_type=FactType.PRICE_BREAK,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            occurred_at=bar.open_time,
            confirmed_at=bar.close_time,
            available_at=bar.close_time,
            direction=direction,
            geometry=PriceGeometry(price=level, extreme=close),
            source_fact_ids=[reference.reference_fact_id],
            metrics={
                "reference_fact_id": reference.reference_fact_id,
                "reference_side": reference.side.value,
                "reference_price": level,
                "close_price": close,
                "close_distance_points": distance,
                "break_method": "close",
                "detection_timeframe": bar.timeframe.value,
                "reference_timeframe": (
                    reference.timeframe.value
                    if reference.timeframe is not None
                    else None
                ),
                "same_timeframe_structure_eligible": (
                    same_timeframe
                ),
                "cross_timeframe_close_transition": not same_timeframe,
            },
            detector_name=self.name,
            detector_version=self.version,
        )


class StructureBreakCandidateDetector:
    version = "0.1.0"

    def detect(self, price_break: ObservableFact) -> ConceptCandidate:
        if price_break.fact_type != FactType.PRICE_BREAK:
            raise ValueError("structure candidate requires a price-break fact")
        if price_break.direction not in {Direction.BULLISH, Direction.BEARISH}:
            raise ValueError("price-break fact requires a directional close")
        same_timeframe = bool(
            price_break.metrics.get("same_timeframe_structure_eligible")
        )
        return ConceptCandidate(
            candidate_id=stable_candidate_id(
                CandidateType.STRUCTURE_BREAK.value,
                price_break.fact_id,
            ),
            candidate_type=CandidateType.STRUCTURE_BREAK,
            symbol=price_break.symbol,
            timeframe=price_break.timeframe,
            direction=price_break.direction,
            occurred_at=price_break.occurred_at,
            available_at=price_break.available_at,
            evidence_fact_ids=[price_break.fact_id, *price_break.source_fact_ids],
            raw_features={
                **price_break.metrics,
                "structure_break_type": StructureBreakType.UNCLASSIFIED.value,
            },
            machine_labels=[
                "close_through_confirmed_swing",
                "unclassified_structure_break",
                (
                    "same_timeframe_structure_eligible"
                    if same_timeframe
                    else "cross_timeframe_reference_interaction"
                ),
            ],
        )
