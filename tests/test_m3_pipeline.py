from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ict_trading_agent.candidates import TargetCandidate
from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import (
    CandidateType,
    FactType,
    SetupStatus,
    SwingRank,
    TargetScope,
    TargetSide,
    TargetType,
    Timeframe,
)
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.m3 import M3Policy, M3SetupPipeline
from ict_trading_agent.market import ClosedBarFeed, OHLCBar
from ict_trading_agent.pipeline import M2PrimitivePipeline
from ict_trading_agent.stores import CandidateStore, FactStore, SetupStore
from ict_trading_agent.structure_lifecycle import StructureLifecycleTracker
from ict_trading_agent.swing_hierarchy import SwingHierarchyPromoter

UTC = timezone.utc
T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


def bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> OHLCBar:
    opened = T0 + timedelta(minutes=5 * index)
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


def reference_fact(
    *,
    fact_id: str,
    fact_type: FactType,
    timeframe: Timeframe,
    side: str,
    price: float,
) -> ObservableFact:
    return ObservableFact(
        fact_id=fact_id,
        fact_type=fact_type,
        symbol="XAUUSD",
        timeframe=timeframe,
        occurred_at=T0 - timedelta(hours=2),
        confirmed_at=T0 - timedelta(hours=1),
        available_at=T0 - timedelta(hours=1),
        geometry=PriceGeometry(price=price),
        metrics={"side": side, "rank": SwingRank.SHORT_TERM.value},
        detector_name="fixture",
        detector_version="0.1.0",
    )


def build_pipelines(
    bars: list[OHLCBar],
    *,
    policy: M3Policy | None = None,
    include_structure: bool = True,
) -> tuple[
    M2PrimitivePipeline,
    M3SetupPipeline,
    FactStore,
    CandidateStore,
    SetupStore,
]:
    feed = ClosedBarFeed("XAUUSD")
    for item in bars:
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.append(
        reference_fact(
            fact_id="pdl-100",
            fact_type=FactType.PREVIOUS_DAY_LEVEL,
            timeframe=Timeframe.D1,
            side="low",
            price=100.0,
        )
    )
    if include_structure:
        facts.append(
            reference_fact(
                fact_id="m5-swing-high-104",
                fact_type=FactType.SWING_POINT,
                timeframe=Timeframe.M5,
                side="high",
                price=104.0,
            )
        )
    candidates = CandidateStore()
    setups = SetupStore()
    m2 = M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    )
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
        policy=policy,
        target_candidates=[
            TargetCandidate(
                candidate_id="target-pdh-110",
                symbol="XAUUSD",
                price=110.0,
                side=TargetSide.UPSIDE,
                target_type=TargetType.PREVIOUS_DAY_HIGH,
                scope=TargetScope.INTRADAY,
                source_timeframe=Timeframe.D1,
                available_at=T0 - timedelta(hours=1),
            )
        ],
        context={"data_source": "Exness", "candle_timezone": "UTC"},
    )
    return m2, m3, facts, candidates, setups


def run_sequence(
    bars: list[OHLCBar],
    *,
    policy: M3Policy | None = None,
    include_structure: bool = True,
) -> tuple[list, FactStore, CandidateStore, SetupStore]:
    m2, m3, facts, candidates, setups = build_pipelines(
        bars,
        policy=policy,
        include_structure=include_structure,
    )
    batches = []
    for index in range(3, len(bars)):
        m2.process_latest(timeframe=Timeframe.M5, as_of=bars[index].close_time)
        batches.append(
            m3.process_latest(timeframe=Timeframe.M5, as_of=bars[index].close_time)
        )
    return batches, facts, candidates, setups


def ready_bars() -> list[OHLCBar]:
    return [
        bar(0, open_=101.0, high=102.0, low=100.5, close=101.2),
        bar(1, open_=101.2, high=102.0, low=100.8, close=101.3),
        bar(2, open_=101.3, high=102.0, low=100.9, close=101.4),
        # Canonical sell-side sweep/reclaim -> bullish raid.
        bar(3, open_=101.4, high=102.0, low=99.0, close=101.0),
        # Same-TF close-through + directional repricing candle.
        bar(4, open_=101.0, high=105.5, low=100.8, close=105.0),
        # Confirms bullish FVG made by bar 4: [102.0, 103.0].
        bar(5, open_=105.0, high=106.0, low=103.0, close=105.5),
        # Retraces into the FVG and closes favorably above it.
        bar(6, open_=104.5, high=105.0, low=102.5, close=104.5),
    ]


def test_full_m3_sequence_reaches_ready_for_llm_with_traceable_evidence() -> None:
    bars = ready_bars()
    batches, facts, candidates, setups = run_sequence(bars)
    current = setups.visible(as_of=bars[-1].close_time)
    assert len(current) == 1
    setup = current[0]
    assert setup.status is SetupStatus.READY_FOR_LLM
    assert setup.hard_invalidation_price == 99.0
    assert len(setup.entry_zone_candidate_ids) == 1

    transition_statuses = [
        event.to_status for event in setups.transitions(setup.setup_candidate_id)
    ]
    assert transition_statuses == [
        SetupStatus.FORMING,
        SetupStatus.FORMING,
        SetupStatus.READY_FOR_LLM,
    ]
    assert len(batches[-1].ready_for_llm) == 1
    payload = batches[-1].ready_for_llm[0]
    assert payload.setup.setup_candidate_id == setup.setup_candidate_id
    assert payload.context["data_source"] == "Exness"
    assert [target.candidate_id for target in payload.targets] == ["target-pdh-110"]
    assert setup.target_candidate_ids == ["target-pdh-110"]
    assert {fact.fact_id for fact in payload.facts} >= set(setup.evidence_fact_ids)
    assert {candidate.candidate_id for candidate in payload.candidates} >= set(
        setup.evidence_candidate_ids
    )
    assert CandidateType.DISPLACEMENT in {
        candidate.candidate_type for candidate in payload.candidates
    }
    reactions = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.FVG_REACTION
    ]
    assert len(reactions) == 1
    assert reactions[0].metrics == {
        "setup_candidate_id": setup.setup_candidate_id,
        "entry_zone_candidate_id": setup.entry_zone_candidate_ids[0],
        "touched": True,
        "penetration_fraction": 0.5,
        "favorable_close_outside": True,
        "close_price": 104.5,
        "lifecycle": "reacted",
    }
    assert any(
        candidate.candidate_type == CandidateType.SHIFT
        for candidate in candidates.visible(as_of=bars[-1].close_time)
    )


def test_multi_bar_reclaim_within_three_bars_starts_permissive_setup() -> None:
    bars = [
        bar(0, open_=101.0, high=102.0, low=100.5, close=101.2),
        bar(1, open_=101.2, high=102.0, low=100.8, close=101.3),
        bar(2, open_=101.3, high=102.0, low=100.9, close=101.4),
        bar(3, open_=101.4, high=101.5, low=99.0, close=99.5),
        bar(4, open_=99.5, high=100.0, low=98.5, close=99.8),
        bar(5, open_=99.8, high=101.0, low=99.0, close=100.5),
    ]
    _, facts, candidates, setups = run_sequence(bars, include_structure=False)
    raids = [
        candidate
        for candidate in candidates.visible(as_of=bars[-1].close_time)
        if candidate.candidate_type == CandidateType.LIQUIDITY_EVENT
    ]
    assert len(raids) == 1
    assert raids[0].raw_features["same_bar_reclaim"] is False
    assert raids[0].raw_features["reclaim_span_bars"] == 2
    assert "permissive_multi_bar_sweep_candidate" in raids[0].machine_labels
    assert len(setups.visible(as_of=bars[-1].close_time)) == 1
    reclaims = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.LEVEL_RECLAIM
    ]
    assert reclaims[0].metrics["promotion_eligible"] is True


def test_late_reclaim_is_logged_but_not_promoted_to_setup() -> None:
    bars = [
        bar(0, open_=101.0, high=102.0, low=100.5, close=101.2),
        bar(1, open_=101.2, high=102.0, low=100.8, close=101.3),
        bar(2, open_=101.3, high=102.0, low=100.9, close=101.4),
        bar(3, open_=101.4, high=101.5, low=99.0, close=99.5),
        bar(4, open_=99.5, high=100.0, low=98.8, close=99.6),
        bar(5, open_=99.6, high=100.0, low=98.7, close=99.7),
        bar(6, open_=99.7, high=100.0, low=98.6, close=99.8),
        bar(7, open_=99.8, high=101.0, low=99.0, close=100.5),
    ]
    _, facts, candidates, setups = run_sequence(bars, include_structure=False)
    reclaims = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.LEVEL_RECLAIM
    ]
    assert len(reclaims) == 1
    assert reclaims[0].metrics["reclaim_span_bars"] == 4
    assert reclaims[0].metrics["promotion_eligible"] is False
    assert reclaims[0].metrics["reason_code"] == "RECLAIM_OUTSIDE_WINDOW"
    assert not [
        candidate
        for candidate in candidates.visible(as_of=bars[-1].close_time)
        if candidate.candidate_type == CandidateType.LIQUIDITY_EVENT
    ]
    assert setups.visible(as_of=bars[-1].close_time) == ()


def test_setup_invalidates_on_one_setup_tf_close_beyond_raid_extreme() -> None:
    bars = ready_bars()[:4] + [bar(4, open_=101.0, high=101.2, low=98.0, close=98.5)]
    _, _, _, setups = run_sequence(bars)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    assert setup.status is SetupStatus.INVALIDATED
    event = setups.transitions(setup.setup_candidate_id)[0]
    assert event.reason_codes == ["SETUP_TF_CLOSE_BEYOND_RAID_EXTREME"]
    assert event.metrics["consecutive_closes"] == 1
    assert event.metrics["distance_buffer"] == 0.0


def test_raid_expires_when_no_shift_arrives_inside_configured_window() -> None:
    policy = M3Policy(
        shift_window_bars={Timeframe.M5: 1, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 24, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    bars = ready_bars()[:4] + [bar(4, open_=101.0, high=103.0, low=100.5, close=102.0)]
    _, _, _, setups = run_sequence(bars, policy=policy)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    assert setup.status is SetupStatus.EXPIRED
    assert setups.transitions(setup.setup_candidate_id)[0].reason_codes == [
        "SHIFT_WINDOW_EXPIRED"
    ]


def test_fvg_touch_only_is_logged_then_entry_opportunity_expires() -> None:
    policy = M3Policy(
        shift_window_bars={Timeframe.M5: 12, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 2, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    bars = ready_bars()[:6] + [
        # Touch/penetrate, but close inside rather than favorably above the FVG.
        bar(6, open_=104.5, high=105.0, low=102.5, close=102.8),
        # Second eligible bar completes the configured expiry budget.
        bar(7, open_=103.2, high=104.0, low=103.2, close=103.5),
    ]
    batches, facts, _, setups = run_sequence(bars, policy=policy)
    setup_at_touch = setups.visible(as_of=bars[6].close_time)[0]
    assert setup_at_touch.status is SetupStatus.FORMING
    touch_facts = [
        fact for fact in batches[3].facts if fact.fact_type == FactType.FVG_REACTION
    ]
    assert len(touch_facts) == 1
    assert touch_facts[0].metrics["touched"] is True
    assert touch_facts[0].metrics["favorable_close_outside"] is False
    assert touch_facts[0].metrics["lifecycle"] == "touched"

    final = setups.visible(as_of=bars[-1].close_time)[0]
    assert final.status is SetupStatus.EXPIRED
    assert setups.transitions(final.setup_candidate_id)[-1].reason_codes == [
        "FVG_RETRACE_WINDOW_EXPIRED"
    ]
    assert (
        len(
            [
                fact
                for fact in facts.visible(as_of=bars[-1].close_time)
                if fact.fact_type == FactType.FVG_REACTION
            ]
        )
        == 1
    )


def test_cross_tf_close_is_raw_interaction_not_structure_eligible() -> None:
    bars = ready_bars()[:3] + [bar(3, open_=104.0, high=106.0, low=103.5, close=105.5)]
    feed = ClosedBarFeed("XAUUSD")
    for item in bars:
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.append(
        reference_fact(
            fact_id="h1-swing-high-105",
            fact_type=FactType.SWING_POINT,
            timeframe=Timeframe.H1,
            side="high",
            price=105.0,
        )
    )
    candidates = CandidateStore()
    M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    ).process_latest(timeframe=Timeframe.M5, as_of=bars[-1].close_time)
    structure = [
        candidate
        for candidate in candidates.visible(as_of=bars[-1].close_time)
        if candidate.candidate_type == CandidateType.STRUCTURE_BREAK
    ]
    assert len(structure) == 1
    assert structure[0].raw_features["detection_timeframe"] == "M5"
    assert structure[0].raw_features["reference_timeframe"] == "H1"
    assert structure[0].raw_features["same_timeframe_structure_eligible"] is False
    assert "cross_timeframe_reference_interaction" in structure[0].machine_labels
    assert not [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.STRUCTURE_STATE
    ]


def test_swing_hierarchy_promotions_are_append_only() -> None:
    swings: list[ObservableFact] = []
    for index, price in enumerate((101.0, 105.0, 102.0)):
        occurred = T0 + timedelta(minutes=5 * index)
        swings.append(
            ObservableFact(
                fact_id=f"swing-{index}",
                fact_type=FactType.SWING_POINT,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                occurred_at=occurred,
                confirmed_at=occurred + timedelta(minutes=5),
                available_at=occurred + timedelta(minutes=5),
                geometry=PriceGeometry(price=price),
                metrics={"side": "high", "rank": SwingRank.SHORT_TERM.value},
                detector_name="fixture",
                detector_version="0.1.0",
            )
        )
    promoter = SwingHierarchyPromoter()
    promotions = promoter.detect(swings)
    assert len(promotions) == 1
    assert promotions[0].fact_type is FactType.SWING_PROMOTION
    assert promotions[0].metrics["rank"] == SwingRank.INTERMEDIATE.value
    assert promotions[0].metrics["promoted_swing_fact_id"] == "swing-1"
    assert promoter.detect([*swings, *promotions]) == ()
    assert all(fact.metrics["rank"] == SwingRank.SHORT_TERM.value for fact in swings)


def test_structure_supersession_is_an_explicit_append_only_observation() -> None:
    older = reference_fact(
        fact_id="older-high",
        fact_type=FactType.SWING_POINT,
        timeframe=Timeframe.M15,
        side="high",
        price=104.0,
    )
    newer = older.model_copy(
        update={
            "fact_id": "newer-high",
            "occurred_at": T0,
            "confirmed_at": T0 + timedelta(minutes=15),
            "available_at": T0 + timedelta(minutes=15),
            "geometry": PriceGeometry(price=105.0),
        }
    )
    tracker = StructureLifecycleTracker()
    event = tracker.superseded_observation(older, newer)
    assert event.fact_type is FactType.STRUCTURE_STATE
    assert event.metrics["status"] == "superseded"
    assert (
        tracker.is_eligible(
            older.fact_id,
            [older, newer, event],
            as_of=event.available_at,
        )
        is False
    )


def test_m3_range_replay_and_catch_up_share_the_realtime_path() -> None:
    bars = ready_bars()
    m2, m3, _, _, setups = build_pipelines(bars)
    m2.process_range(
        timeframe=Timeframe.M5,
        start_after=None,
        as_of=bars[-1].close_time,
    )
    replay = m3.process_range(
        timeframe=Timeframe.M5,
        start_after=None,
        as_of=bars[-1].close_time,
    )
    assert [batch.processed_bar_open_at for batch in replay] == [
        item.open_time for item in bars[3:]
    ]
    assert replay[-1].ready_for_llm
    assert m3.catch_up(timeframe=Timeframe.M5, as_of=bars[-1].close_time) == ()

    sequential_batches, _, _, sequential_setups = run_sequence(bars)
    assert sequential_batches[-1].ready_for_llm
    replay_origins, replay_transitions = setups.as_mappings()
    sequential_origins, sequential_transitions = sequential_setups.as_mappings()
    assert set(replay_origins) == set(sequential_origins)
    assert set(replay_transitions) == set(sequential_transitions)
