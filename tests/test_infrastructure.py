from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from ict_trading_agent.candidates import ConceptCandidate
from ict_trading_agent.config import TradingDayPolicy, build_xauusd_intraday_v0
from ict_trading_agent.detectors import (
    CandleFeatureConfig,
    CandleFeatureDetector,
    FVGGeometryDetector,
    ThreeBarSwingDetector,
)
from ict_trading_agent.enums import (
    CandidateType,
    Direction,
    FactType,
    Session,
    Timeframe,
)
from ict_trading_agent.market import (
    ClosedBarFeed,
    ExplicitClosureCalendar,
    MarketClosure,
    MarketSequenceAdjacencyPolicy,
    OHLCBar,
    bars_are_contiguous,
)
from ict_trading_agent.reducer import MarketStateReducer
from ict_trading_agent.references import (
    CompletedSessionRange,
    CompletedTradingDay,
    ReferenceFactBuilder,
)
from ict_trading_agent.sessions import SessionSchedule, SessionWindow
from ict_trading_agent.state import TemporalContext
from ict_trading_agent.stores import CandidateStore, DuplicateRecordError, FactStore

T0 = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)


def bar(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    timeframe: Timeframe = Timeframe.M5,
    closed: bool = True,
) -> OHLCBar:
    opened = T0 + timedelta(minutes=minute)
    duration = {
        Timeframe.M5: timedelta(minutes=5),
        Timeframe.M15: timedelta(minutes=15),
    }[timeframe]
    return OHLCBar(
        symbol="XAUUSD",
        timeframe=timeframe,
        open_time=opened,
        close_time=opened + duration,
        open=open_,
        high=high,
        low=low,
        close=close,
        is_closed=closed,
    )


def test_closed_bar_feed_rejects_developing_and_future_bars() -> None:
    feed = ClosedBarFeed("XAUUSD")
    developing = bar(0, open_=100, high=102, low=99, close=101, closed=False)
    with pytest.raises(ValueError, match="developing"):
        feed.append(developing, observed_at=developing.close_time)
    closed = developing.model_copy(update={"is_closed": True})
    with pytest.raises(ValueError, match="before close_time"):
        feed.append(closed, observed_at=closed.close_time - timedelta(seconds=1))
    feed.append(closed, observed_at=closed.close_time)
    assert feed.latest(Timeframe.M5, as_of=closed.close_time) == closed
    overlapping = bar(4, open_=101, high=103, low=100, close=102)
    with pytest.raises(ValueError, match="overlap"):
        feed.append(overlapping, observed_at=overlapping.close_time)


def test_three_bar_swing_is_strict_and_available_after_right_close() -> None:
    detector = ThreeBarSwingDetector(tick_size=0.1)
    left = bar(0, open_=100, high=101, low=99, close=100)
    middle = bar(5, open_=100, high=105, low=98, close=103)
    right = bar(10, open_=103, high=104, low=100, close=102)
    facts = detector.detect_triplet(left, middle, right)
    assert len(facts) == 2  # outside bar: both strict swing high and low
    assert {fact.metrics["side"] for fact in facts} == {"high", "low"}
    assert all(fact.occurred_at == middle.open_time for fact in facts)
    assert all(fact.available_at == right.close_time for fact in facts)

    equal_right = right.model_copy(update={"high": 105.0})
    equal_facts = detector.detect_triplet(left, middle, equal_right)
    assert {fact.metrics["side"] for fact in equal_facts} == {"low"}


def test_detectors_reject_missing_candle_gap() -> None:
    detector = ThreeBarSwingDetector(tick_size=0.1)
    left = bar(0, open_=100, high=101, low=99, close=100)
    middle = bar(5, open_=100, high=105, low=98, close=103)
    gapped_right = bar(15, open_=103, high=104, low=100, close=102)
    with pytest.raises(ValueError, match="contiguous"):
        detector.detect_triplet(left, middle, gapped_right)


def test_market_calendar_distinguishes_closure_from_missing_trading_bar() -> None:
    left = bar(0, open_=100, high=101, low=99, close=100)
    right = bar(15, open_=100, high=102, low=99, close=101)
    assert bars_are_contiguous(left, right) is False

    calendar = ExplicitClosureCalendar(
        calendar_id="fixture-calendar",
        closures=[
            MarketClosure(
                start_at=left.close_time,
                end_at=right.open_time,
                reason="weekend-or-maintenance",
            )
        ],
    )
    policy = MarketSequenceAdjacencyPolicy(calendar)
    assert bars_are_contiguous(left, right, policy) is True

    incomplete_calendar = ExplicitClosureCalendar(
        calendar_id="incomplete-calendar",
        closures=[
            MarketClosure(
                start_at=left.close_time,
                end_at=right.open_time - timedelta(minutes=5),
                reason="partial-closure",
            )
        ],
    )
    assert (
        bars_are_contiguous(
            left,
            right,
            MarketSequenceAdjacencyPolicy(incomplete_calendar),
        )
        is False
    )

    middle = bar(5, open_=100, high=105, low=98, close=103)
    triplet_calendar = ExplicitClosureCalendar(
        calendar_id="triplet-calendar",
        closures=[
            MarketClosure(
                start_at=middle.close_time,
                end_at=right.open_time,
                reason="scheduled-close",
            )
        ],
    )
    facts = ThreeBarSwingDetector(
        tick_size=0.1,
        adjacency_policy=MarketSequenceAdjacencyPolicy(triplet_calendar),
    ).detect_triplet(left, middle, right)
    assert facts
    feature = CandleFeatureDetector(
        CandleFeatureConfig(baseline_period=2),
        adjacency_policy=MarketSequenceAdjacencyPolicy(triplet_calendar),
    ).detect(right, [left, middle])
    assert feature.available_at == right.close_time


def test_daily_baseline_can_cross_an_explicit_weekend_closure() -> None:
    thursday_open = datetime(2026, 8, 13, tzinfo=UTC)

    def daily(offset_days: int, open_: float, close: float) -> OHLCBar:
        opened = thursday_open + timedelta(days=offset_days)
        return OHLCBar(
            symbol="XAUUSD",
            timeframe=Timeframe.D1,
            open_time=opened,
            close_time=opened + timedelta(days=1),
            open=open_,
            high=max(open_, close) + 5.0,
            low=min(open_, close) - 5.0,
            close=close,
        )

    thursday = daily(0, 3300.0, 3310.0)
    friday = daily(1, 3310.0, 3320.0)
    monday = daily(4, 3320.0, 3335.0)
    calendar = ExplicitClosureCalendar(
        calendar_id="xau-weekend-fixture",
        closures=[
            MarketClosure(
                start_at=friday.close_time,
                end_at=monday.open_time,
                reason="weekend",
            )
        ],
    )
    feature = CandleFeatureDetector(
        CandleFeatureConfig(baseline_period=2),
        adjacency_policy=MarketSequenceAdjacencyPolicy(calendar),
    ).detect(monday, [thursday, friday])
    assert feature.metrics["baseline_period"] == 2


def test_fvg_geometry_is_three_bar_strict_and_point_in_time_safe() -> None:
    detector = FVGGeometryDetector(tick_size=0.1)
    left = bar(0, open_=100, high=101, low=99, close=100)
    middle = bar(5, open_=101, high=106, low=100, close=105)
    right = bar(10, open_=103, high=108, low=103, close=107)
    (fact,) = detector.detect_triplet(left, middle, right)
    assert fact.fact_type is FactType.FVG_GEOMETRY
    assert fact.direction is Direction.BULLISH
    assert fact.geometry is not None
    assert (fact.geometry.low, fact.geometry.high) == (101.0, 103.0)
    assert fact.occurred_at == middle.open_time
    assert fact.available_at == right.close_time

    touching = right.model_copy(update={"low": 101.0})
    assert detector.detect_triplet(left, middle, touching) == ()


def test_fact_store_is_append_only_and_queries_available_at() -> None:
    detector = FVGGeometryDetector(tick_size=0.1)
    fact = detector.detect_triplet(
        bar(0, open_=100, high=101, low=99, close=100),
        bar(5, open_=101, high=106, low=100, close=105),
        bar(10, open_=103, high=108, low=103, close=107),
    )[0]
    store = FactStore()
    store.append(fact)
    assert store.contains(fact.fact_id)
    assert store.existing_ids([fact.fact_id, "missing"]) == {fact.fact_id}
    assert store.get_optional("missing") is None
    assert store.visible(as_of=fact.available_at - timedelta(seconds=1)) == ()
    assert store.visible(as_of=fact.available_at) == (fact,)
    assert store.visible(
        as_of=fact.available_at,
        fact_types={FactType.FVG_GEOMETRY, FactType.SWING_POINT},
    ) == (fact,)
    assert store.visible(
        as_of=fact.available_at,
        fact_types={FactType.SWING_POINT},
    ) == ()
    with pytest.raises(DuplicateRecordError):
        store.append(fact)


def test_reference_facts_require_completed_explicit_periods() -> None:
    builder = ReferenceFactBuilder()
    period = CompletedTradingDay(
        symbol="XAUUSD",
        trading_day="2026-08-14",
        start_at=T0,
        end_at=T0 + timedelta(days=1),
        available_at=T0 + timedelta(days=1),
        high=3370.0,
        low=3320.0,
    )
    high, low = builder.previous_day(period)
    assert high.metrics["side"] == "high"
    assert low.metrics["side"] == "low"
    assert high.available_at == period.end_at

    session = CompletedSessionRange(
        symbol="XAUUSD",
        trading_day="2026-08-15",
        session=Session.ASIA,
        start_at=T0,
        end_at=T0 + timedelta(hours=6),
        available_at=T0 + timedelta(hours=6),
        high=3360.0,
        low=3330.0,
    )
    session_high, _ = builder.session(session)
    assert session_high.metrics["session"] == "asia"


def test_session_schedule_uses_iana_timezone_and_explicit_overlap_priority() -> None:
    schedule = SessionSchedule(
        windows=[
            SessionWindow(
                session=Session.NY_AM,
                timezone="America/New_York",
                start_local=time(8, 0),
                end_local=time(11, 0),
            )
        ]
    )
    summer = datetime(2026, 8, 15, 13, 0, tzinfo=UTC)  # 09:00 EDT
    assert schedule.primary_session_at(summer) is Session.NY_AM


def test_reducer_excludes_future_facts_and_candidates() -> None:
    profile = build_xauusd_intraday_v0(
        TradingDayPolicy(
            timezone="America/New_York",
            rollover_local_time=time(17, 0),
        )
    )
    feed = ClosedBarFeed("XAUUSD")
    m5 = bar(0, open_=100, high=102, low=99, close=101)
    feed.append(m5, observed_at=m5.close_time)
    detector = ThreeBarSwingDetector(tick_size=0.1)
    facts = detector.detect_triplet(
        bar(0, open_=100, high=101, low=99, close=100),
        bar(5, open_=100, high=105, low=98, close=103),
        bar(10, open_=103, high=104, low=100, close=102),
    )
    fact_store = FactStore()
    fact_store.extend(facts)
    candidate_store = CandidateStore()
    future_candidate = ConceptCandidate(
        candidate_id="future-candidate",
        candidate_type=CandidateType.STRUCTURE_BREAK,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        occurred_at=T0,
        available_at=T0 + timedelta(hours=1),
    )
    candidate_store.append(future_candidate)
    assert candidate_store.contains(future_candidate.candidate_id)
    assert candidate_store.get(future_candidate.candidate_id) == future_candidate
    assert candidate_store.get_optional("missing") is None
    reducer = MarketStateReducer(
        profile=profile,
        bar_feed=feed,
        fact_store=fact_store,
        candidate_store=candidate_store,
    )
    state = reducer.reduce(
        symbol="XAUUSD",
        as_of=T0 + timedelta(minutes=15),
        temporal=TemporalContext(
            trading_day="2026-08-15",
            session=Session.ASIA,
            ny_time=T0 + timedelta(minutes=15),
        ),
    )
    assert len(state.visible_fact_ids) == 2
    assert state.visible_candidate_ids == []
