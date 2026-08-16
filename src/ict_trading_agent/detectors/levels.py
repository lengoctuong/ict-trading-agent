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
    def validate_reference_type(self) -> "ReferenceLevel":
        if self.fact_type not in REFERENCE_FACT_TYPES:
            raise ValueError("unsupported reference-level fact type")
        if self.price <= 0:
            raise ValueError("reference price must be positive")
        return self

    @classmethod
    def from_fact(cls, fact: ObservableFact) -> "ReferenceLevel":
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
    version = "0.1.0"

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
        }
        breach = ObservableFact(
            fact_id=stable_fact_id(
                FactType.LEVEL_BREACH.value,
                reference.reference_fact_id,
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
        if (
            breach.metrics.get("reference_fact_id")
            != reclaim.metrics.get("reference_fact_id")
        ):
            raise ValueError("breach and reclaim must use the same reference")
        if breach.available_at != reclaim.available_at:
            raise ValueError("v0 liquidity raid requires a same-bar reclaim")
        if (
            breach.symbol != reclaim.symbol
            or breach.timeframe != reclaim.timeframe
            or breach.occurred_at != reclaim.occurred_at
        ):
            raise ValueError("v0 liquidity raid facts must describe the same bar")

        side = LiquiditySide(str(breach.metrics["reference_side"]))
        direction = (
            Direction.BEARISH
            if side == LiquiditySide.BUY_SIDE
            else Direction.BULLISH
        )
        return ConceptCandidate(
            candidate_id=stable_candidate_id(
                CandidateType.LIQUIDITY_EVENT.value,
                breach.metrics["reference_fact_id"],
                breach.occurred_at.isoformat(),
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
                "same_bar_reclaim": True,
            },
            machine_labels=[
                "canonical_same_bar_sweep_candidate",
                "reclaimed_reference_level",
            ],
        )
