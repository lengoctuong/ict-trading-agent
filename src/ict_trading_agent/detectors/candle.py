from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from statistics import median

from pydantic import Field

from ..base import SchemaModel
from ..candidates import ConceptCandidate
from ..enums import CandidateType, Direction, FactType
from ..facts import ObservableFact
from ..market import BarAdjacencyPolicy, OHLCBar, bars_are_contiguous
from .common import stable_candidate_id, stable_fact_id


class CandleFeatureConfig(SchemaModel):
    baseline_period: int = Field(default=20, ge=1)


class DisplacementThresholds(SchemaModel):
    """Research parameters, not universal ICT truth."""

    min_body_to_range: float = Field(default=0.70, ge=0.0, le=1.0)
    min_body_vs_baseline: float = Field(default=1.50, gt=0.0)
    max_opposing_wick_to_range: float = Field(default=0.20, ge=0.0, le=1.0)
    min_directional_close_location: float = Field(default=0.50, ge=0.50, le=1.0)


class CandleFeatureDetector:
    name = "CandleFeatureDetector"
    version = "0.1.0"

    def __init__(
        self,
        config: CandleFeatureConfig | None = None,
        *,
        adjacency_policy: BarAdjacencyPolicy | None = None,
    ) -> None:
        self.config = config or CandleFeatureConfig()
        self.adjacency_policy = adjacency_policy

    def detect(
        self,
        bar: OHLCBar,
        baseline_bars: Sequence[OHLCBar],
    ) -> ObservableFact:
        if not bar.is_closed:
            raise ValueError("candle features require a closed bar")
        if len(baseline_bars) < self.config.baseline_period:
            raise ValueError("insufficient closed bars for the configured baseline")
        baseline = list(baseline_bars[-self.config.baseline_period :])
        chain = [*baseline, bar]
        if any(not item.is_closed for item in chain):
            raise ValueError("candle feature baseline must contain closed bars only")
        if any(
            item.symbol != bar.symbol or item.timeframe != bar.timeframe
            for item in baseline
        ):
            raise ValueError("candle feature baseline must use one symbol/timeframe")
        if any(
            not bars_are_contiguous(left, right, self.adjacency_policy)
            for left, right in pairwise(chain)
        ):
            raise ValueError("candle feature baseline must be contiguous")

        candle_range = bar.high - bar.low
        body = abs(bar.close - bar.open)
        upper_wick = bar.high - max(bar.open, bar.close)
        lower_wick = min(bar.open, bar.close) - bar.low
        baseline_bodies = [abs(item.close - item.open) for item in baseline]
        baseline_ranges = [item.high - item.low for item in baseline]
        mean_body = sum(baseline_bodies) / len(baseline_bodies)
        median_body = median(baseline_bodies)
        mean_range = sum(baseline_ranges) / len(baseline_ranges)
        median_range = median(baseline_ranges)

        true_ranges: list[float] = []
        previous_close: float | None = None
        for item in baseline:
            true_range = item.high - item.low
            if previous_close is not None:
                true_range = max(
                    true_range,
                    abs(item.high - previous_close),
                    abs(item.low - previous_close),
                )
            true_ranges.append(true_range)
            previous_close = item.close
        baseline_atr = sum(true_ranges) / len(true_ranges)

        if bar.close > bar.open:
            direction = Direction.BULLISH
            opposing_wick = lower_wick
        elif bar.close < bar.open:
            direction = Direction.BEARISH
            opposing_wick = upper_wick
        else:
            direction = Direction.NEUTRAL
            opposing_wick = max(upper_wick, lower_wick)

        def ratio(numerator: float, denominator: float) -> float:
            return numerator / denominator if denominator > 0 else 0.0

        close_location = ratio(bar.close - bar.low, candle_range)
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.CANDLE_FEATURES.value,
                bar.symbol,
                bar.timeframe.value,
                bar.open_time.isoformat(),
            ),
            fact_type=FactType.CANDLE_FEATURES,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            occurred_at=bar.open_time,
            confirmed_at=bar.close_time,
            available_at=bar.close_time,
            direction=direction,
            metrics={
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "range": candle_range,
                "body": body,
                "upper_wick": upper_wick,
                "lower_wick": lower_wick,
                "opposing_wick": opposing_wick,
                "body_to_range": ratio(body, candle_range),
                "upper_wick_to_range": ratio(upper_wick, candle_range),
                "lower_wick_to_range": ratio(lower_wick, candle_range),
                "opposing_wick_to_range": ratio(opposing_wick, candle_range),
                "close_location": close_location,
                "mean_body": mean_body,
                "median_body": median_body,
                "mean_range": mean_range,
                "median_range": median_range,
                "atr": baseline_atr,
                "average_body_baseline": mean_body,
                "body_vs_baseline": ratio(body, mean_body),
                "atr_baseline": baseline_atr,
                "range_vs_atr": ratio(candle_range, baseline_atr),
                "baseline_period": self.config.baseline_period,
            },
            detector_name=self.name,
            detector_version=self.version,
        )


class DisplacementCandidateDetector:
    version = "0.1.0"

    def __init__(self, thresholds: DisplacementThresholds | None = None) -> None:
        self.thresholds = thresholds or DisplacementThresholds()

    def detect(self, feature_fact: ObservableFact) -> ConceptCandidate | None:
        if feature_fact.fact_type != FactType.CANDLE_FEATURES:
            raise ValueError("displacement requires a candle-features fact")
        if feature_fact.direction not in {Direction.BULLISH, Direction.BEARISH}:
            return None

        metrics = feature_fact.metrics
        directional_close_ok = (
            float(metrics["close_location"])
            >= self.thresholds.min_directional_close_location
            if feature_fact.direction == Direction.BULLISH
            else float(metrics["close_location"])
            <= 1.0 - self.thresholds.min_directional_close_location
        )
        criteria = {
            "body_to_range": float(metrics["body_to_range"])
            >= self.thresholds.min_body_to_range,
            "body_vs_baseline": float(metrics["body_vs_baseline"])
            >= self.thresholds.min_body_vs_baseline,
            "opposing_wick_to_range": float(metrics["opposing_wick_to_range"])
            <= self.thresholds.max_opposing_wick_to_range,
            "directional_close": directional_close_ok,
        }
        all_thresholds_passed = all(criteria.values())

        return ConceptCandidate(
            candidate_id=stable_candidate_id(
                CandidateType.DISPLACEMENT.value,
                feature_fact.fact_id,
            ),
            candidate_type=CandidateType.DISPLACEMENT,
            symbol=feature_fact.symbol,
            timeframe=feature_fact.timeframe,
            direction=feature_fact.direction,
            occurred_at=feature_fact.occurred_at,
            available_at=feature_fact.available_at,
            evidence_fact_ids=[feature_fact.fact_id],
            raw_features={
                **metrics,
                "criteria": criteria,
                "all_thresholds_passed": all_thresholds_passed,
                "thresholds": self.thresholds.model_dump(mode="json"),
            },
            machine_labels=["directional_repricing_candidate"],
        )
