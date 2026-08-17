from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, Field

from .base import NonEmptyStr, SchemaModel
from .enums import CandidateType, Direction, FactType, Timeframe
from .m4 import M4AuditEvent, M4EventKind, M4ReplayResult
from .market import OHLCBar


class DistributionStats(SchemaModel):
    count: int = Field(ge=0)
    minimum: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p90: float | None = None
    p95: float | None = None
    maximum: float | None = None
    mean: float | None = None


class ThresholdCoverage(SchemaModel):
    metric: NonEmptyStr
    timeframe: Timeframe | None = None
    current_threshold: int = Field(ge=0)
    observed_count: int = Field(ge=0)
    coverage_by_threshold: dict[int, float] = Field(default_factory=dict)
    interpretation: NonEmptyStr = "observational coverage; not a PnL optimization"


class M42OutcomeLabel(SchemaModel):
    label_id: NonEmptyStr
    setup_candidate_id: NonEmptyStr
    ready_at: AwareDatetime
    direction: Direction
    anchor_price: float = Field(gt=0.0)
    horizon_bars: int = Field(ge=1)
    bars_observed: int = Field(ge=0)
    censored: bool
    mfe_price: float = Field(ge=0.0)
    mae_price: float = Field(ge=0.0)
    mfe_ticks: float = Field(ge=0.0)
    mae_ticks: float = Field(ge=0.0)
    close_return_price: float | None = None
    first_target_candidate_id: str | None = None
    bars_to_first_target: int | None = Field(default=None, ge=1)


class M42ChartReviewItem(SchemaModel):
    review_id: NonEmptyStr
    status: str = "PENDING_USER_REVIEW"
    source_event_id: NonEmptyStr
    setup_candidate_id: str | None = None
    event_kind: M4EventKind
    category: NonEmptyStr
    as_of: AwareDatetime
    timeframe: Timeframe | None = None
    direction: Direction | None = None
    reason_codes: list[str] = Field(default_factory=list)
    evidence_payload: dict[str, Any]
    window_bars: list[OHLCBar] = Field(default_factory=list)
    review_note: str = "User will inspect chart later; no semantic verdict assigned."


class M42ResearchReport(SchemaModel):
    report_version: NonEmptyStr = "0.1.0"
    replay_run_id: NonEmptyStr
    symbol: NonEmptyStr
    generated_at: AwareDatetime
    distributions: dict[str, DistributionStats]
    threshold_sensitivity: list[ThresholdCoverage]
    outcome_label_count: int = Field(ge=0)
    chart_review_count: int = Field(ge=0)
    chart_review_status: str = "PENDING_USER_REVIEW"
    notes: list[str] = Field(default_factory=list)


class M42ResearchBundle(SchemaModel):
    report: M42ResearchReport
    outcomes: list[M42OutcomeLabel]
    chart_review_queue: list[M42ChartReviewItem]

    def export(self, output_directory: str | Path) -> dict[str, Path]:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        paths = {
            "report": output / "m42_report.json",
            "outcomes": output / "outcome_labels.jsonl",
            "chart_review": output / "chart_review_queue.jsonl",
        }
        paths["report"].write_text(
            self.report.model_dump_json(indent=2), encoding="utf-8"
        )
        paths["outcomes"].write_text(
            "".join(item.model_dump_json() + "\n" for item in self.outcomes),
            encoding="utf-8",
        )
        paths["chart_review"].write_text(
            "".join(item.model_dump_json() + "\n" for item in self.chart_review_queue),
            encoding="utf-8",
        )
        return paths


class M42ResearchAnalyzer:
    """Post-replay empirical labels; never changes frozen M3 detector semantics."""

    version = "0.1.0"

    def __init__(
        self,
        *,
        tick_size: float,
        entry_timeframe: Timeframe = Timeframe.M5,
        outcome_horizons: Sequence[int] = (12, 24, 48),
        max_chart_samples: int = 50,
        chart_bars_before: int = 12,
        chart_bars_after: int = 24,
    ) -> None:
        if tick_size <= 0:
            raise ValueError("M4.2 tick size must be positive")
        if not outcome_horizons or any(item < 1 for item in outcome_horizons):
            raise ValueError("outcome horizons must be positive")
        self.tick_size = tick_size
        self.entry_timeframe = entry_timeframe
        self.outcome_horizons = tuple(sorted(set(outcome_horizons)))
        self.max_chart_samples = max_chart_samples
        self.chart_bars_before = chart_bars_before
        self.chart_bars_after = chart_bars_after

    def analyze(
        self,
        replay: M4ReplayResult,
        bars: Sequence[OHLCBar],
        *,
        generated_at: AwareDatetime,
    ) -> M42ResearchBundle:
        analysis_events = [item for item in replay.events if item.included_in_analysis]
        distributions, raw = self._distributions(analysis_events, replay)
        sensitivity = self._sensitivity(raw, replay)
        entry_bars = sorted(
            (item for item in bars if item.timeframe == self.entry_timeframe),
            key=lambda item: item.open_time,
        )
        ready_events = [
            item for item in analysis_events if item.kind == M4EventKind.READY_PAYLOAD
        ]
        outcomes = self._outcomes(ready_events, entry_bars)
        queue = self._chart_queue(analysis_events, replay, entry_bars)
        return M42ResearchBundle(
            report=M42ResearchReport(
                replay_run_id=replay.run_id,
                symbol=replay.symbol,
                generated_at=generated_at,
                distributions=distributions,
                threshold_sensitivity=sensitivity,
                outcome_label_count=len(outcomes),
                chart_review_count=len(queue),
                notes=[
                    "Outcome labels are forward price-response labels from READY close, not simulated entries or PnL.",
                    "Threshold tables are observational coverage and do not authorize changing frozen M3 parameters.",
                    "Chart items remain PENDING_USER_REVIEW as requested; no ICT semantic verdict is inferred.",
                ],
            ),
            outcomes=outcomes,
            chart_review_queue=queue,
        )

    def _distributions(
        self, events: Sequence[M4AuditEvent], replay: M4ReplayResult
    ) -> tuple[dict[str, DistributionStats], dict[str, list[float]]]:
        raw: dict[str, list[float]] = defaultdict(list)
        for event in events:
            features = event.payload.get("raw_features", {})
            metrics = event.payload.get("metrics", {})
            if (
                event.kind == M4EventKind.CANDIDATE
                and event.category == CandidateType.LIQUIDITY_EVENT.value
            ):
                _append_number(
                    raw["reclaim_span_bars"], features.get("reclaim_span_bars")
                )
            if (
                event.kind == M4EventKind.CANDIDATE
                and event.category == CandidateType.SHIFT.value
            ):
                key = f"shift_lag_bars.{event.timeframe.value if event.timeframe else 'unknown'}"
                _append_number(raw[key], features.get("bars_after_raid"))
            if event.kind == M4EventKind.FACT:
                if event.category == FactType.FVG_REACTION.value:
                    key = f"reaction_lag_bars.{event.timeframe.value if event.timeframe else 'unknown'}"
                    _append_number(raw[key], metrics.get("reaction_lag_bars"))
                if event.category == FactType.RESEARCH_OBSERVATION.value:
                    _append_number(
                        raw["research.bars_after_shift"],
                        metrics.get("bars_after_shift"),
                    )
                    _append_number(
                        raw["research.bars_after_raid"], metrics.get("bars_after_raid")
                    )
        has_research_events = any(
            event.category == FactType.RESEARCH_OBSERVATION.value for event in events
        )
        for miss in replay.near_misses:
            if not miss.included_in_analysis:
                continue
            _append_number(raw["near_miss.distance_bars"], miss.distance_bars)
            _append_number(raw["near_miss.excess_bars"], miss.excess_bars)
            if not has_research_events:
                metrics = miss.payload.get("metrics", {})
                _append_number(
                    raw["research.bars_after_shift"],
                    metrics.get("bars_after_shift"),
                )
                _append_number(
                    raw["research.bars_after_raid"],
                    metrics.get("bars_after_raid"),
                )
        return ({key: _describe(values) for key, values in sorted(raw.items())}, raw)

    def _sensitivity(
        self, raw: dict[str, list[float]], replay: M4ReplayResult
    ) -> list[ThresholdCoverage]:
        policy = replay.manifest.m3_policy
        specs: list[tuple[str, Timeframe | None, int, tuple[int, ...]]] = [
            (
                "reclaim_span_bars",
                None,
                int(policy["reclaim_window_bars"]),
                (1, 2, 3, 4, 5),
            ),
        ]
        shift_windows = policy["shift_window_bars"]
        reaction_window = int(policy["reaction_confirmation_bars"])
        for timeframe in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
            shift = int(shift_windows[timeframe.value])
            specs.append(
                (
                    f"shift_lag_bars.{timeframe.value}",
                    timeframe,
                    shift,
                    tuple(sorted({max(1, shift // 2), shift, shift + 2, shift * 2})),
                )
            )
            specs.append(
                (
                    f"reaction_lag_bars.{timeframe.value}",
                    timeframe,
                    reaction_window,
                    tuple(
                        sorted(
                            {
                                1,
                                reaction_window,
                                reaction_window + 1,
                                reaction_window + 2,
                            }
                        )
                    ),
                )
            )
        result: list[ThresholdCoverage] = []
        for metric, timeframe, current, thresholds in specs:
            values = raw.get(metric, [])
            coverage = {
                threshold: (
                    sum(value <= threshold for value in values) / len(values)
                    if values
                    else 0.0
                )
                for threshold in thresholds
            }
            result.append(
                ThresholdCoverage(
                    metric=metric,
                    timeframe=timeframe,
                    current_threshold=current,
                    observed_count=len(values),
                    coverage_by_threshold=coverage,
                )
            )
        return result

    def _outcomes(
        self, ready_events: Sequence[M4AuditEvent], bars: Sequence[OHLCBar]
    ) -> list[M42OutcomeLabel]:
        outcomes: list[M42OutcomeLabel] = []
        for event in ready_events:
            if event.direction not in {Direction.BULLISH, Direction.BEARISH}:
                continue
            anchor_index = _latest_closed_index(bars, event.available_at)
            if anchor_index is None:
                continue
            anchor = bars[anchor_index].close
            targets = event.payload.get("targets", [])
            future = list(bars[anchor_index + 1 :])
            for horizon in self.outcome_horizons:
                observed = future[:horizon]
                highs = [item.high for item in observed]
                lows = [item.low for item in observed]
                closes = [item.close for item in observed]
                if event.direction == Direction.BULLISH:
                    mfe = max(0.0, max(highs, default=anchor) - anchor)
                    mae = max(0.0, anchor - min(lows, default=anchor))
                    close_return = closes[-1] - anchor if closes else None
                else:
                    mfe = max(0.0, anchor - min(lows, default=anchor))
                    mae = max(0.0, max(highs, default=anchor) - anchor)
                    close_return = anchor - closes[-1] if closes else None
                target_id, bars_to_target = _first_target_hit(
                    event.direction, targets, observed
                )
                raw_id = f"{event.setup_candidate_id}|{event.available_at.isoformat()}|{horizon}"
                outcomes.append(
                    M42OutcomeLabel(
                        label_id="outcome-" + sha256(raw_id.encode()).hexdigest()[:24],
                        setup_candidate_id=event.setup_candidate_id or event.record_id,
                        ready_at=event.available_at,
                        direction=event.direction,
                        anchor_price=anchor,
                        horizon_bars=horizon,
                        bars_observed=len(observed),
                        censored=len(observed) < horizon,
                        mfe_price=mfe,
                        mae_price=mae,
                        mfe_ticks=mfe / self.tick_size,
                        mae_ticks=mae / self.tick_size,
                        close_return_price=close_return,
                        first_target_candidate_id=target_id,
                        bars_to_first_target=bars_to_target,
                    )
                )
        return outcomes

    def _chart_queue(
        self,
        events: Sequence[M4AuditEvent],
        replay: M4ReplayResult,
        bars: Sequence[OHLCBar],
    ) -> list[M42ChartReviewItem]:
        candidates = [item for item in events if item.kind == M4EventKind.READY_PAYLOAD]
        if len(candidates) < self.max_chart_samples:
            candidates.extend(
                item
                for item in events
                if item.kind == M4EventKind.CANDIDATE
                and item.category
                in {
                    CandidateType.LIQUIDITY_EVENT.value,
                    CandidateType.SHIFT.value,
                }
            )
        source_events = {item.event_id: item for item in events}
        if len(candidates) < self.max_chart_samples:
            candidates.extend(
                source_events[item.source_event_id]
                for item in replay.near_misses
                if item.included_in_analysis and item.source_event_id in source_events
            )
        unique = {item.event_id: item for item in candidates}
        selected = _evenly_spaced(
            sorted(unique.values(), key=lambda item: item.available_at),
            self.max_chart_samples,
        )
        result: list[M42ChartReviewItem] = []
        for event in selected:
            center = _latest_closed_index(bars, event.available_at)
            window: list[OHLCBar] = []
            if center is not None:
                start = max(0, center - self.chart_bars_before + 1)
                end = min(len(bars), center + self.chart_bars_after + 1)
                window = list(bars[start:end])
            result.append(
                M42ChartReviewItem(
                    review_id="chart-review-"
                    + sha256(event.event_id.encode()).hexdigest()[:24],
                    source_event_id=event.event_id,
                    setup_candidate_id=event.setup_candidate_id,
                    event_kind=event.kind,
                    category=event.category,
                    as_of=event.available_at,
                    timeframe=event.timeframe,
                    direction=event.direction,
                    reason_codes=event.reason_codes,
                    evidence_payload=event.payload,
                    window_bars=window,
                )
            )
        return result


def _append_number(target: list[float], value: Any) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        target.append(float(value))


def _describe(values: Sequence[float]) -> DistributionStats:
    if not values:
        return DistributionStats(count=0)
    ordered = sorted(values)
    return DistributionStats(
        count=len(ordered),
        minimum=ordered[0],
        p25=_quantile(ordered, 0.25),
        median=_quantile(ordered, 0.50),
        p75=_quantile(ordered, 0.75),
        p90=_quantile(ordered, 0.90),
        p95=_quantile(ordered, 0.95),
        maximum=ordered[-1],
        mean=sum(ordered) / len(ordered),
    )


def _quantile(ordered: Sequence[float], probability: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _latest_closed_index(bars: Sequence[OHLCBar], as_of: AwareDatetime) -> int | None:
    result: int | None = None
    for index, bar in enumerate(bars):
        if bar.close_time > as_of:
            break
        result = index
    return result


def _first_target_hit(
    direction: Direction,
    targets: Sequence[dict[str, Any]],
    bars: Sequence[OHLCBar],
) -> tuple[str | None, int | None]:
    eligible = [
        item
        for item in targets
        if isinstance(item.get("price"), (int, float))
        and not item.get("already_taken", False)
    ]
    for index, bar in enumerate(bars, start=1):
        for target in eligible:
            price = float(target["price"])
            hit = (
                bar.high >= price
                if direction == Direction.BULLISH
                else bar.low <= price
            )
            if hit:
                return str(target.get("candidate_id")), index
    return None, None


def _evenly_spaced(items: Sequence[M4AuditEvent], limit: int) -> list[M4AuditEvent]:
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    indexes = {round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)}
    return [items[index] for index in sorted(indexes)]
