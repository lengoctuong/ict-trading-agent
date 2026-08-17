from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_trading_agent.candidates import (
    ConceptCandidate,
    SetupCandidate,
    TargetCandidate,
)
from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import (
    CandidateType,
    Direction,
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


def candle_fact(item: OHLCBar) -> ObservableFact:
    return ObservableFact(
        fact_id=f"candle-{item.timeframe.value}-{item.open_time.isoformat()}",
        fact_type=FactType.CANDLE_FEATURES,
        symbol=item.symbol,
        timeframe=item.timeframe,
        occurred_at=item.open_time,
        confirmed_at=item.close_time,
        available_at=item.close_time,
        metrics={},
        detector_name="fixture",
        detector_version="0.1.0",
    )


def raid_candidate(item: OHLCBar) -> ConceptCandidate:
    return ConceptCandidate(
        candidate_id=f"raid-{item.timeframe.value}-{item.open_time.isoformat()}",
        candidate_type=CandidateType.LIQUIDITY_EVENT,
        symbol=item.symbol,
        timeframe=item.timeframe,
        direction=Direction.BULLISH,
        occurred_at=item.open_time,
        available_at=item.close_time,
        evidence_fact_ids=["pdl-100"],
        raw_features={
            "reference_fact_id": "pdl-100",
            "reference_timeframe": Timeframe.H1.value,
            "reference_price": 100.0,
            "reference_side": "sell_side",
            "extreme": item.low,
            "same_bar_reclaim": True,
            "reclaim_span_bars": 0,
        },
        machine_labels=["canonical_same_bar_sweep_candidate"],
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
    if policy is None:
        policy = M3Policy(setup_timeframes=(Timeframe.M5,))
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


def test_same_time_evidence_links_are_distinct_without_self_transitions() -> None:
    feed = ClosedBarFeed("XAUUSD")
    setups = SetupStore()
    setup = SetupCandidate(
        setup_candidate_id="setup-transition-identity",
        setup_type="ict_intraday_v0",
        setup_version="0.1.0",
        symbol="XAUUSD",
        direction=Direction.BULLISH,
        setup_timeframe=Timeframe.M15,
        entry_timeframe=Timeframe.M5,
        created_at=T0,
        available_at=T0,
        status=SetupStatus.DETECTED,
        evidence_candidate_ids=[],
        evidence_fact_ids=[],
    )
    setups.append_setup(setup)
    pipeline = M3SetupPipeline(
        bar_feed=feed,
        fact_store=FactStore(),
        candidate_store=CandidateStore(),
        setup_store=setups,
        tick_size=0.1,
    )
    available_at = T0 + timedelta(minutes=5)

    first = pipeline._append_evidence_link(
        setup,
        T0,
        available_at,
        evidence_fact_ids=["observation-a"],
        reason_codes=["RAID_EPISODE_EVIDENCE_MERGED"],
    )
    second = pipeline._append_evidence_link(
        setup,
        T0,
        available_at,
        evidence_fact_ids=["observation-b"],
        reason_codes=["RAID_EPISODE_EVIDENCE_MERGED"],
    )

    assert first.evidence_link_id != second.evidence_link_id
    current = setups.current_view(setup.setup_candidate_id)
    assert current.status is SetupStatus.DETECTED
    assert current.available_at == T0
    assert current.evidence_fact_ids == [
        "observation-a",
        "observation-b",
    ]
    assert setups.transitions(setup.setup_candidate_id) == ()
    assert len(setups.evidence_links(setup.setup_candidate_id)) == 2
    historical = setups.current(
        setup.setup_candidate_id,
        as_of=T0 + timedelta(minutes=4),
    )
    assert historical.evidence_fact_ids == []


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
        SetupStatus.READY_FOR_LLM,
    ]
    assert any(
        link.reason_codes == ["LINKED_REPRICING_FVG_AVAILABLE"]
        for link in setups.evidence_links(setup.setup_candidate_id)
    )
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
    assert reactions[0].metrics["setup_candidate_id"] == setup.setup_candidate_id
    assert (
        reactions[0].metrics["entry_zone_candidate_id"]
        == setup.entry_zone_candidate_ids[0]
    )
    assert reactions[0].metrics["lifecycle"] == "reacted"
    assert reactions[0].metrics["reaction_lag_bars"] == 0
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
    batches, facts, candidates, setups = run_sequence(bars, include_structure=False)
    assert len(batches[0].raid_episodes_created) == 1
    assert batches[0].raid_episodes_created[0].first_raid_candidate_id is None
    assert (
        batches[0].raid_episodes_created[0].observation_states[Timeframe.M5].value
        == "breached"
    )
    assert batches[0].setups_created == []
    assert batches[-1].setups_created
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
    bars = ready_bars()[:5] + [bar(5, open_=105.0, high=105.2, low=98.0, close=98.5)]
    _, _, _, setups = run_sequence(bars)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    assert setup.status is SetupStatus.INVALIDATED
    event = setups.transitions(setup.setup_candidate_id)[-1]
    assert event.reason_codes == ["SETUP_TF_CLOSE_BEYOND_RAID_EXTREME"]
    assert event.metrics["consecutive_closes"] == 1
    assert event.metrics["distance_buffer"] == 0.0


def test_raid_expires_when_no_shift_arrives_inside_configured_window() -> None:
    policy = M3Policy(
        setup_timeframes=(Timeframe.M5,),
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
        setup_timeframes=(Timeframe.M5,),
        shift_window_bars={Timeframe.M5: 12, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 2, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    bars = ready_bars()[:6] + [
        # Touch/penetrate, but close inside rather than favorably above the FVG.
        bar(6, open_=104.5, high=105.0, low=102.5, close=102.8),
        # Second eligible bar completes the configured expiry budget.
        bar(7, open_=103.2, high=104.0, low=102.5, close=102.8),
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
        == 2
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


def test_cross_tf_close_through_emits_only_on_each_new_crossing() -> None:
    bars = [
        bar(0, open_=100.0, high=101.0, low=99.0, close=100.0),
        bar(1, open_=100.0, high=101.0, low=99.0, close=100.5),
        bar(2, open_=100.5, high=102.0, low=100.0, close=101.0),
        # First M5 close through the active H1 high.
        bar(3, open_=101.0, high=106.0, low=100.5, close=105.5),
        # Still above: this is acceptance duration, not another interaction.
        bar(4, open_=105.5, high=107.0, low=105.0, close=106.0),
        # Return to the non-broken side; it carries no price-break fact.
        bar(5, open_=106.0, high=106.5, low=103.0, close=104.5),
        # A later M5 close through creates a new interaction episode.
        bar(6, open_=104.5, high=106.0, low=104.0, close=105.5),
    ]
    feed = ClosedBarFeed("XAUUSD")
    for item in bars:
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.append(
        reference_fact(
            fact_id="h1-swing-high-105-crossing",
            fact_type=FactType.SWING_POINT,
            timeframe=Timeframe.H1,
            side="high",
            price=105.0,
        )
    )
    candidates = CandidateStore()
    pipeline = M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    )
    for item in bars[3:]:
        pipeline.process_latest(timeframe=Timeframe.M5, as_of=item.close_time)

    cross_tf_breaks = [
        candidate
        for candidate in candidates.visible(as_of=bars[-1].close_time)
        if candidate.candidate_type == CandidateType.STRUCTURE_BREAK
        and candidate.raw_features.get("reference_fact_id")
        == "h1-swing-high-105-crossing"
    ]
    assert [candidate.occurred_at for candidate in cross_tf_breaks] == [
        bars[3].open_time,
        bars[6].open_time,
    ]
    assert all(
        candidate.raw_features["cross_timeframe_close_transition"] is True
        for candidate in cross_tf_breaks
    )


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


def test_m5_take_is_global_but_m15_observes_the_same_raid_episode() -> None:
    m5 = bar(0, open_=101.0, high=102.0, low=99.0, close=101.0)
    m15 = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=102.0,
        low=98.5,
        close=101.0,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (m5, m15):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            candle_fact(m5),
            candle_fact(m15),
        ]
    )
    candidates = CandidateStore()
    candidates.append(raid_candidate(m5))
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )

    first = m3.process_latest(timeframe=Timeframe.M5, as_of=m5.close_time)
    second = m3.process_latest(timeframe=Timeframe.M15, as_of=m15.close_time)

    assert len(first.raid_episodes_created) == 1
    assert len(second.raid_updates) == 1
    episode = m3.raid_store.visible(as_of=m15.close_time)[0]
    assert episode.observed_timeframes == [Timeframe.M5, Timeframe.M15]
    assert episode.extreme == 98.5
    assert {item.setup_timeframe for item in setups.visible(as_of=m15.close_time)} == {
        Timeframe.H1,
        Timeframe.M15,
    }
    assert all(
        item.hard_invalidation_price is None
        for item in setups.visible(as_of=m15.close_time)
    )
    assert all(
        item.metrics["dynamic_raid_extreme"] == 98.5
        for item in setups.visible(as_of=m15.close_time)
    )


def test_m5_raid_m15_shift_and_m5_entry_fvg_are_decoupled() -> None:
    raid_bar = bar(0, open_=101.0, high=102.0, low=99.0, close=101.0)
    shift_bar = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=105.0,
        low=100.5,
        close=104.5,
    )
    entry_bar = bar(3, open_=104.5, high=106.0, low=104.0, close=105.5)
    feed = ClosedBarFeed("XAUUSD")
    for item in (raid_bar, entry_bar, shift_bar):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            candle_fact(raid_bar),
            candle_fact(shift_bar),
            candle_fact(entry_bar),
            ObservableFact(
                fact_id="entry-fvg",
                fact_type=FactType.FVG_GEOMETRY,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                occurred_at=entry_bar.open_time,
                confirmed_at=entry_bar.close_time,
                available_at=entry_bar.close_time,
                direction=Direction.BULLISH,
                geometry=PriceGeometry(low=102.0, high=103.0),
                metrics={"ce": 102.5},
                detector_name="fixture",
                detector_version="0.1.0",
            ),
        ]
    )
    candidates = CandidateStore()
    candidates.extend(
        [
            raid_candidate(raid_bar),
            ConceptCandidate(
                candidate_id="m15-structure-break",
                candidate_type=CandidateType.STRUCTURE_BREAK,
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                direction=Direction.BULLISH,
                occurred_at=shift_bar.open_time,
                available_at=shift_bar.close_time,
                evidence_fact_ids=[],
                raw_features={
                    "reference_fact_id": "m15-swing-high",
                    "same_timeframe_structure_eligible": True,
                    "effective_rank_as_of_break": SwingRank.INTERMEDIATE.value,
                },
            ),
            ConceptCandidate(
                candidate_id="m5-displacement",
                candidate_type=CandidateType.DISPLACEMENT,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                direction=Direction.BULLISH,
                occurred_at=entry_bar.open_time,
                available_at=entry_bar.close_time,
            ),
        ]
    )
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )

    m3.process_latest(timeframe=Timeframe.M5, as_of=raid_bar.close_time)
    shift_batch = m3.process_latest(timeframe=Timeframe.M15, as_of=shift_bar.close_time)
    entry_batch = m3.process_latest(timeframe=Timeframe.M5, as_of=entry_bar.close_time)

    m15_setup = next(
        item
        for item in setups.visible(as_of=entry_bar.close_time)
        if item.setup_timeframe == Timeframe.M15
    )
    assert m15_setup.status is SetupStatus.FORMING
    assert m15_setup.entry_timeframe == Timeframe.M5
    assert len(m15_setup.entry_zone_candidate_ids) == 1
    assert any(
        item.candidate_type == CandidateType.SHIFT for item in shift_batch.candidates
    )
    assert entry_batch.candidates[0].timeframe == Timeframe.M5
    shift = next(
        item
        for item in candidates.visible(as_of=entry_bar.close_time)
        if item.candidate_type == CandidateType.SHIFT
    )
    assert shift.raw_features["effective_rank_as_of_break"] == "intermediate"


def test_promoted_ith_rank_is_resolved_on_price_break() -> None:
    bars = [
        OHLCBar(
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            open_time=T0 + timedelta(minutes=15 * index),
            close_time=T0 + timedelta(minutes=15 * (index + 1)),
            open=103.0,
            high=106.0 if index == 3 else 104.0,
            low=102.0,
            close=105.5 if index == 3 else 103.0,
        )
        for index in range(4)
    ]
    feed = ClosedBarFeed("XAUUSD")
    for item in bars:
        feed.append(item, observed_at=item.close_time)
    swing = reference_fact(
        fact_id="m15-promoted-high",
        fact_type=FactType.SWING_POINT,
        timeframe=Timeframe.M15,
        side="high",
        price=105.0,
    )
    promotion = ObservableFact(
        fact_id="m15-promoted-high-ith",
        fact_type=FactType.SWING_PROMOTION,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        occurred_at=swing.occurred_at,
        confirmed_at=swing.confirmed_at,
        available_at=swing.available_at,
        geometry=PriceGeometry(price=105.0),
        source_fact_ids=[swing.fact_id],
        metrics={
            "side": "high",
            "rank": SwingRank.INTERMEDIATE.value,
            "promoted_swing_fact_id": swing.fact_id,
        },
        detector_name="fixture",
        detector_version="0.1.0",
    )
    facts = FactStore()
    facts.extend([swing, promotion])
    candidates = CandidateStore()
    M2PrimitivePipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        tick_size=0.1,
        candle_config=CandleFeatureConfig(baseline_period=3),
    ).process_latest(timeframe=Timeframe.M15, as_of=bars[-1].close_time)
    structure = next(
        item
        for item in candidates.visible(as_of=bars[-1].close_time)
        if item.candidate_type == CandidateType.STRUCTURE_BREAK
    )
    assert structure.raw_features["effective_rank_as_of_break"] == "intermediate"


def test_fvg_touch_on_bar_a_can_react_on_bar_b() -> None:
    bars = ready_bars()[:6] + [
        bar(6, open_=104.5, high=105.0, low=102.5, close=102.8),
        bar(7, open_=103.2, high=104.0, low=103.2, close=103.5),
    ]
    _, facts, _, setups = run_sequence(bars)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    lifecycle = [
        fact.metrics["lifecycle"]
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.FVG_REACTION
    ]
    assert lifecycle == ["touched", "reacted"]
    reacted = next(
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.FVG_REACTION
        and fact.metrics["lifecycle"] == "reacted"
    )
    assert reacted.metrics["reaction_lag_bars"] == 1
    assert reacted.metrics["touched"] is False
    assert setup.status is SetupStatus.READY_FOR_LLM


def test_fvg_close_through_far_edge_fails_before_reaction() -> None:
    bars = ready_bars()[:6] + [
        bar(6, open_=104.5, high=105.0, low=101.0, close=101.5),
    ]
    _, facts, _, setups = run_sequence(bars)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    failed = next(
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.FVG_REACTION
    )
    assert failed.metrics["lifecycle"] == "failed"
    assert failed.metrics["fully_failed"] is True
    assert setup.status is SetupStatus.EXPIRED
    assert setups.transitions(setup.setup_candidate_id)[-1].reason_codes == [
        "ALL_ENTRY_ZONES_FAILED"
    ]


@pytest.mark.parametrize("late_by", [1, 2, 3])
def test_shift_one_to_three_bars_after_expiry_is_research_logged(
    late_by: int,
) -> None:
    policy = M3Policy(
        setup_timeframes=(Timeframe.M5,),
        shift_window_bars={Timeframe.M5: 1, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 24, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    bars = ready_bars()[:4] + [
        bar(4 + index, open_=101.0, high=103.0, low=100.5, close=102.0)
        for index in range(late_by)
    ]
    final_index = 4 + late_by
    bars.append(bar(final_index, open_=102.0, high=105.5, low=101.5, close=105.0))
    _, facts, _, setups = run_sequence(bars, policy=policy)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    assert setup.status is SetupStatus.EXPIRED
    late = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.RESEARCH_OBSERVATION
        and fact.metrics["reason_code"] == "LATE_SHIFT_AFTER_TERMINAL"
    ]
    assert len(late) == 1
    assert late[0].metrics["bars_after_terminal"] == late_by


def test_retrace_after_configured_fvg_window_is_research_logged() -> None:
    policy = M3Policy(
        setup_timeframes=(Timeframe.M5,),
        shift_window_bars={Timeframe.M5: 12, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 2, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    bars = ready_bars()[:6] + [
        bar(6, open_=105.0, high=106.0, low=104.0, close=105.0),
        bar(7, open_=105.0, high=106.0, low=104.0, close=105.0),
        bar(8, open_=104.0, high=105.0, low=102.5, close=104.0),
    ]
    _, facts, _, setups = run_sequence(bars, policy=policy)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    assert setup.status is SetupStatus.EXPIRED
    late = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.RESEARCH_OBSERVATION
        and fact.metrics["reason_code"] == "LATE_RETRACE_AFTER_TERMINAL"
    ]
    assert len(late) == 1
    assert late[0].metrics["bars_after_terminal"] == 1


def test_fvg_after_link_window_is_research_logged() -> None:
    policy = M3Policy(
        setup_timeframes=(Timeframe.M5,),
        repricing_max_lag_bars=0,
        shift_window_bars={Timeframe.M5: 12, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 24, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    bars = ready_bars()[:4] + [
        # Small close-through creates SHIFT without a displacement/FVG.
        bar(4, open_=103.9, high=104.3, low=103.8, close=104.1),
        # Repricing starts after the zero-lag link window has expired.
        bar(5, open_=104.1, high=106.0, low=101.5, close=105.8),
        # This bar confirms the bullish FVG whose middle candle is bar 5.
        bar(6, open_=105.8, high=107.0, low=105.0, close=106.5),
    ]
    _, facts, _, setups = run_sequence(bars, policy=policy)
    setup = setups.visible(as_of=bars[-1].close_time)[0]
    assert setup.status is SetupStatus.EXPIRED
    late = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.RESEARCH_OBSERVATION
        and fact.metrics["reason_code"] == "LATE_FVG_AFTER_TERMINAL"
    ]
    assert len(late) == 1
    assert late[0].metrics["bars_after_terminal"] == 1


def test_m5_fvg_inside_m15_shift_candle_is_linked_at_shift_close() -> None:
    raid_bar = bar(0, open_=101.0, high=102.0, low=99.0, close=101.0)
    displacement_bar = bar(1, open_=101.0, high=104.0, low=100.8, close=103.8)
    post_fvg_bar = bar(2, open_=103.8, high=105.0, low=103.5, close=104.5)
    shift_bar = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=105.0,
        low=99.0,
        close=104.5,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (raid_bar, displacement_bar, post_fvg_bar, shift_bar):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            candle_fact(raid_bar),
            candle_fact(shift_bar),
            ObservableFact(
                fact_id="inside-shift-fvg",
                fact_type=FactType.FVG_GEOMETRY,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                occurred_at=displacement_bar.open_time,
                confirmed_at=displacement_bar.close_time,
                available_at=displacement_bar.close_time,
                direction=Direction.BULLISH,
                geometry=PriceGeometry(low=102.0, high=103.0),
                metrics={"ce": 102.5},
                detector_name="fixture",
                detector_version="0.1.0",
            ),
        ]
    )
    candidates = CandidateStore()
    candidates.extend(
        [
            raid_candidate(raid_bar),
            ConceptCandidate(
                candidate_id="inside-shift-displacement",
                candidate_type=CandidateType.DISPLACEMENT,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                direction=Direction.BULLISH,
                occurred_at=displacement_bar.open_time,
                available_at=displacement_bar.close_time,
            ),
            ConceptCandidate(
                candidate_id="inside-shift-structure",
                candidate_type=CandidateType.STRUCTURE_BREAK,
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                direction=Direction.BULLISH,
                occurred_at=shift_bar.open_time,
                available_at=shift_bar.close_time,
                raw_features={
                    "reference_fact_id": "m15-high",
                    "same_timeframe_structure_eligible": True,
                },
            ),
        ]
    )
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )
    m3.process_latest(timeframe=Timeframe.M5, as_of=raid_bar.close_time)
    batch = m3.process_latest(timeframe=Timeframe.M15, as_of=shift_bar.close_time)

    m15_setup = next(
        item
        for item in setups.visible(as_of=shift_bar.close_time)
        if item.setup_timeframe == Timeframe.M15
    )
    assert m15_setup.status is SetupStatus.FORMING
    assert len(m15_setup.entry_zone_candidate_ids) == 1
    zone = candidates.as_mapping()[m15_setup.entry_zone_candidate_ids[0]]
    assert zone.timeframe == Timeframe.M5
    assert zone.occurred_at == displacement_bar.open_time
    assert zone.available_at == shift_bar.close_time
    assert zone.candidate_id not in {
        item.candidate_id
        for item in candidates.visible(as_of=displacement_bar.close_time)
    }
    assert zone.raw_features["temporal_relation"] == "inside_shift_bar"
    assert any(
        event.reason_codes == ["INSIDE_SHIFT_REPRICING_FVG_AVAILABLE"]
        for event in batch.evidence_links
    )


def test_m15_same_bar_reclaim_shift_uses_m5_physical_first_take_for_fvg() -> None:
    first_take_bar = bar(0, open_=101.0, high=101.5, low=99.0, close=99.5)
    displacement_bar = bar(1, open_=99.5, high=104.0, low=99.4, close=103.8)
    post_fvg_bar = bar(2, open_=103.8, high=105.0, low=103.5, close=104.5)
    shift_bar = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=105.0,
        low=98.5,
        close=104.5,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (first_take_bar, displacement_bar, post_fvg_bar, shift_bar):
        feed.append(item, observed_at=item.close_time)
    reference = reference_fact(
        fact_id="pdl-100",
        fact_type=FactType.PREVIOUS_DAY_LEVEL,
        timeframe=Timeframe.H1,
        side="low",
        price=100.0,
    )
    breach = ObservableFact(
        fact_id="m5-physical-first-take",
        fact_type=FactType.LEVEL_BREACH,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        occurred_at=first_take_bar.open_time,
        confirmed_at=first_take_bar.close_time,
        available_at=first_take_bar.close_time,
        geometry=PriceGeometry(price=100.0, extreme=99.0),
        source_fact_ids=[reference.fact_id],
        metrics={
            "reference_fact_id": reference.fact_id,
            "reference_fact_type": reference.fact_type.value,
            "reference_side": "sell_side",
            "reference_price": 100.0,
            "reference_timeframe": Timeframe.H1.value,
            "extreme": 99.0,
        },
        detector_name="fixture",
        detector_version="0.1.0",
    )
    fvg = ObservableFact(
        fact_id="pre-m15-confirmation-fvg",
        fact_type=FactType.FVG_GEOMETRY,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        occurred_at=displacement_bar.open_time,
        confirmed_at=displacement_bar.close_time,
        available_at=displacement_bar.close_time,
        direction=Direction.BULLISH,
        geometry=PriceGeometry(low=102.0, high=103.0),
        metrics={"ce": 102.5},
        detector_name="fixture",
        detector_version="0.1.0",
    )
    facts = FactStore()
    facts.extend(
        [reference, breach, candle_fact(first_take_bar), candle_fact(shift_bar), fvg]
    )
    candidates = CandidateStore()
    candidates.extend(
        [
            ConceptCandidate(
                candidate_id="pre-m15-confirmation-displacement",
                candidate_type=CandidateType.DISPLACEMENT,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                direction=Direction.BULLISH,
                occurred_at=displacement_bar.open_time,
                available_at=displacement_bar.close_time,
            ),
            ConceptCandidate(
                candidate_id="same-bar-m15-structure",
                candidate_type=CandidateType.STRUCTURE_BREAK,
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                direction=Direction.BULLISH,
                occurred_at=shift_bar.open_time,
                available_at=shift_bar.close_time,
                raw_features={
                    "reference_fact_id": "m15-high",
                    "same_timeframe_structure_eligible": True,
                },
            ),
        ]
    )
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )

    first = m3.process_latest(timeframe=Timeframe.M5, as_of=first_take_bar.close_time)
    assert first.setups_created == []
    batch = m3.process_latest(timeframe=Timeframe.M15, as_of=shift_bar.close_time)

    setup = next(
        item
        for item in setups.visible(as_of=shift_bar.close_time)
        if item.setup_timeframe == Timeframe.M15
    )
    shift = next(
        item
        for item in candidates.visible(as_of=shift_bar.close_time)
        if item.candidate_type == CandidateType.SHIFT
    )
    assert shift.raw_features["bars_after_raid"] == 0
    assert "SAME_BAR_RAID_SHIFT" in shift.machine_labels
    assert setup.hard_invalidation_price == 98.5
    assert setup.metrics["invalidation_frozen"] is True
    assert len(setup.entry_zone_candidate_ids) == 1
    zone = candidates.as_mapping()[setup.entry_zone_candidate_ids[0]]
    assert zone.raw_features["physical_first_take_fact_id"] == breach.fact_id
    assert zone.raw_features["physical_first_take_available_at"] == (
        first_take_bar.close_time.isoformat()
    )
    assert any(
        event.reason_codes == ["SAME_BAR_RAID_SHIFT_CANDIDATE"]
        for event in batch.transitions
    )


def test_inside_shift_fvg_consumed_before_confirmation_is_not_an_entry_zone() -> None:
    raid_bar = bar(0, open_=101.0, high=102.0, low=99.0, close=101.0)
    displacement_bar = bar(1, open_=101.0, high=104.0, low=100.8, close=103.8)
    consumed_bar = bar(2, open_=103.8, high=104.0, low=101.5, close=102.5)
    shift_bar = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=105.0,
        low=99.0,
        close=104.5,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (raid_bar, displacement_bar, consumed_bar, shift_bar):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            candle_fact(raid_bar),
            candle_fact(shift_bar),
            ObservableFact(
                fact_id="consumed-inside-fvg",
                fact_type=FactType.FVG_GEOMETRY,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                occurred_at=displacement_bar.open_time,
                confirmed_at=displacement_bar.close_time,
                available_at=displacement_bar.close_time,
                direction=Direction.BULLISH,
                geometry=PriceGeometry(low=102.0, high=103.0),
                metrics={"ce": 102.5},
                detector_name="fixture",
                detector_version="0.1.0",
            ),
        ]
    )
    candidates = CandidateStore()
    candidates.extend(
        [
            raid_candidate(raid_bar),
            ConceptCandidate(
                candidate_id="consumed-displacement",
                candidate_type=CandidateType.DISPLACEMENT,
                symbol="XAUUSD",
                timeframe=Timeframe.M5,
                direction=Direction.BULLISH,
                occurred_at=displacement_bar.open_time,
                available_at=displacement_bar.close_time,
            ),
            ConceptCandidate(
                candidate_id="consumed-shift",
                candidate_type=CandidateType.STRUCTURE_BREAK,
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                direction=Direction.BULLISH,
                occurred_at=shift_bar.open_time,
                available_at=shift_bar.close_time,
                raw_features={
                    "reference_fact_id": "m15-high",
                    "same_timeframe_structure_eligible": True,
                },
            ),
        ]
    )
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )
    m3.process_latest(timeframe=Timeframe.M5, as_of=raid_bar.close_time)
    batch = m3.process_latest(timeframe=Timeframe.M15, as_of=shift_bar.close_time)
    m15_setup = next(
        item
        for item in setups.visible(as_of=shift_bar.close_time)
        if item.setup_timeframe == Timeframe.M15
    )
    assert m15_setup.entry_zone_candidate_ids == []
    assert any(
        fact.metrics.get("reason_code")
        == "INSIDE_SHIFT_FVG_CONSUMED_BEFORE_CONFIRMATION"
        for fact in batch.facts
    )


def test_m5_take_m15_breach_then_next_m15_bar_reclaims_without_rebreach() -> None:
    m5 = bar(0, open_=101.0, high=102.0, low=99.0, close=101.0)
    m15_a = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=101.5,
        low=98.0,
        close=99.5,
    )
    m15_b = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0 + timedelta(minutes=15),
        close_time=T0 + timedelta(minutes=30),
        open=100.2,
        high=101.0,
        low=100.1,
        close=100.5,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (m5, m15_a, m15_b):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            candle_fact(m5),
            candle_fact(m15_a),
            candle_fact(m15_b),
        ]
    )
    candidates = CandidateStore()
    candidates.append(raid_candidate(m5))
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )
    m3.process_latest(timeframe=Timeframe.M5, as_of=m5.close_time)
    first = m3.process_latest(timeframe=Timeframe.M15, as_of=m15_a.close_time)
    second = m3.process_latest(timeframe=Timeframe.M15, as_of=m15_b.close_time)

    assert first.raid_updates[0].observation_state.value == "breached"
    assert second.raid_updates[0].observation_state.value == "reclaimed"
    assert second.facts[0].metrics["breached_this_bar"] is False
    assert second.facts[0].metrics["reclaim_span_bars"] == 1
    episode = m3.raid_store.visible(as_of=m15_b.close_time)[0]
    assert episode.observation_states[Timeframe.M15].value == "reclaimed"
    assert episode.extreme == 98.0
    assert all(
        item.hard_invalidation_price is None
        for item in setups.visible(as_of=m15_b.close_time)
    )
    assert all(
        item.metrics["dynamic_raid_extreme"] == 98.0
        for item in setups.visible(as_of=m15_b.close_time)
    )


def test_breached_raid_emits_only_on_new_extreme_or_reclaim() -> None:
    m5 = bar(0, open_=101.0, high=102.0, low=99.0, close=101.0)
    m15_breach = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=101.5,
        low=98.0,
        close=99.5,
    )
    m15_unchanged = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0 + timedelta(minutes=15),
        close_time=T0 + timedelta(minutes=30),
        open=99.5,
        high=100.0,
        low=98.2,
        close=99.4,
    )
    m15_reclaim = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0 + timedelta(minutes=30),
        close_time=T0 + timedelta(minutes=45),
        open=99.4,
        high=101.0,
        low=98.4,
        close=100.5,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (m5, m15_breach, m15_unchanged, m15_reclaim):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            *(candle_fact(item) for item in (m5, m15_breach, m15_unchanged, m15_reclaim)),
        ]
    )
    candidates = CandidateStore()
    candidates.append(raid_candidate(m5))
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=SetupStore(),
        tick_size=0.1,
    )

    m3.process_latest(timeframe=Timeframe.M5, as_of=m5.close_time)
    breached = m3.process_latest(
        timeframe=Timeframe.M15, as_of=m15_breach.close_time
    )
    unchanged = m3.process_latest(
        timeframe=Timeframe.M15, as_of=m15_unchanged.close_time
    )
    reclaimed = m3.process_latest(
        timeframe=Timeframe.M15, as_of=m15_reclaim.close_time
    )

    assert len(breached.raid_updates) == 1
    assert unchanged.raid_updates == []
    assert not any(
        fact.fact_type is FactType.RAID_OBSERVATION for fact in unchanged.facts
    )
    assert len(reclaimed.raid_updates) == 1
    assert reclaimed.raid_updates[0].observation_state.value == "reclaimed"
    episode = m3.raid_store.current_view(reclaimed.raid_updates[0].raid_episode_id)
    assert episode.observation_extremes[Timeframe.M15] == 98.0


def test_m15_candle_containing_raid_updates_dynamic_extreme_without_invalidation() -> (
    None
):
    m5 = bar(1, open_=101.0, high=102.0, low=99.0, close=101.0)
    m15 = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=T0,
        close_time=T0 + timedelta(minutes=15),
        open=101.0,
        high=101.5,
        low=98.5,
        close=98.8,
    )
    feed = ClosedBarFeed("XAUUSD")
    for item in (m5, m15):
        feed.append(item, observed_at=item.close_time)
    facts = FactStore()
    facts.extend(
        [
            reference_fact(
                fact_id="pdl-100",
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                timeframe=Timeframe.H1,
                side="low",
                price=100.0,
            ),
            candle_fact(m5),
            candle_fact(m15),
        ]
    )
    candidates = CandidateStore()
    candidates.append(raid_candidate(m5))
    setups = SetupStore()
    m3 = M3SetupPipeline(
        bar_feed=feed,
        fact_store=facts,
        candidate_store=candidates,
        setup_store=setups,
        tick_size=0.1,
    )
    m3.process_latest(timeframe=Timeframe.M5, as_of=m5.close_time)
    m3.process_latest(timeframe=Timeframe.M15, as_of=m15.close_time)
    m15_setup = next(
        item
        for item in setups.visible(as_of=m15.close_time)
        if item.setup_timeframe == Timeframe.M15
    )
    assert m15.open_time < m15_setup.created_at
    assert m15.close_time > setups.get_origin(m15_setup.setup_candidate_id).available_at
    assert m15_setup.status is SetupStatus.DETECTED
    assert m15_setup.hard_invalidation_price is None
    assert m15_setup.metrics["dynamic_raid_extreme"] == 98.5
    assert m15_setup.metrics["invalidation_frozen"] is False


def test_fvg_logs_multi_touch_path_aggregates_before_reaction() -> None:
    bars = ready_bars()[:6] + [
        bar(6, open_=104.5, high=105.0, low=102.8, close=102.9),
        bar(7, open_=102.9, high=103.2, low=102.4, close=102.7),
        bar(8, open_=103.2, high=104.0, low=103.2, close=103.5),
    ]
    _, facts, _, setups = run_sequence(bars)
    path = [
        fact
        for fact in facts.visible(as_of=bars[-1].close_time)
        if fact.fact_type == FactType.FVG_REACTION
    ]
    assert [item.metrics["lifecycle"] for item in path] == [
        "touched",
        "touched",
        "reacted",
    ]
    final = path[-1].metrics
    assert final["touch_count"] == 2
    assert final["first_penetration"] == pytest.approx(0.2)
    assert final["max_zone_penetration_fraction"] == pytest.approx(0.6)
    assert final["ce_reached"] is True
    assert final["full_fill"] is False
    assert final["bars_since_first_touch"] == 2
    assert final["bars_inside_zone"] == 2
    assert final["max_zone_penetration_points"] == pytest.approx(0.6)
    assert (
        setups.visible(as_of=bars[-1].close_time)[0].status is SetupStatus.READY_FOR_LLM
    )
