from __future__ import annotations

from pydantic import AwareDatetime, model_validator

from ..base import NonEmptyStr, SchemaModel
from ..candidates import ConceptCandidate
from ..enums import CandidateType, Direction, FactType, LiquiditySide, Timeframe
from ..facts import ObservableFact, PriceGeometry
from ..market import OHLCBar
from .common import (
    normalize_to_tick,
    stable_candidate_id,
    stable_fact_id,
)

REFERENCE_FACT_TYPES = {
    FactType.SWING_POINT,
    FactType.SESSION_LEVEL,
    FactType.PREVIOUS_DAY_LEVEL,
}


class ReferenceLevel(SchemaModel):
    reference_fact_id: NonEmptyStr
    fact_type: FactType
    symbol: NonEmptyStr
    timeframe: Timeframe | None = None
    side: LiquiditySide
    price: float
    available_at: AwareDatetime

    @model_validator(mode="after")
    def validate_reference_type(self) -> ReferenceLevel:
        if self.fact_type not in REFERENCE_FACT_TYPES:
            raise ValueError("unsupported reference-level fact type")
        if self.price <= 0:
            raise ValueError("reference price must be positive")
        return self

    @classmethod
    def from_fact(cls, fact: ObservableFact) -> ReferenceLevel:
        if fact.fact_type not in REFERENCE_FACT_TYPES:
            raise ValueError("fact is not a supported reference level")
        if fact.geometry is None or fact.geometry.price is None:
            raise ValueError("reference fact requires point-price geometry")
        side_value = fact.metrics.get("side")
        if side_value == "high":
            side = LiquiditySide.BUY_SIDE
        elif side_value == "low":
            side = LiquiditySide.SELL_SIDE
        else:
            raise ValueError("reference fact metrics.side must be high or low")
        return cls(
            reference_fact_id=fact.fact_id,
            fact_type=fact.fact_type,
            symbol=fact.symbol,
            timeframe=fact.timeframe,
            side=side,
            price=fact.geometry.price,
            available_at=fact.available_at,
        )


def validate_reference_for_bar(bar: OHLCBar, reference: ReferenceLevel) -> None:
    if not bar.is_closed:
        raise ValueError("level detectors require a closed bar")
    if bar.symbol != reference.symbol:
        raise ValueError("bar and reference symbol must match")
    if reference.available_at > bar.open_time:
        raise ValueError("reference must be available before the bar opens")


class LevelInteractionDetector:
    name = "LevelInteractionDetector"
    version = "0.1.1"

    def __init__(self, *, tick_size: float) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be positive")
        self.tick_size = tick_size

    def detect(
        self,
        bar: OHLCBar,
        reference: ReferenceLevel,
    ) -> tuple[ObservableFact, ...]:
        validate_reference_for_bar(bar, reference)
        level = normalize_to_tick(reference.price, self.tick_size)
        high = normalize_to_tick(bar.high, self.tick_size)
        low = normalize_to_tick(bar.low, self.tick_size)
        close = normalize_to_tick(bar.close, self.tick_size)
        candle_range = high - low

        if reference.side == LiquiditySide.BUY_SIDE:
            breached = high > level
            reclaimed = breached and close < level
            extreme = high
            penetration = high - level
            rejection_fraction = (high - close) / candle_range if candle_range else 0.0
        else:
            breached = low < level
            reclaimed = breached and close > level
            extreme = low
            penetration = level - low
            rejection_fraction = (close - low) / candle_range if candle_range else 0.0

        if not breached:
            return ()

        common_metrics = {
            "reference_fact_id": reference.reference_fact_id,
            "reference_fact_type": reference.fact_type.value,
            "reference_side": reference.side.value,
            "reference_price": level,
            "extreme": extreme,
            "close_price": close,
            "penetration_points": penetration,
            "rejection_fraction": rejection_fraction,
            "detection_timeframe": bar.timeframe.value,
            "reference_timeframe": (
                reference.timeframe.value if reference.timeframe is not None else None
            ),
        }
        breach = ObservableFact(
            fact_id=stable_fact_id(
                FactType.LEVEL_BREACH.value,
                reference.reference_fact_id,
                bar.timeframe.value,
                bar.open_time.isoformat(),
            ),
            fact_type=FactType.LEVEL_BREACH,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            occurred_at=bar.open_time,
            confirmed_at=bar.close_time,
            available_at=bar.close_time,
            geometry=PriceGeometry(price=level, extreme=extreme),
            source_fact_ids=[reference.reference_fact_id],
            metrics=common_metrics,
            detector_name=self.name,
            detector_version=self.version,
        )
        if not reclaimed:
            return (breach,)

        reclaim = ObservableFact(
            fact_id=stable_fact_id(
                FactType.LEVEL_RECLAIM.value,
                reference.reference_fact_id,
                bar.timeframe.value,
                bar.open_time.isoformat(),
            ),
            fact_type=FactType.LEVEL_RECLAIM,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            occurred_at=bar.open_time,
            confirmed_at=bar.close_time,
            available_at=bar.close_time,
            geometry=PriceGeometry(price=level, extreme=extreme),
            source_fact_ids=[reference.reference_fact_id, breach.fact_id],
            metrics=common_metrics | {"same_bar_reclaim": True},
            detector_name=self.name,
            detector_version=self.version,
        )
        return breach, reclaim

    def detect_reclaim(
        self,
        bar: OHLCBar,
        reference: ReferenceLevel,
        breach: ObservableFact,
        *,
        reclaim_span_bars: int,
        episode_extreme: float,
    ) -> ObservableFact | None:
        """Detect a later close reclaim while retaining the original breach."""

        validate_reference_for_bar(bar, reference)
        if breach.fact_type != FactType.LEVEL_BREACH:
            raise ValueError("multi-bar reclaim requires a level-breach fact")
        if breach.metrics.get("reference_fact_id") != reference.reference_fact_id:
            raise ValueError("breach does not belong to the supplied reference")
        if breach.timeframe != bar.timeframe:
            raise ValueError("reclaim must use the breach detection timeframe")
        if reclaim_span_bars < 1:
            raise ValueError("later reclaim span must be at least one bar")
        level = normalize_to_tick(reference.price, self.tick_size)
        close = normalize_to_tick(bar.close, self.tick_size)
        reclaimed = (
            close < level if reference.side == LiquiditySide.BUY_SIDE else close > level
        )
        if not reclaimed:
            return None
        extreme = normalize_to_tick(episode_extreme, self.tick_size)
        penetration = (
            extreme - level
            if reference.side == LiquiditySide.BUY_SIDE
            else level - extreme
        )
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.LEVEL_RECLAIM.value,
                breach.fact_id,
                bar.timeframe.value,
                bar.open_time.isoformat(),
            ),
            fact_type=FactType.LEVEL_RECLAIM,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            occurred_at=bar.open_time,
            confirmed_at=bar.close_time,
            available_at=bar.close_time,
            geometry=PriceGeometry(price=level, extreme=extreme),
            source_fact_ids=[reference.reference_fact_id, breach.fact_id],
            metrics={
                **breach.metrics,
                "extreme": extreme,
                "close_price": close,
                "penetration_points": penetration,
                "same_bar_reclaim": False,
                "reclaim_span_bars": reclaim_span_bars,
            },
            detector_name=self.name,
            detector_version=self.version,
        )


class LiquidityRaidCandidateDetector:
    version = "0.1.0"

    def detect(
        self,
        breach: ObservableFact,
        reclaim: ObservableFact,
    ) -> ConceptCandidate:
        if breach.fact_type != FactType.LEVEL_BREACH:
            raise ValueError("liquidity raid requires a level-breach fact")
        if reclaim.fact_type != FactType.LEVEL_RECLAIM:
            raise ValueError("liquidity raid requires a level-reclaim fact")
        if breach.fact_id not in reclaim.source_fact_ids:
            raise ValueError("reclaim must reference its breach fact")
        if breach.metrics.get("reference_fact_id") != reclaim.metrics.get(
            "reference_fact_id"
        ):
            raise ValueError("breach and reclaim must use the same reference")
        if breach.symbol != reclaim.symbol or breach.timeframe != reclaim.timeframe:
            raise ValueError("liquidity raid facts must share symbol/timeframe")

        same_bar = breach.occurred_at == reclaim.occurred_at
        if same_bar != bool(reclaim.metrics.get("same_bar_reclaim")):
            raise ValueError("reclaim timing metadata does not match its facts")
        reclaim_span_bars = int(reclaim.metrics.get("reclaim_span_bars", 0))
        if same_bar and reclaim_span_bars != 0:
            raise ValueError("same-bar reclaim must use a zero-bar span")
        if not same_bar and reclaim_span_bars < 1:
            raise ValueError("multi-bar reclaim requires a positive bar span")

        side = LiquiditySide(str(breach.metrics["reference_side"]))
        direction = (
            Direction.BEARISH if side == LiquiditySide.BUY_SIDE else Direction.BULLISH
        )
        return ConceptCandidate(
            candidate_id=stable_candidate_id(
                CandidateType.LIQUIDITY_EVENT.value,
                breach.metrics["reference_fact_id"],
                breach.occurred_at.isoformat(),
                breach.timeframe.value if breach.timeframe is not None else "none",
            ),
            candidate_type=CandidateType.LIQUIDITY_EVENT,
            symbol=breach.symbol,
            timeframe=breach.timeframe,
            direction=direction,
            occurred_at=breach.occurred_at,
            available_at=reclaim.available_at,
            evidence_fact_ids=[
                str(breach.metrics["reference_fact_id"]),
                breach.fact_id,
                reclaim.fact_id,
            ],
            raw_features={
                **breach.metrics,
                **reclaim.metrics,
                "same_bar_reclaim": same_bar,
                "reclaim_span_bars": reclaim_span_bars,
                "breach_available_at": breach.available_at.isoformat(),
                "reclaim_available_at": reclaim.available_at.isoformat(),
            },
            machine_labels=[
                (
                    "canonical_same_bar_sweep_candidate"
                    if same_bar
                    else "permissive_multi_bar_sweep_candidate"
                ),
                "reclaimed_reference_level",
            ],
        )
