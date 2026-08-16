from __future__ import annotations

from collections.abc import Iterable

from pydantic import AwareDatetime

from .detectors.common import stable_fact_id
from .enums import FactType, StructureReferenceStatus
from .facts import ObservableFact


class StructureLifecycleTracker:
    """Tracks close-based structural consumption independently of liquidity."""

    name = "StructureLifecycleTracker"
    version = "0.1.0"

    def status(
        self,
        reference_fact_id: str,
        facts: Iterable[ObservableFact],
        *,
        as_of: AwareDatetime,
    ) -> StructureReferenceStatus:
        latest: ObservableFact | None = None
        for fact in facts:
            if fact.fact_type != FactType.STRUCTURE_STATE:
                continue
            if fact.available_at > as_of:
                continue
            if fact.metrics.get("reference_fact_id") != reference_fact_id:
                continue
            if latest is None or fact.available_at > latest.available_at:
                latest = fact
        if latest is None:
            return StructureReferenceStatus.ACTIVE
        return StructureReferenceStatus(str(latest.metrics["status"]))

    def is_eligible(
        self,
        reference_fact_id: str,
        facts: Iterable[ObservableFact],
        *,
        as_of: AwareDatetime,
    ) -> bool:
        return (
            self.status(reference_fact_id, facts, as_of=as_of)
            == StructureReferenceStatus.ACTIVE
        )

    def broken_observation(
        self,
        reference: ObservableFact,
        price_break: ObservableFact,
    ) -> ObservableFact:
        if reference.fact_type != FactType.SWING_POINT:
            raise ValueError("only a swing point can become structurally broken")
        if price_break.fact_type != FactType.PRICE_BREAK:
            raise ValueError("broken transition requires a price-break fact")
        if not price_break.metrics.get("same_timeframe_structure_eligible"):
            raise ValueError("cross-timeframe close cannot break reference structure")
        if price_break.metrics.get("reference_fact_id") != reference.fact_id:
            raise ValueError("price break does not belong to the swing reference")
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.STRUCTURE_STATE.value,
                reference.fact_id,
                price_break.fact_id,
                StructureReferenceStatus.BROKEN.value,
            ),
            fact_type=FactType.STRUCTURE_STATE,
            symbol=price_break.symbol,
            timeframe=reference.timeframe,
            occurred_at=price_break.occurred_at,
            confirmed_at=price_break.confirmed_at,
            available_at=price_break.available_at,
            geometry=(
                price_break.geometry.model_copy(deep=True)
                if price_break.geometry is not None
                else None
            ),
            source_fact_ids=[reference.fact_id, price_break.fact_id],
            metrics={
                "reference_fact_id": reference.fact_id,
                "previous_status": StructureReferenceStatus.ACTIVE.value,
                "status": StructureReferenceStatus.BROKEN.value,
                "break_fact_id": price_break.fact_id,
            },
            detector_name=self.name,
            detector_version=self.version,
        )

    def superseded_observation(
        self,
        reference: ObservableFact,
        successor: ObservableFact,
    ) -> ObservableFact:
        """Record an explicit policy decision without mutating either swing."""

        if reference.fact_type != FactType.SWING_POINT:
            raise ValueError("only a swing point can be structurally superseded")
        if successor.fact_type != FactType.SWING_POINT:
            raise ValueError("structural successor must be a swing point")
        if (
            reference.symbol != successor.symbol
            or reference.timeframe != successor.timeframe
        ):
            raise ValueError("structural successor must share symbol/timeframe")
        if reference.metrics.get("side") != successor.metrics.get("side"):
            raise ValueError("structural successor must use the same swing side")
        if successor.available_at < reference.available_at:
            raise ValueError("structural successor cannot predate its reference")
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.STRUCTURE_STATE.value,
                reference.fact_id,
                successor.fact_id,
                StructureReferenceStatus.SUPERSEDED.value,
            ),
            fact_type=FactType.STRUCTURE_STATE,
            symbol=reference.symbol,
            timeframe=reference.timeframe,
            occurred_at=successor.occurred_at,
            confirmed_at=successor.confirmed_at,
            available_at=successor.available_at,
            geometry=(
                reference.geometry.model_copy(deep=True)
                if reference.geometry is not None
                else None
            ),
            source_fact_ids=[reference.fact_id, successor.fact_id],
            metrics={
                "reference_fact_id": reference.fact_id,
                "successor_fact_id": successor.fact_id,
                "previous_status": StructureReferenceStatus.ACTIVE.value,
                "status": StructureReferenceStatus.SUPERSEDED.value,
            },
            detector_name=self.name,
            detector_version=self.version,
        )
