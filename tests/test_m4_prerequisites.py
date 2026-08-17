from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import FactType, Session, Timeframe
from ict_trading_agent.m4 import DataQualityError, ExnessCsvLoader, M4ReplayEngine
from ict_trading_agent.m4_support import (
    CausalReferenceBuilder,
    CausalReferencePolicy,
    ExnessXauCalendarPreset,
    M4StudyWindow,
    M4SymbolMetadata,
    SessionContextProvider,
)
from ict_trading_agent.market import MarketSequenceAdjacencyPolicy, OHLCBar
from ict_trading_agent.sessions import SessionSchedule, SessionWindow


def _bar(opened: datetime, timeframe: Timeframe = Timeframe.M5) -> OHLCBar:
    duration = timedelta(days=1) if timeframe == Timeframe.D1 else timedelta(minutes=5)
    return OHLCBar(
        symbol="XAUUSD",
        timeframe=timeframe,
        open_time=opened,
        close_time=opened + duration,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
    )


def _identity() -> dict[str, object]:
    return {
        "symbol_metadata": M4SymbolMetadata(
            symbol="XAUUSD", digits=2, point=0.01, trade_tick_size=0.01
        ),
        "git_commit_sha": "abc123",
    }


def test_exness_xau_calendar_is_dst_aware_and_unknown_gaps_still_fail() -> None:
    preset = ExnessXauCalendarPreset()
    calendar = preset.build(start_date=date(2026, 3, 6), end_date=date(2026, 3, 10))

    assert calendar.covers_gap(
        datetime(2026, 3, 6, 21, 58, tzinfo=UTC),
        datetime(2026, 3, 8, 22, 5, tzinfo=UTC),
    )
    assert calendar.covers_gap(
        datetime(2026, 3, 9, 20, 58, tzinfo=UTC),
        datetime(2026, 3, 9, 22, 2, tzinfo=UTC),
    )
    assert not calendar.covers_gap(
        datetime(2026, 3, 9, 19, 0, tzinfo=UTC),
        datetime(2026, 3, 9, 19, 10, tzinfo=UTC),
    )

    unknown = """date,time,open,high,low,close
2026-03-09,19:00:00,100,101,99,100.5
2026-03-09,19:10:00,100,101,99,100.5
"""
    with pytest.raises(DataQualityError):
        ExnessCsvLoader(timeframe=Timeframe.M5, closure_calendar=calendar).loads(
            unknown
        )


def test_warmup_runs_but_is_excluded_from_main_summary() -> None:
    start = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    bars = [_bar(start + timedelta(minutes=5 * index)) for index in range(7)]
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        candle_config=CandleFeatureConfig(baseline_period=1),
        **_identity(),
    )
    result = engine.run(
        bars,
        study_window=M4StudyWindow(
            replay_start=start,
            analysis_start=start + timedelta(minutes=20),
        ),
    )

    bar_events = [item for item in result.events if item.category == "closed_bar"]
    assert len(bar_events) == 7
    assert sum(item.study_phase == "warmup" for item in bar_events) == 3
    assert result.summary.bars == 4
    assert all(not item.included_in_analysis for item in bar_events[:3])


def test_manifest_fingerprints_code_configs_metadata_and_calendar() -> None:
    start = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    calendar = ExnessXauCalendarPreset().build(
        start_date=start.date(), end_date=start.date()
    )
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        candle_config=CandleFeatureConfig(baseline_period=1),
        adjacency_policy=MarketSequenceAdjacencyPolicy(calendar),
        **_identity(),
    )
    result = engine.run(
        [_bar(start + timedelta(minutes=5 * index)) for index in range(4)],
        study_window=M4StudyWindow(
            replay_start=start, analysis_start=start + timedelta(seconds=1)
        ),
    )

    assert result.manifest.git_commit_sha == "abc123"
    assert result.manifest.symbol_metadata.trade_tick_size == 0.01
    assert result.manifest.candle_config["baseline_period"] == 1
    assert (
        result.manifest.adjacency_calendar_policy["calendar"]["calendar_id"]
        == "exness.xau.regular-hours.us-dst@2026-08-13"
    )
    assert result.run_id.endswith(result.manifest.fingerprint()[:24])


def test_session_context_is_new_york_dst_aware_without_filtering_policy() -> None:
    schedule = SessionSchedule(
        windows=[
            SessionWindow(
                session=Session.NY_AM,
                timezone="America/New_York",
                start_local=time(8, 0),
                end_local=time(12, 0),
            )
        ]
    )
    provider = SessionContextProvider(schedule)

    winter = provider.context_at(datetime(2026, 2, 2, 13, 0, tzinfo=UTC))
    summer = provider.context_at(datetime(2026, 8, 3, 12, 0, tzinfo=UTC))
    assert winter["session"] == Session.NY_AM.value
    assert summer["session"] == Session.NY_AM.value
    assert winter["new_york_utc_offset_seconds"] == -5 * 3600
    assert summer["new_york_utc_offset_seconds"] == -4 * 3600
    assert provider.manifest()["annotation_only"] is True


def test_session_context_preserves_unprioritized_overlaps_as_multi_label() -> None:
    schedule = SessionSchedule(
        windows=[
            SessionWindow(
                session=Session.LONDON,
                timezone="America/New_York",
                start_local=time(8, 0),
                end_local=time(10, 0),
            ),
            SessionWindow(
                session=Session.NY_AM,
                timezone="America/New_York",
                start_local=time(9, 0),
                end_local=time(12, 0),
            ),
        ]
    )
    context = SessionContextProvider(schedule).context_at(
        datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    )

    assert context["sessions"] == [Session.LONDON.value, Session.NY_AM.value]
    assert context["session"] is None
    assert context["primary_session"] is None
    assert context["session_overlap"] is True
    assert context["session_overlap_key"] == "london+ny_am"


def test_causal_reference_builder_emits_native_d1_levels_only_at_close() -> None:
    opened = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)
    builder = CausalReferenceBuilder(CausalReferencePolicy())
    facts = builder.ingest(_bar(opened, Timeframe.D1))

    assert {item.fact_type for item in facts} == {FactType.PREVIOUS_DAY_LEVEL}
    assert {item.metrics["side"] for item in facts} == {"high", "low"}
    assert all(item.available_at == opened + timedelta(days=1) for item in facts)


def test_session_levels_and_tdo_require_explicit_clock_and_closed_source_bars() -> None:
    schedule = SessionSchedule(
        windows=[
            SessionWindow(
                session=Session.NY_AM,
                timezone="America/New_York",
                start_local=time(8, 0),
                end_local=time(8, 10),
            )
        ]
    )
    builder = CausalReferenceBuilder(
        CausalReferencePolicy(
            previous_day_from_native_d1=False,
            session_source_timeframe=Timeframe.M5,
            session_schedule=schedule,
            true_day_open_source_timeframe=Timeframe.M5,
            true_day_open_timezone="America/New_York",
            true_day_open_local=time(8, 0),
        )
    )
    first = _bar(datetime(2026, 8, 3, 12, 0, tzinfo=UTC))
    second = _bar(datetime(2026, 8, 3, 12, 5, tzinfo=UTC))

    first_facts = builder.ingest(first)
    second_facts = builder.ingest(second)
    assert [item.fact_type for item in first_facts] == [FactType.TRUE_DAY_OPEN]
    assert first_facts[0].occurred_at == first.open_time
    assert first_facts[0].confirmed_at == first.open_time
    assert first_facts[0].available_at == first.open_time
    assert first_facts[0].metrics["observed_at"] == first.close_time.isoformat()
    assert {item.fact_type for item in second_facts} == {FactType.SESSION_LEVEL}
    assert {item.metrics["side"] for item in second_facts} == {"high", "low"}
    assert all(item.available_at == second.close_time for item in second_facts)
    assert all(item.metrics["source_timeframe"] == "M5" for item in second_facts)


def test_tdo_audit_separates_concept_availability_from_closed_bar_observation() -> None:
    start = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    reference_builder = CausalReferenceBuilder(
        CausalReferencePolicy(
            previous_day_from_native_d1=False,
            true_day_open_source_timeframe=Timeframe.M5,
            true_day_open_timezone="America/New_York",
            true_day_open_local=time(5, 0),
        )
    )
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        candle_config=CandleFeatureConfig(baseline_period=1),
        reference_builder=reference_builder,
        **_identity(),
    )
    result = engine.run(
        [_bar(start + timedelta(minutes=5 * index)) for index in range(4)],
        study_window=M4StudyWindow(
            replay_start=start, analysis_start=start + timedelta(seconds=1)
        ),
    )

    tdo = next(item for item in result.events if item.category == "true_day_open")
    assert tdo.occurred_at == start
    assert tdo.available_at == start
    assert tdo.observed_at == start + timedelta(minutes=5)
    first_step = next(item for item in result.steps if item.as_of == tdo.observed_at)
    assert tdo.event_id in first_step.event_ids


def test_symbol_metadata_must_match_engine_symbol() -> None:
    with pytest.raises(ValueError, match="symbol metadata"):
        M4ReplayEngine(
            symbol="XAUUSD",
            symbol_metadata=M4SymbolMetadata(
                symbol="BTCUSD", digits=2, point=0.01, trade_tick_size=0.01
            ),
            git_commit_sha="abc123",
        )
