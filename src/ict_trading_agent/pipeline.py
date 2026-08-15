from __future__ import annotations

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
from .market import ClosedBarFeed
from .stores import CandidateStore, DuplicateRecordError, FactStore


class M2DetectionBatch(SchemaModel):
    symbol: NonEmptyStr
    timeframe: Timeframe
    as_of: AwareDatetime
    processed_bar_open_at: AwareDatetime
    facts: list[ObservableFact] = Field(default_factory=list)
    candidates: list[ConceptCandidate] = Field(default_factory=list)


class M2PrimitivePipeline:
    """Closed-bar M2 vertical slice with append-only point-in-time outputs."""

    def __init__(
        self,
        *,
        bar_feed: ClosedBarFeed,
        fact_store: FactStore,
        candidate_store: CandidateStore,
        tick_size: float,
        candle_config: CandleFeatureConfig | None = None,
        displacement_thresholds: DisplacementThresholds | None = None,
    ) -> None:
        self.bar_feed = bar_feed
        self.fact_store = fact_store
        self.candidate_store = candidate_store
        self.candle_features = CandleFeatureDetector(candle_config)
        self.displacement = DisplacementCandidateDetector(displacement_thresholds)
        self.swings = ThreeBarSwingDetector(tick_size=tick_size)
        self.fvgs = FVGGeometryDetector(tick_size=tick_size)
        self.level_interactions = LevelInteractionDetector(tick_size=tick_size)
        self.liquidity_raids = LiquidityRaidCandidateDetector()
        self.price_breaks = PriceBreakDetector(tick_size=tick_size)
        self.structure_breaks = StructureBreakCandidateDetector()

    def process_latest(
        self,
        *,
        timeframe: Timeframe,
        as_of: AwareDatetime,
    ) -> M2DetectionBatch:
        bars = self.bar_feed.bars(timeframe, as_of=as_of)
        baseline_period = self.candle_features.config.baseline_period
        if len(bars) < max(3, baseline_period + 1):
            raise ValueError("insufficient closed bars for the M2 pipeline")

        bar = bars[-1]
        baseline = bars[-(baseline_period + 1) : -1]
        facts: list[ObservableFact] = []
        candidates: list[ConceptCandidate] = []

        facts.extend(self.swings.detect_triplet(*bars[-3:]))
        facts.extend(self.fvgs.detect_triplet(*bars[-3:]))
        candle_fact = self.candle_features.detect(bar, baseline)
        facts.append(candle_fact)
        displacement = self.displacement.detect(candle_fact)
        if displacement is not None:
            candidates.append(displacement)

        reference_facts = [
            fact
            for fact in self.fact_store.visible(
                as_of=bar.open_time,
                symbol=bar.symbol,
            )
            if fact.fact_type
            in {
                FactType.SWING_POINT,
                FactType.SESSION_LEVEL,
                FactType.PREVIOUS_DAY_LEVEL,
            }
        ]
        for reference_fact in reference_facts:
            reference = ReferenceLevel.from_fact(reference_fact)
            interactions = self.level_interactions.detect(bar, reference)
            facts.extend(interactions)
            if len(interactions) == 2:
                candidates.append(
                    self.liquidity_raids.detect(interactions[0], interactions[1])
                )
            if reference.fact_type == FactType.SWING_POINT:
                price_break = self.price_breaks.detect(bar, reference)
                if price_break is not None:
                    facts.append(price_break)
                    candidates.append(self.structure_breaks.detect(price_break))

        self._preflight_append(facts, candidates)
        self.fact_store.extend(facts)
        self.candidate_store.extend(candidates)
        return M2DetectionBatch(
            symbol=bar.symbol,
            timeframe=timeframe,
            as_of=as_of,
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
