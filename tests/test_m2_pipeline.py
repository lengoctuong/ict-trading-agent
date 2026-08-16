from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from ict_trading_agent.config import TradingDayPolicy, build_xauusd_intraday_v0
from ict_trading_agent.detectors import (
    CandleFeatureConfig,
    CandleFeatureDetector,
    DisplacementCandidateDetector,
    LevelInteractionDetector,
    LiquidityRaidCandidateDetector,
    PriceBreakDetector,
    ReferenceLevel,
    StructureBreakCandidateDetector,
)
from ict_trading_agent.enums import (
    CandidateType,
    Direction,
    FactType,
    Session,
    Timeframe,
)
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.market import ClosedBarFeed, OHLCBar
from ict_trading_agent.pipeline import M2PrimitivePipeline
from ict_trading_agent.reducer import MarketStateReducer
from ict_trading_agent.reference_lifecycle import (
    ReferenceLifecyclePolicy,
    ReferenceLifecycleTracker,
)
from ict_trading_agent.state import TemporalContext
from ict_trading_agent.stores import CandidateStore, DuplicateRecordError, FactStore

UTC = timezone.utc
T0 = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)


def bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> OHLCBar:
    opened = T0 + timedelta(minutes=index * 5)
    return OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        open_time=opened,
        close_time=opened + timedelta(minutes=5),
        open=open_,
        high=high,
        low=low,
        close=close,
    )


def swing_reference(
    *,
    side: str,
    price: float,
    available_at: datetime = T0,
    fact_id: str | None = None,
) -> ObservableFact:
    return ObservableFact(
        fact_id=fact_id or f"swing-{side}-{price}",
        fact_type=FactType.SWING_POINT,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        occurred_at=T0 - timedelta(minutes=10),
        confirmed_at=available_at,
        available_at=available_at,
        geometry=PriceGeometry(price=price),
        metrics={"side": side, "rank": "short_term"},
        detector_name="fixture",
        detector_version="0.1.0",
    )


def baseline_and_displacement() -> tuple[list[OHLCBar], OHLCBar]:
    baseline = [
        bar(0, open_=100.0, high=101.0, low=99.0, close=100.5),
        bar(1, open_=100.5, high=101.5, low=100.0, close=101.0),
        bar(2, open_=101.0, high=102.0, low=100.5, close=101.5),
    ]
    displacement = bar(
        3,
        open_=101.5,
        high=105.0,
        low=101.4,
        close=104.8,
    )
    return baseline, displacement


def test_candle_features_and_displacement_use_prior_closed_baseline_only() -> None:
    baseline, target = baseline_and_displacement()
    feature = CandleFeatureDetector(
        CandleFeatureConfig(baseline_period=3)
    ).detect(target, baseline)
    assert feature.fact_type is FactType.CANDLE_FEATURES
    assert feature.occurred_at == target.open_time
    assert feature.available_at == target.close_time
    assert feature.metrics["average_body_baseline"] == pytest.approx(0.5)
    assert feature.metrics["mean_body"] == pytest.approx(0.5)
    assert feature.metrics["median_body"] == pytest.approx(0.5)
    assert feature.metrics["median_range"] == pytest.approx(1.5)
    assert feature.metrics["atr"] == feature.metrics["atr_baseline"]
    assert feature.metrics["body_vs_baseline"] == pytest.approx(6.6)
    assert "follow_through" not in feature.metrics

    candidate = DisplacementCandidateDetector().detect(feature)
    assert candidate is not None
    assert candidate.candidate_type is CandidateType.DISPLACEMENT
    assert candidate.direction is Direction.BULLISH
    assert candidate.available_at == target.close_time

    bearish_bar = target.model_copy(
        update={
            "open": 101.5,
            "high": 101.6,
            "low": 98.0,
            "close": 98.2,
        }
    )
    bearish_feature = CandleFeatureDetector(
        CandleFeatureConfig(baseline_period=3)
    ).detect(bearish_bar, baseline)
    bearish_candidate = DisplacementCandidateDetector().detect(bearish_feature)
    assert bearish_candidate is not None
    assert bearish_candidate.direction is Direction.BEARISH


def test_displacement_candidate_preserves_near_threshold_evidence() -> None:
    baseline, target = baseline_and_displacement()
    near_threshold = target.model_copy(
        update={
            "open": 101.5,
            "high": 106.4,
            "low": 101.4,
            "close": 104.9,
        }
    )
    feature = CandleFeatureDetector(
        CandleFeatureConfig(baseline_period=3)
    ).detect(near_threshold, baseline)
    assert feature.metrics["body_to_range"] == pytest.approx(0.68)
    candidate = DisplacementCandidateDetector().detect(feature)
    assert candidate is not None
    assert candidate.raw_features["criteria"]["body_to_range"] is False
    assert candidate.raw_features["all_thresholds_passed"] is False
    assert candidate.machine_labels == ["directional_repricing_candidate"]


def test_candle_feature_detector_rejects_gapped_or_developing_history() -> None:
    baseline, target = baseline_and_displacement()
    gapped = baseline[1].model_copy(
        update={
            "open_time": baseline[1].open_time + timedelta(minutes=1),
            "close_time": baseline[1].close_time + timedelta(minutes=1),
        }
    )
    detector = CandleFeatureDetector(CandleFeatureConfig(baseline_period=3))
    with pytest.raises(ValueError, match="contiguous"):
        detector.detect(target, [baseline[0], gapped, baseline[2]])
    with pytest.raises(ValueError, match="closed bars only"):
        detector.detect(
            target,
            [*baseline[:2], baseline[2].model_copy(update={"is_closed": False})],
        )


def test_same_bar_breach_and_reclaim_emit_bearish_raid_candidate() -> None:
    reference = ReferenceLevel.from_fact(
        swing_reference(side="high", price=105.0)
    )
    sweep_bar = bar(3, open_=104.0, high=106.0, low=103.5, close=104.5)
    interactions = LevelInteractionDetector(tick_size=0.1).detect(
        sweep_bar,
        reference,
    )
    assert [fact.fact_type for fact in interactions] == [
        FactType.LEVEL_BREACH,
        FactType.LEVEL_RECLAIM,
    ]
    raid = LiquidityRaidCandidateDetector().detect(*interactions)
    assert raid.candidate_type is CandidateType.LIQUIDITY_EVENT
    assert raid.direction is Direction.BEARISH
    assert raid.available_at == sweep_bar.close_time
    assert raid.raw_features["penetration_points"] == pytest.approx(1.0)

    touching = sweep_bar.model_copy(update={"high": 105.0})
    assert LevelInteractionDetector(tick_size=0.1).detect(touching, reference) == ()

    sell_side = ReferenceLevel.from_fact(
        swing_reference(side="low", price=100.0)
    )
    sell_side_sweep = bar(
        3,
        open_=100.5,
        high=101.0,
        low=99.0,
        close=100.4,
    )
    sell_side_facts = LevelInteractionDetector(tick_size=0.1).detect(
        sell_side_sweep,
        sell_side,
    )
    bullish_raid = LiquidityRaidCandidateDetector().detect(*sell_side_facts)
    assert bullish_raid.direction is Direction.BULLISH


def test_reference_must_exist_before_the_interacting_bar_opens() -> None:
    target = bar(3, open_=104.0, high=106.0, low=103.5, close=104.5)
    reference = ReferenceLevel.from_fact(
        swing_reference(
            side="high",
            price=105.0,
            available_at=target.close_time,
        )
    )
    with pytest.raises(ValueError, match="before the bar opens"):
        LevelInteractionDetector(tick_size=0.1).detect(target, reference)


def test_close_through_confirmed_swing_is_unclassified_structure_candidate() -> None:
    reference = ReferenceLevel.from_fact(
        swing_reference(side="high", price=105.0)
    )
    break_bar = bar(3, open_=104.0, high=107.0, low=103.5, close=106.0)
    price_break = PriceBreakDetector(tick_size=0.1).detect(break_bar, reference)
    assert price_break is not None
    assert price_break.fact_type is FactType.PRICE_BREAK
    assert price_break.direction is Direction.BULLISH
    candidate = StructureBreakCandidateDetector().detect(price_break)
    assert candidate.candidate_type is CandidateType.STRUCTURE_BREAK
    assert candidate.raw_features["structure_break_type"] == "unclassified"
    assert "unclassified_structure_break" in candidate.machine_labels

    equal_close = break_bar.model_copy(update={"close": 105.0})
    assert PriceBreakDetector(tick_size=0.1).detect(equal_close, reference) is None


def test_m2_pipeline_appends_only_outputs_available_at_closed_bar() -> None:
    feed = ClosedBarFeed("XAUUSD")
    bars = [
        bar(0, open_=100.0, high=101.0, low=99.0, close=100.0),
        bar(1, open_=100.0, high=101.0, low=99.5, close=100.5),
        bar(2, open_=100.5, high=101.5, low=100.0, close=101.0),
        bar(3, open_=101.0, high=106.0, low=100.5, close=104.5),
    ]
    for item in bars:
        feed.append(item, observed_at=item.close_time)

    facts = FactStore()
    facts.append(swing_reference(side="high", price=105.0))
    candidates = CandidateStore()
    pipeline = M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    )
    batch = pipeline.process_latest(
        timeframe=Timeframe.M5,
        as_of=bars[-1].close_time,
    )
    assert all(fact.available_at <= bars[-1].close_time for fact in batch.facts)
    assert all(
        candidate.available_at <= bars[-1].close_time
        for candidate in batch.candidates
    )
    raids = [
        candidate
        for candidate in batch.candidates
        if candidate.candidate_type == CandidateType.LIQUIDITY_EVENT
    ]
    assert len(raids) == 1
    assert facts.visible(as_of=bars[-1].close_time - timedelta(seconds=1)) == (
        swing_reference(side="high", price=105.0),
    )
    assert raids[0] in candidates.visible(as_of=bars[-1].close_time)

    state = MarketStateReducer(
        profile=build_xauusd_intraday_v0(
            TradingDayPolicy(
                timezone="America/New_York",
                rollover_local_time=time(17, 0),
            )
        ),
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
    ).reduce(
        symbol="XAUUSD",
        as_of=bars[-1].close_time,
        temporal=TemporalContext(
            trading_day="2026-08-15",
            session=Session.ASIA,
            ny_time=bars[-1].close_time,
        ),
    )
    assert raids[0].candidate_id in state.visible_candidate_ids
    assert (
        raids[0].candidate_id
        in state.timeframes[Timeframe.M5].active_liquidity_candidate_ids
    )

    with pytest.raises(DuplicateRecordError):
        pipeline.process_latest(
            timeframe=Timeframe.M5,
            as_of=bars[-1].close_time,
        )


def test_reference_lifecycle_prevents_repeated_raids_by_default() -> None:
    feed = ClosedBarFeed("XAUUSD")
    bars = [
        bar(0, open_=100.0, high=101.0, low=99.0, close=100.0),
        bar(1, open_=100.0, high=101.0, low=99.5, close=100.5),
        bar(2, open_=100.5, high=101.5, low=100.0, close=101.0),
        bar(3, open_=101.0, high=106.0, low=100.5, close=104.5),
        bar(4, open_=104.5, high=107.0, low=104.0, close=104.8),
    ]
    for item in bars:
        feed.append(item, observed_at=item.close_time)

    reference = swing_reference(
        side="high",
        price=105.0,
        fact_id="single-use-reference",
    )
    facts = FactStore()
    facts.append(reference)
    candidates = CandidateStore()
    pipeline = M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    )
    batches = pipeline.process_range(
        timeframe=Timeframe.M5,
        start_after=None,
        as_of=bars[-1].close_time,
    )
    assert [batch.processed_bar_open_at for batch in batches] == [
        bars[3].open_time,
        bars[4].open_time,
    ]
    raids_for_reference = [
        candidate
        for candidate in candidates.visible(as_of=bars[-1].close_time)
        if candidate.candidate_type == CandidateType.LIQUIDITY_EVENT
        and candidate.raw_features.get("reference_fact_id") == reference.fact_id
    ]
    assert len(raids_for_reference) == 1
    lifecycle_facts = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.REFERENCE_STATE
        and fact.metrics.get("reference_fact_id") == reference.fact_id
    ]
    assert len(lifecycle_facts) == 1
    assert lifecycle_facts[0].metrics["status"] == "taken"

    state = MarketStateReducer(
        profile=build_xauusd_intraday_v0(
            TradingDayPolicy(
                timezone="America/New_York",
                rollover_local_time=time(17, 0),
            )
        ),
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
    ).reduce(
        symbol="XAUUSD",
        as_of=bars[-1].close_time,
        temporal=TemporalContext(
            trading_day="2026-08-15",
            session=Session.ASIA,
            ny_time=bars[-1].close_time,
        ),
    )
    assert reference.fact_id not in state.timeframes[
        Timeframe.M5
    ].active_swing_fact_ids


def test_reference_reuse_requires_explicit_policy() -> None:
    reference_fact = swing_reference(side="high", price=105.0)
    reference = ReferenceLevel.from_fact(reference_fact)
    interacting_bar = bar(3, open_=104.0, high=106.0, low=103.5, close=104.5)
    breach = LevelInteractionDetector(tick_size=0.1).detect(
        interacting_bar,
        reference,
    )[0]
    default_tracker = ReferenceLifecycleTracker()
    taken = default_tracker.taken_observation(reference, breach)
    history = [reference_fact, breach, taken]
    assert default_tracker.is_eligible(
        reference.reference_fact_id,
        history,
        as_of=interacting_bar.close_time,
    ) is False

    reuse_tracker = ReferenceLifecycleTracker(
        ReferenceLifecyclePolicy(reuse_taken_levels=True)
    )
    assert reuse_tracker.is_eligible(
        reference.reference_fact_id,
        history,
        as_of=interacting_bar.close_time,
    ) is True


def test_catch_up_processes_every_unseen_bar_once() -> None:
    feed = ClosedBarFeed("XAUUSD")
    bars = [
        bar(
            index,
            open_=100.0 + index * 0.1,
            high=101.0 + index * 0.1,
            low=99.0 + index * 0.1,
            close=100.2 + index * 0.1,
        )
        for index in range(6)
    ]
    for item in bars:
        feed.append(item, observed_at=item.close_time)
    pipeline = M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=FactStore(),
        candidate_store=CandidateStore(),
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    )

    initial = pipeline.process_range(
        timeframe=Timeframe.M5,
        start_after=None,
        as_of=bars[4].close_time,
    )
    assert [batch.processed_bar_open_at for batch in initial] == [
        bars[3].open_time,
        bars[4].open_time,
    ]
    resumed = pipeline.catch_up(
        timeframe=Timeframe.M5,
        as_of=bars[5].close_time,
    )
    assert [batch.processed_bar_open_at for batch in resumed] == [
        bars[5].open_time
    ]
    assert pipeline.catch_up(
        timeframe=Timeframe.M5,
        as_of=bars[5].close_time,
    ) == ()

    sequential = M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=FactStore(),
        candidate_store=CandidateStore(),
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    )
    for index in range(3, 6):
        sequential.process_latest(
            timeframe=Timeframe.M5,
            as_of=bars[index].close_time,
        )
    assert set(pipeline.fact_store.as_mapping()) == set(
        sequential.fact_store.as_mapping()
    )
    assert set(pipeline.candidate_store.as_mapping()) == set(
        sequential.candidate_store.as_mapping()
    )
