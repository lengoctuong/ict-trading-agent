from __future__ import annotations

from collections.abc import Sequence

from pydantic import AwareDatetime, Field

from .base import NonEmptyStr, SchemaModel
from .candidates import ConceptCandidate
from .detectors import (
    CandleFeatureConfig,
    CandleFeatureDetector,
    DisplacementCandidateDetector,
    DisplacementThresholds,
    FVGGeometryDetector,
    LevelInteractionDetector,
    LiquidityRaidCandidateDetector,
    PriceBreakDetector,
    ReferenceLevel,
    StructureBreakCandidateDetector,
    ThreeBarSwingDetector,
)
from .enums import FactType, Timeframe
from .facts import ObservableFact
from .market import BarAdjacencyPolicy, ClosedBarFeed, OHLCBar
from .reference_lifecycle import (
    ReferenceLifecyclePolicy,
    ReferenceLifecycleTracker,
)
from .stores import CandidateStore, DuplicateRecordError, FactStore
from .structure_lifecycle import StructureLifecycleTracker
from .swing_hierarchy import SwingHierarchyPromoter, effective_swing_rank


class M2DetectionBatch(SchemaModel):
    symbol: NonEmptyStr
    timeframe: Timeframe
    as_of: AwareDatetime
    processed_bar_open_at: AwareDatetime
    facts: list[ObservableFact] = Field(default_factory=list)
    candidates: list[ConceptCandidate] = Field(default_factory=list)


class M2PrimitivePipeline:
    """Closed-bar M2 vertical slice with append-only point-in-time outputs."""

    version = "0.1.0"

    def __init__(
        self,
        *,
        bar_feed: ClosedBarFeed,
        fact_store: FactStore,
        candidate_store: CandidateStore,
        tick_size: float,
        candle_config: CandleFeatureConfig | None = None,
        displacement_thresholds: DisplacementThresholds | None = None,
        adjacency_policy: BarAdjacencyPolicy | None = None,
        reference_lifecycle_policy: ReferenceLifecyclePolicy | None = None,
    ) -> None:
        self.bar_feed = bar_feed
        self.fact_store = fact_store
        self.candidate_store = candidate_store
        self.candle_features = CandleFeatureDetector(
            candle_config,
            adjacency_policy=adjacency_policy,
        )
        self.displacement = DisplacementCandidateDetector(displacement_thresholds)
        self.swings = ThreeBarSwingDetector(
            tick_size=tick_size,
            adjacency_policy=adjacency_policy,
        )
        self.fvgs = FVGGeometryDetector(
            tick_size=tick_size,
            adjacency_policy=adjacency_policy,
        )
        self.level_interactions = LevelInteractionDetector(tick_size=tick_size)
        self.liquidity_raids = LiquidityRaidCandidateDetector()
        self.price_breaks = PriceBreakDetector(tick_size=tick_size)
        self.structure_breaks = StructureBreakCandidateDetector()
        self.reference_lifecycle = ReferenceLifecycleTracker(reference_lifecycle_policy)
        self.structure_lifecycle = StructureLifecycleTracker()
        self.swing_hierarchy = SwingHierarchyPromoter()

    def process_latest(
        self,
        *,
        timeframe: Timeframe,
        as_of: AwareDatetime,
    ) -> M2DetectionBatch:
        bars = self.bar_feed.bars(timeframe, as_of=as_of)
        minimum_index = self._minimum_processable_index()
        if len(bars) <= minimum_index:
            raise ValueError("insufficient closed bars for the M2 pipeline")
        return self._process_index(bars, len(bars) - 1)

    def process_range(
        self,
        *,
        timeframe: Timeframe,
        start_after: AwareDatetime | None,
        as_of: AwareDatetime,
    ) -> tuple[M2DetectionBatch, ...]:
        """Process every eligible bar in order using the realtime code path."""

        bars = self.bar_feed.bars(timeframe, as_of=as_of)
        batches: list[M2DetectionBatch] = []
        for index in range(self._minimum_processable_index(), len(bars)):
            if start_after is not None and bars[index].open_time <= start_after:
                continue
            batches.append(self._process_index(bars, index))
        return tuple(batches)

    def catch_up(
        self,
        *,
        timeframe: Timeframe,
        as_of: AwareDatetime,
    ) -> tuple[M2DetectionBatch, ...]:
        """Resume after the last persisted candle-feature cursor."""

        processed = self.fact_store.visible(
            as_of=as_of,
            symbol=self.bar_feed.symbol,
            timeframe=timeframe,
            fact_type=FactType.CANDLE_FEATURES,
        )
        last_processed = max(
            (fact.occurred_at for fact in processed),
            default=None,
        )
        return self.process_range(
            timeframe=timeframe,
            start_after=last_processed,
            as_of=as_of,
        )

    def _minimum_processable_index(self) -> int:
        return max(2, self.candle_features.config.baseline_period)

    def _process_index(
        self,
        bars: Sequence[OHLCBar],
        index: int,
    ) -> M2DetectionBatch:
        baseline_period = self.candle_features.config.baseline_period
        if index < self._minimum_processable_index():
            raise ValueError("bar does not have enough causal history")

        bar = bars[index]
        baseline = bars[index - baseline_period : index]
        facts: list[ObservableFact] = []
        candidates: list[ConceptCandidate] = []

        facts.extend(self.swings.detect_triplet(*bars[index - 2 : index + 1]))
        facts.extend(self.fvgs.detect_triplet(*bars[index - 2 : index + 1]))
        candle_fact = self.candle_features.detect(bar, baseline)
        facts.append(candle_fact)
        displacement = self.displacement.detect(candle_fact)
        if displacement is not None:
            candidates.append(displacement)

        visible_at_open = self.fact_store.visible(
            as_of=bar.open_time,
            symbol=bar.symbol,
        )
        facts.extend(self.swing_hierarchy.detect([*visible_at_open, *facts]))
        reference_facts = [
            fact
            for fact in visible_at_open
            if fact.fact_type
            in {
                FactType.SWING_POINT,
                FactType.SESSION_LEVEL,
                FactType.PREVIOUS_DAY_LEVEL,
            }
        ]
        for reference_fact in reference_facts:
            reference = ReferenceLevel.from_fact(reference_fact)
            liquidity_eligible = self.reference_lifecycle.is_eligible(
                reference.reference_fact_id,
                self.fact_store.visible(as_of=bar.close_time, symbol=bar.symbol),
                as_of=bar.close_time,
            )
            if liquidity_eligible:
                interactions = self.level_interactions.detect(bar, reference)
                facts.extend(interactions)
                if interactions:
                    facts.append(
                        self.reference_lifecycle.taken_observation(
                            reference,
                            interactions[0],
                        )
                    )
                if len(interactions) == 2:
                    candidates.append(
                        self.liquidity_raids.detect(
                            interactions[0],
                            interactions[1],
                        )
                    )
            if (
                reference.fact_type == FactType.SWING_POINT
                and self.structure_lifecycle.is_eligible(
                    reference.reference_fact_id,
                    visible_at_open,
                    as_of=bar.open_time,
                )
            ):
                price_break = self.price_breaks.detect(bar, reference)
                if price_break is not None:
                    rank = effective_swing_rank(
                        reference.reference_fact_id,
                        [*visible_at_open, *facts],
                        as_of=price_break.available_at,
                    )
                    price_break = price_break.model_copy(
                        update={
                            "metrics": price_break.metrics
                            | {"effective_rank_as_of_break": rank.value}
                        },
                        deep=True,
                    )
                    facts.append(price_break)
                    candidates.append(self.structure_breaks.detect(price_break))
                    if price_break.metrics["same_timeframe_structure_eligible"]:
                        facts.append(
                            self.structure_lifecycle.broken_observation(
                                reference_fact,
                                price_break,
                            )
                        )

        self._preflight_append(facts, candidates)
        self.fact_store.extend(facts)
        self.candidate_store.extend(candidates)
        return M2DetectionBatch(
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            as_of=bar.close_time,
            processed_bar_open_at=bar.open_time,
            facts=facts,
            candidates=candidates,
        )

    def _preflight_append(
        self,
        facts: list[ObservableFact],
        candidates: list[ConceptCandidate],
    ) -> None:
        fact_ids = [fact.fact_id for fact in facts]
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if len(fact_ids) != len(set(fact_ids)):
            raise DuplicateRecordError("M2 batch contains duplicate fact IDs")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise DuplicateRecordError("M2 batch contains duplicate candidate IDs")
        duplicate_facts = set(fact_ids) & set(self.fact_store.as_mapping())
        duplicate_candidates = set(candidate_ids) & set(
            self.candidate_store.as_mapping()
        )
        if duplicate_facts:
            raise DuplicateRecordError(
                f"facts already stored: {sorted(duplicate_facts)}"
            )
        if duplicate_candidates:
            raise DuplicateRecordError(
                f"candidates already stored: {sorted(duplicate_candidates)}"
            )
