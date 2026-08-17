from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from ict_trading_agent.enums import FactType, SwingRank, Timeframe
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.swing_hierarchy import SwingHierarchyPromoter

T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


def swing(
    index: int,
    price: float,
    *,
    side: str = "high",
    timeframe: Timeframe = Timeframe.M5,
) -> ObservableFact:
    occurred_at = T0 + timedelta(minutes=5 * index)
    return ObservableFact(
        fact_id=f"swing-{timeframe.value}-{side}-{index}",
        fact_type=FactType.SWING_POINT,
        symbol="XAUUSD",
        timeframe=timeframe,
        occurred_at=occurred_at,
        confirmed_at=occurred_at + timedelta(minutes=5),
        available_at=occurred_at + timedelta(minutes=5),
        geometry=PriceGeometry(price=price),
        metrics={"side": side, "rank": SwingRank.SHORT_TERM.value},
        detector_name="fixture",
        detector_version="0.1.0",
    )


def signature(fact: ObservableFact) -> tuple[object, ...]:
    return (
        fact.fact_id,
        fact.occurred_at,
        fact.available_at,
        fact.metrics["rank"],
        fact.metrics["promoted_swing_fact_id"],
        tuple(fact.source_fact_ids),
    )


def test_incremental_promoter_matches_full_history_on_nested_extremes() -> None:
    prices = [100, 104, 101, 106, 102, 105, 99, 107, 98]
    origins = [swing(index, price) for index, price in enumerate(prices)]
    expected = SwingHierarchyPromoter.detect_full_history(origins)
    promoter = SwingHierarchyPromoter()
    actual = [
        promotion
        for origin in origins
        for promotion in promoter.detect([origin])
    ]

    assert {signature(item) for item in actual} == {
        signature(item) for item in expected
    }
    assert any(
        item.metrics["rank"] == SwingRank.LONG_TERM.value for item in actual
    )


@pytest.mark.parametrize("seed", range(20))
def test_incremental_promoter_matches_full_history_for_random_stream(seed: int) -> None:
    rng = Random(seed)
    origins: list[ObservableFact] = []
    for index in range(80):
        side = "high" if index % 2 == 0 else "low"
        timeframe = Timeframe.M5 if index % 3 else Timeframe.M15
        origins.append(
            swing(
                index,
                100.0 + rng.uniform(-10.0, 10.0),
                side=side,
                timeframe=timeframe,
            )
        )
    expected = SwingHierarchyPromoter.detect_full_history(origins)
    promoter = SwingHierarchyPromoter()
    actual = [
        promotion
        for origin in origins
        for promotion in promoter.detect([origin])
    ]

    assert {signature(item) for item in actual} == {
        signature(item) for item in expected
    }
