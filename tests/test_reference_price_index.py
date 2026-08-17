from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

from ict_trading_agent.detectors.levels import (
    LevelInteractionDetector,
    ReferenceLevel,
)
from ict_trading_agent.enums import FactType, Timeframe
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.market import OHLCBar
from ict_trading_agent.reference_lifecycle import ReferenceLifecycleTracker
from ict_trading_agent.stores import FactStore

T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


def reference(index: int, price: float, side: str) -> ObservableFact:
    timeframe = (Timeframe.M5, Timeframe.M15, Timeframe.H1)[index % 3]
    return ObservableFact(
        fact_id=f"reference-{index}",
        fact_type=(
            FactType.SWING_POINT if index % 2 else FactType.PREVIOUS_DAY_LEVEL
        ),
        symbol="XAUUSD",
        timeframe=timeframe,
        occurred_at=T0 - timedelta(hours=2),
        confirmed_at=T0 - timedelta(hours=1),
        available_at=T0 - timedelta(hours=1),
        geometry=PriceGeometry(price=price),
        metrics={"side": side, "rank": "short_term"},
        detector_name="fixture",
        detector_version="0.1.0",
    )


def test_price_index_matches_full_scan_and_keeps_cross_timeframe_levels() -> None:
    rng = Random(7)
    references = [
        reference(index, round(90 + rng.random() * 20, 2), "high" if index % 2 else "low")
        for index in range(200)
    ]
    store = FactStore()
    store.extend(references)
    bar = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        open_time=T0,
        close_time=T0 + timedelta(minutes=5),
        open=100.0,
        high=103.25,
        low=97.75,
        close=101.0,
    )
    detector = LevelInteractionDetector(tick_size=0.01)
    expected = {
        fact.fact_id
        for fact in references
        if detector.detect(bar, ReferenceLevel.from_fact(fact))
    }
    actual = {
        fact.fact_id
        for fact in store.active_liquidity_reference_views_for_bar(
            symbol=bar.symbol,
            low=bar.low,
            high=bar.high,
            as_of=bar.open_time,
        )
    }

    assert actual == expected
    assert {fact.timeframe for fact in references if fact.fact_id in actual} == {
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
    }


def test_liquidity_taken_and_structure_inactive_lifecycles_are_independent() -> None:
    swing = reference(1, 100.0, "high")
    store = FactStore()
    store.append(swing)
    bar = OHLCBar(
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        open_time=T0,
        close_time=T0 + timedelta(minutes=5),
        open=99.5,
        high=101.0,
        low=99.0,
        close=99.8,
    )
    breach = LevelInteractionDetector(tick_size=0.1).detect(
        bar, ReferenceLevel.from_fact(swing)
    )[0]
    store.append(breach)
    store.append(
        ReferenceLifecycleTracker().taken_observation(
            ReferenceLevel.from_fact(swing), breach
        )
    )

    assert store.active_liquidity_reference_views_for_bar(
        symbol="XAUUSD",
        low=99.0,
        high=101.0,
        as_of=bar.close_time,
    ) == ()
    assert [
        fact.fact_id
        for fact in store.active_structure_reference_views_for_close(
            symbol="XAUUSD",
            close=101.0,
            as_of=bar.close_time,
        )
    ] == [swing.fact_id]
