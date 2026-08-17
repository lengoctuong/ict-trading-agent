from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import FactType, Session, SetupStatus, SwingRank, Timeframe
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.m3 import M3Policy
from ict_trading_agent.m4 import (
    DataQualityError,
    ExnessBarRecord,
    ExnessCsvLoader,
    M4EventKind,
    M4ReplayEngine,
)
from ict_trading_agent.m4_support import (
    M4StudyWindow,
    M4SymbolMetadata,
    SessionContextProvider,
)
from ict_trading_agent.market import OHLCBar
from ict_trading_agent.sessions import SessionSchedule, SessionWindow

T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


def engine_identity() -> dict[str, object]:
    return {
        "symbol_metadata": M4SymbolMetadata(
            symbol="XAUUSD", digits=2, point=0.01, trade_tick_size=0.1
        ),
        "git_commit_sha": "test-revision",
    }


def study() -> M4StudyWindow:
    return M4StudyWindow(
        replay_start=T0,
        analysis_start=T0 + timedelta(seconds=1),
    )


def bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    timeframe: Timeframe = Timeframe.M5,
) -> OHLCBar:
    minutes = 5 if timeframe == Timeframe.M5 else 15
    opened = T0 + timedelta(minutes=minutes * index)
    return OHLCBar(
        symbol="XAUUSD",
        timeframe=timeframe,
        open_time=opened,
        close_time=opened + timedelta(minutes=minutes),
        open=open_,
        high=high,
        low=low,
        close=close,
    )


def reference(
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


def record(item: OHLCBar, row: int) -> ExnessBarRecord:
    return ExnessBarRecord(
        bar=item,
        source_name="fixture",
        source_row_number=row,
        spread_points=25.0,
    )


def ready_bars() -> list[OHLCBar]:
    return [
        bar(0, open_=101.0, high=102.0, low=100.5, close=101.2),
        bar(1, open_=101.2, high=102.0, low=100.8, close=101.3),
        bar(2, open_=101.3, high=102.0, low=100.9, close=101.4),
        bar(3, open_=101.4, high=102.0, low=99.0, close=101.0),
        bar(4, open_=101.0, high=105.5, low=100.8, close=105.0),
        bar(5, open_=105.0, high=106.0, low=103.0, close=105.5),
        bar(6, open_=104.5, high=105.0, low=102.5, close=104.5),
    ]


def test_exness_mt5_loader_preserves_utc_spread_and_reports_quality() -> None:
    header = (
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t"
        "<VOL>\t<SPREAD>\t<BID_SOURCE>"
    )
    text = (
        header
        + """
2026.08.17\t09:00:00\t100.0\t101.0\t99.5\t100.5\t120\t3\t25\tbid
2026.08.17\t09:05:00\t100.5\t101.2\t100.0\t101.0\t130\t4\t30\tbid
2026.08.17\t09:10:00\t101.0\t101.5\t100.8\t101.2\t140\t5\t150\tbid
"""
    )
    dataset = ExnessCsvLoader(
        timeframe=Timeframe.M5,
        abnormal_spread_threshold_points=100.0,
    ).loads(text, source_name="xau-m5.tsv")

    assert dataset.quality.rows_read == 3
    assert dataset.quality.rows_accepted == 3
    assert dataset.quality.gaps == []
    assert dataset.quality.abnormal_spread_rows == 1
    assert dataset.records[0].bar.open_time.tzinfo is UTC
    assert dataset.records[0].bar.close_time == T0 + timedelta(minutes=5)
    assert dataset.records[0].spread_points == 25.0
    assert dataset.records[0].source_metrics == {"bid_source": "bid"}


def test_exness_loader_rejects_duplicates_and_can_report_gaps_permissively() -> None:
    duplicate = """date,time,open,high,low,close
2026-08-17,09:00:00,100,101,99,100.5
2026-08-17,09:00:00,100,101,99,100.5
"""
    with pytest.raises(DataQualityError) as raised:
        ExnessCsvLoader(timeframe=Timeframe.M5).loads(duplicate)
    assert raised.value.report.duplicate_rows == 1

    gap = """date,time,open,high,low,close
2026-08-17,09:00:00,100,101,99,100.5
2026-08-17,09:10:00,100.5,101,100,100.8
"""
    dataset = ExnessCsvLoader(
        timeframe=Timeframe.M5,
        strict=False,
    ).loads(gap)
    assert dataset.quality.unexplained_gap_count == 1
    assert dataset.quality.gaps[0].missing_bars == 1


def test_m4_replay_runs_production_path_and_exports_audit_datasets(tmp_path) -> None:
    bars = ready_bars()
    context_provider = SessionContextProvider(
        SessionSchedule(
            windows=[
                SessionWindow(
                    session=Session.LONDON,
                    timezone="America/New_York",
                    start_local=time(4, 0),
                    end_local=time(6, 0),
                ),
                SessionWindow(
                    session=Session.NY_AM,
                    timezone="America/New_York",
                    start_local=time(5, 0),
                    end_local=time(7, 0),
                ),
            ]
        )
    )
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        **engine_identity(),
        initial_facts=[
            reference(
                "pdl-100", FactType.PREVIOUS_DAY_LEVEL, Timeframe.D1, "low", 100.0
            ),
            reference("m5-high-104", FactType.SWING_POINT, Timeframe.M5, "high", 104.0),
        ],
        candle_config=CandleFeatureConfig(baseline_period=3),
        m3_policy=M3Policy(setup_timeframes=(Timeframe.M5,)),
        context_provider=context_provider,
    )

    result = engine.run(
        [record(item, index + 2) for index, item in enumerate(bars)],
        study_window=study(),
    )

    assert result.summary.bars == 7
    assert result.summary.liquidity_raids == 1
    assert result.summary.same_bar_sweeps == 1
    assert result.summary.shifts == 1
    assert result.summary.linked_fvgs == 1
    assert result.summary.reactions == 1
    assert result.summary.ready_for_llm == 1
    assert result.summary.setups_by_status == {SetupStatus.READY_FOR_LLM.value: 1}
    assert result.summary.breakdowns["session"] == {"london": 1, "ny_am": 1}
    assert result.summary.breakdowns["session_overlap"] == {"london+ny_am": 1}
    assert all(event.available_at <= result.completed_at for event in result.events)
    assert [event.available_at for event in result.events] == sorted(
        event.available_at for event in result.events
    )
    ready = [
        event for event in result.events if event.kind == M4EventKind.READY_PAYLOAD
    ]
    assert len(ready) == 1
    assert any(
        event.kind == M4EventKind.FACT and event.record_id == "pdl-100"
        for event in result.events
    )
    paths = result.export_jsonl(tmp_path / "m4-audit")
    assert all(path.exists() for path in paths.values())
    assert len(paths["events"].read_text(encoding="utf-8").splitlines()) == len(
        result.events
    )


def test_m4_replay_keeps_late_reclaim_as_near_miss() -> None:
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
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        **engine_identity(),
        initial_facts=[
            reference(
                "pdl-100", FactType.PREVIOUS_DAY_LEVEL, Timeframe.D1, "low", 100.0
            )
        ],
        candle_config=CandleFeatureConfig(baseline_period=3),
        m3_policy=M3Policy(setup_timeframes=(Timeframe.M5,)),
    )

    result = engine.run(
        [record(item, index + 2) for index, item in enumerate(bars)],
        study_window=study(),
    )

    late = [
        item
        for item in result.near_misses
        if item.reason_code == "RECLAIM_OUTSIDE_WINDOW"
    ]
    assert len(late) == 1
    assert late[0].distance_bars == 4
    assert late[0].threshold_bars == 3
    assert late[0].excess_bars == 1
    assert result.summary.near_misses_by_reason["RECLAIM_OUTSIDE_WINDOW"] == 1


def test_m4_replay_audits_expiry_and_late_shift_without_reopening_setup() -> None:
    bars = ready_bars()[:4] + [
        bar(4, open_=101.0, high=103.0, low=100.5, close=102.0),
        bar(5, open_=103.9, high=104.5, low=103.8, close=104.2),
    ]
    policy = M3Policy(
        setup_timeframes=(Timeframe.M5,),
        shift_window_bars={Timeframe.M5: 1, Timeframe.M15: 8, Timeframe.H1: 4},
        fvg_expiry_bars={Timeframe.M5: 24, Timeframe.M15: 16, Timeframe.H1: 6},
    )
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        **engine_identity(),
        initial_facts=[
            reference(
                "pdl-100", FactType.PREVIOUS_DAY_LEVEL, Timeframe.D1, "low", 100.0
            ),
            reference("m5-high-104", FactType.SWING_POINT, Timeframe.M5, "high", 104.0),
        ],
        candle_config=CandleFeatureConfig(baseline_period=3),
        m3_policy=policy,
    )

    result = engine.run(
        [record(item, index + 2) for index, item in enumerate(bars)],
        study_window=study(),
    )

    assert result.summary.expired_setups == 1
    assert result.summary.late_shifts == 1
    late = [
        item
        for item in result.near_misses
        if item.reason_code == "LATE_SHIFT_AFTER_TERMINAL"
    ]
    assert len(late) == 1
    assert late[0].distance_bars == 2
    assert late[0].threshold_bars == 1
    assert late[0].excess_bars == 1
    assert result.summary.setups_by_status == {SetupStatus.EXPIRED.value: 1}


def test_same_close_processes_lower_timeframe_before_higher_timeframe() -> None:
    m5 = [bar(i, open_=100.0, high=100.5, low=99.5, close=100.1) for i in range(12)]
    m15 = [
        bar(
            i,
            open_=100.0,
            high=100.5,
            low=99.5,
            close=100.1,
            timeframe=Timeframe.M15,
        )
        for i in range(4)
    ]
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        **engine_identity(),
        candle_config=CandleFeatureConfig(baseline_period=1),
    )

    result = engine.run(
        [
            *(record(item, index + 2) for index, item in enumerate(m5)),
            *(record(item, index + 100) for index, item in enumerate(m15)),
        ],
        study_window=study(),
    )

    shared_close = T0 + timedelta(hours=1)
    step = next(item for item in result.steps if item.as_of == shared_close)
    assert step.processed_timeframes == [Timeframe.M5, Timeframe.M15]
