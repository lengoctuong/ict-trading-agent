from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from heapq import heappop, heappush

from pydantic import AwareDatetime

from .detectors.common import stable_fact_id
from .enums import FactType, SwingRank, SwingSide
from .facts import ObservableFact, PriceGeometry

_RANK_ORDER = {
    SwingRank.SHORT_TERM: 0,
    SwingRank.INTERMEDIATE: 1,
    SwingRank.LONG_TERM: 2,
}
_PROMOTION_TARGET = {
    SwingRank.SHORT_TERM: SwingRank.INTERMEDIATE,
    SwingRank.INTERMEDIATE: SwingRank.LONG_TERM,
}


def effective_swing_rank(
    reference_fact_id: str,
    facts: Iterable[ObservableFact],
    *,
    as_of: AwareDatetime,
) -> SwingRank:
    """Resolve the highest append-only promotion visible at break time."""

    rank = SwingRank.SHORT_TERM
    for fact in facts:
        if fact.available_at > as_of:
            continue
        is_origin = (
            fact.fact_id == reference_fact_id and fact.fact_type == FactType.SWING_POINT
        )
        is_promotion = (
            fact.fact_type == FactType.SWING_PROMOTION
            and fact.metrics.get("promoted_swing_fact_id") == reference_fact_id
        )
        if not (is_origin or is_promotion):
            continue
        candidate_rank = SwingRank(str(fact.metrics.get("rank", rank.value)))
        if _RANK_ORDER[candidate_rank] > _RANK_ORDER[rank]:
            rank = candidate_rank
    return rank


def _is_swing_source(fact: ObservableFact, rank: SwingRank) -> bool:
    return (
        fact.fact_type in {FactType.SWING_POINT, FactType.SWING_PROMOTION}
        and fact.metrics.get("rank") == rank.value
        and fact.geometry is not None
        and fact.geometry.price is not None
        and fact.metrics.get("side") in {SwingSide.HIGH.value, SwingSide.LOW.value}
    )


def _promotion_fact(
    left: ObservableFact,
    middle: ObservableFact,
    right: ObservableFact,
    *,
    source_rank: SwingRank,
    target_rank: SwingRank,
    detector_name: str,
    detector_version: str,
) -> ObservableFact | None:
    assert left.geometry is not None and left.geometry.price is not None
    assert middle.geometry is not None and middle.geometry.price is not None
    assert right.geometry is not None and right.geometry.price is not None
    prices = [float(item.geometry.price) for item in (left, middle, right)]
    side_value = str(middle.metrics["side"])
    is_extreme = (
        prices[1] > prices[0] and prices[1] > prices[2]
        if side_value == SwingSide.HIGH.value
        else prices[1] < prices[0] and prices[1] < prices[2]
    )
    if not is_extreme:
        return None
    return ObservableFact(
        fact_id=stable_fact_id(
            FactType.SWING_PROMOTION.value,
            middle.fact_id,
            target_rank.value,
        ),
        fact_type=FactType.SWING_PROMOTION,
        symbol=middle.symbol,
        timeframe=middle.timeframe,
        occurred_at=middle.occurred_at,
        confirmed_at=right.available_at,
        available_at=right.available_at,
        geometry=PriceGeometry(price=prices[1]),
        source_fact_ids=[left.fact_id, middle.fact_id, right.fact_id],
        metrics={
            "side": side_value,
            "rank": target_rank.value,
            "promoted_from_rank": source_rank.value,
            "promoted_swing_fact_id": middle.metrics.get(
                "promoted_swing_fact_id", middle.fact_id
            ),
        },
        detector_name=detector_name,
        detector_version=detector_version,
    )


class SwingHierarchyPromoter:
    """Incremental append-only STH/STL -> ITH/ITL -> LTH/LTL promoter."""

    name = "SwingHierarchyPromoter"
    version = "0.2.0"

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._rolling: dict[
            tuple[str, object, str, SwingRank], deque[ObservableFact]
        ] = defaultdict(lambda: deque(maxlen=3))

    @property
    def initialized(self) -> bool:
        return bool(self._seen_ids)

    def detect(
        self,
        facts: Iterable[ObservableFact],
    ) -> tuple[ObservableFact, ...]:
        """Consume only unseen facts and retain three nodes per hierarchy key."""

        incoming = list(facts)
        known_input_ids = {fact.fact_id for fact in incoming}
        queue: list[tuple[object, object, str, ObservableFact]] = []
        for fact in incoming:
            if fact.fact_id not in self._seen_ids:
                heappush(
                    queue,
                    (fact.available_at, fact.occurred_at, fact.fact_id, fact),
                )
        produced: list[ObservableFact] = []
        while queue:
            _, _, _, fact = heappop(queue)
            if fact.fact_id in self._seen_ids:
                continue
            self._seen_ids.add(fact.fact_id)
            for source_rank, target_rank in _PROMOTION_TARGET.items():
                if not _is_swing_source(fact, source_rank):
                    continue
                key = (
                    fact.symbol,
                    fact.timeframe,
                    str(fact.metrics["side"]),
                    source_rank,
                )
                rolling = self._rolling[key]
                rolling.append(fact)
                if len(rolling) < 3:
                    continue
                promotion = _promotion_fact(
                    *rolling,
                    source_rank=source_rank,
                    target_rank=target_rank,
                    detector_name=self.name,
                    detector_version=self.version,
                )
                if promotion is None or promotion.fact_id in self._seen_ids:
                    continue
                if promotion.fact_id in known_input_ids:
                    continue
                produced.append(promotion)
                heappush(
                    queue,
                    (
                        promotion.available_at,
                        promotion.occurred_at,
                        promotion.fact_id,
                        promotion,
                    ),
                )
        return tuple(
            sorted(
                produced,
                key=lambda fact: (
                    _RANK_ORDER[SwingRank(str(fact.metrics["rank"]))],
                    fact.occurred_at,
                    fact.fact_id,
                ),
            )
        )

    @classmethod
    def detect_full_history(
        cls,
        facts: Iterable[ObservableFact],
    ) -> tuple[ObservableFact, ...]:
        """Reference implementation retained for semantic equivalence tests."""

        history = list(facts)
        known_ids = {fact.fact_id for fact in history}
        produced: list[ObservableFact] = []
        for source_rank, target_rank in _PROMOTION_TARGET.items():
            source = [
                fact
                for fact in [*history, *produced]
                if _is_swing_source(fact, source_rank)
            ]
            groups: dict[tuple[str, object, str], list[ObservableFact]] = {}
            for fact in source:
                side = str(fact.metrics["side"])
                groups.setdefault((fact.symbol, fact.timeframe, side), []).append(fact)
            for items in groups.values():
                items.sort(key=lambda fact: (fact.occurred_at, fact.fact_id))
                for index in range(1, len(items) - 1):
                    promotion = _promotion_fact(
                        *items[index - 1 : index + 2],
                        source_rank=source_rank,
                        target_rank=target_rank,
                        detector_name=cls.name,
                        detector_version=cls.version,
                    )
                    if promotion is None:
                        continue
                    if promotion.fact_id in known_ids or any(
                        fact.fact_id == promotion.fact_id for fact in produced
                    ):
                        continue
                    produced.append(promotion)
        return tuple(produced)
