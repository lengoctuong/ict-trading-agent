from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import FactType, SwingRank, Timeframe
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.m3 import M3Policy
from ict_trading_agent.m4 import ExnessBarRecord, M4ReplayEngine
from ict_trading_agent.m4_support import M4StudyWindow, M4SymbolMetadata
from ict_trading_agent.m42 import M42ResearchAnalyzer
from ict_trading_agent.market import OHLCBar

T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


def _bar(index: int, *, open_: float, high: float, low: float, close: float) -> OHLCBar:
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


def _reference(
    fact_id: str, fact_type: FactType, side: str, price: float
) -> ObservableFact:
    return ObservableFact(
        fact_id=fact_id,
        fact_type=fact_type,
        symbol="XAUUSD",
        timeframe=Timeframe.D1
        if fact_type == FactType.PREVIOUS_DAY_LEVEL
        else Timeframe.M5,
        occurred_at=T0 - timedelta(hours=2),
        confirmed_at=T0 - timedelta(hours=1),
        available_at=T0 - timedelta(hours=1),
        geometry=PriceGeometry(price=price),
        metrics={"side": side, "rank": SwingRank.SHORT_TERM.value},
        detector_name="fixture",
        detector_version="0.1.0",
    )


def test_m42_builds_distributions_forward_labels_and_pending_chart_queue(
    tmp_path,
) -> None:
    bars = [
        _bar(0, open_=101.0, high=102.0, low=100.5, close=101.2),
        _bar(1, open_=101.2, high=102.0, low=100.8, close=101.3),
        _bar(2, open_=101.3, high=102.0, low=100.9, close=101.4),
        _bar(3, open_=101.4, high=102.0, low=99.0, close=101.0),
        _bar(4, open_=101.0, high=105.5, low=100.8, close=105.0),
        _bar(5, open_=105.0, high=106.0, low=103.0, close=105.5),
        _bar(6, open_=104.5, high=105.0, low=102.5, close=104.5),
        _bar(7, open_=104.5, high=107.0, low=104.0, close=106.0),
        _bar(8, open_=106.0, high=108.0, low=105.0, close=107.0),
    ]
    engine = M4ReplayEngine(
        symbol="XAUUSD",
        symbol_metadata=M4SymbolMetadata(
            symbol="XAUUSD", digits=2, point=0.01, trade_tick_size=0.1
        ),
        git_commit_sha="test-revision",
        initial_facts=[
            _reference("pdl-100", FactType.PREVIOUS_DAY_LEVEL, "low", 100.0),
            _reference("m5-high-104", FactType.SWING_POINT, "high", 104.0),
        ],
        candle_config=CandleFeatureConfig(baseline_period=3),
        m3_policy=M3Policy(setup_timeframes=(Timeframe.M5,)),
    )
    replay = engine.run(
        [
            ExnessBarRecord(
                bar=item, source_name="fixture", source_row_number=index + 2
            )
            for index, item in enumerate(bars)
        ],
        study_window=M4StudyWindow(
            replay_start=T0, analysis_start=T0 + timedelta(seconds=1)
        ),
    )

    bundle = M42ResearchAnalyzer(
        tick_size=0.1,
        outcome_horizons=(1, 2),
        max_chart_samples=5,
    ).analyze(replay, bars, generated_at=bars[-1].close_time)

    assert bundle.report.replay_run_id == replay.run_id
    assert bundle.report.chart_review_status == "PENDING_USER_REVIEW"
    assert bundle.report.distributions["reclaim_span_bars"].count == 1
    assert len(bundle.outcomes) == 2
    assert all(item.mfe_ticks >= 0 for item in bundle.outcomes)
    assert bundle.chart_review_queue
    assert all(
        item.status == "PENDING_USER_REVIEW" for item in bundle.chart_review_queue
    )
    paths = bundle.export(tmp_path / "m42")
    assert all(path.exists() for path in paths.values())
