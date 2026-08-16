from __future__ import annotations

from collections.abc import Iterable

from .detectors.common import stable_fact_id
from .enums import FactType, SwingRank, SwingSide
from .facts import ObservableFact, PriceGeometry


class SwingHierarchyPromoter:
    """Append-only STH/STL -> ITH/ITL -> LTH/LTL promotion observations."""

    name = "SwingHierarchyPromoter"
    version = "0.1.0"

    def detect(
        self,
        facts: Iterable[ObservableFact],
    ) -> tuple[ObservableFact, ...]:
        history = list(facts)
        known_ids = {fact.fact_id for fact in history}
        produced: list[ObservableFact] = []
        for source_rank, target_rank in (
            (SwingRank.SHORT_TERM, SwingRank.INTERMEDIATE),
            (SwingRank.INTERMEDIATE, SwingRank.LONG_TERM),
        ):
            source = [
                fact
                for fact in [*history, *produced]
                if fact.fact_type in {FactType.SWING_POINT, FactType.SWING_PROMOTION}
                and fact.metrics.get("rank") == source_rank.value
                and fact.geometry is not None
                and fact.geometry.price is not None
            ]
            groups: dict[tuple[str, object, str], list[ObservableFact]] = {}
            for fact in source:
                side = str(fact.metrics.get("side"))
                groups.setdefault((fact.symbol, fact.timeframe, side), []).append(fact)
            for (_, _, side_value), items in groups.items():
                items.sort(key=lambda fact: (fact.occurred_at, fact.fact_id))
                for index in range(1, len(items) - 1):
                    left, middle, right = items[index - 1 : index + 2]
                    prices = [
                        float(item.geometry.price) for item in (left, middle, right)
                    ]
                    is_extreme = (
                        prices[1] > prices[0] and prices[1] > prices[2]
                        if side_value == SwingSide.HIGH.value
                        else prices[1] < prices[0] and prices[1] < prices[2]
                    )
                    if not is_extreme:
                        continue
                    fact_id = stable_fact_id(
                        FactType.SWING_PROMOTION.value,
                        middle.fact_id,
                        target_rank.value,
                    )
                    if fact_id in known_ids or any(
                        fact.fact_id == fact_id for fact in produced
                    ):
                        continue
                    produced.append(
                        ObservableFact(
                            fact_id=fact_id,
                            fact_type=FactType.SWING_PROMOTION,
                            symbol=middle.symbol,
                            timeframe=middle.timeframe,
                            occurred_at=middle.occurred_at,
                            confirmed_at=right.available_at,
                            available_at=right.available_at,
                            geometry=PriceGeometry(price=prices[1]),
                            source_fact_ids=[
                                left.fact_id,
                                middle.fact_id,
                                right.fact_id,
                            ],
                            metrics={
                                "side": side_value,
                                "rank": target_rank.value,
                                "promoted_from_rank": source_rank.value,
                                "promoted_swing_fact_id": middle.metrics.get(
                                    "promoted_swing_fact_id",
                                    middle.fact_id,
                                ),
                            },
                            detector_name=self.name,
                            detector_version=self.version,
                        )
                    )
        return tuple(produced)
