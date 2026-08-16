from __future__ import annotations

from collections.abc import Iterable

from pydantic import AwareDatetime

from .base import SchemaModel
from .detectors.common import stable_fact_id
from .detectors.levels import ReferenceLevel
from .enums import FactType, ReferenceStatus, Timeframe
from .facts import ObservableFact


class ReferenceLifecyclePolicy(SchemaModel):
    """Explicitly controls whether a taken level may create later events."""

    reuse_taken_levels: bool = False


class ReferenceLifecycleTracker:
    name = "ReferenceLifecycleTracker"
    version = "0.1.0"

    def __init__(self, policy: ReferenceLifecyclePolicy | None = None) -> None:
        self.policy = policy or ReferenceLifecyclePolicy()

    def status(
        self,
        reference_fact_id: str,
        facts: Iterable[ObservableFact],
        *,
        as_of: AwareDatetime,
        detection_timeframe: Timeframe | None = None,
    ) -> ReferenceStatus:
        for fact in facts:
            if fact.available_at > as_of:
                continue
            if fact.metrics.get("reference_fact_id") != reference_fact_id:
                continue
            if (
                detection_timeframe is not None
                and fact.timeframe != detection_timeframe
            ):
                continue
            if fact.fact_type in {FactType.LEVEL_BREACH, FactType.REFERENCE_STATE}:
                return ReferenceStatus.TAKEN
        return ReferenceStatus.ACTIVE

    def is_eligible(
        self,
        reference_fact_id: str,
        facts: Iterable[ObservableFact],
        *,
        as_of: AwareDatetime,
        detection_timeframe: Timeframe | None = None,
    ) -> bool:
        if self.policy.reuse_taken_levels:
            return True
        return (
            self.status(
                reference_fact_id,
                facts,
                as_of=as_of,
                detection_timeframe=detection_timeframe,
            )
            == ReferenceStatus.ACTIVE
        )

    def taken_observation(
        self,
        reference: ReferenceLevel,
        breach: ObservableFact,
    ) -> ObservableFact:
        if breach.fact_type != FactType.LEVEL_BREACH:
            raise ValueError("taken transition requires a level-breach fact")
        if breach.metrics.get("reference_fact_id") != reference.reference_fact_id:
            raise ValueError("breach does not belong to the reference level")
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.REFERENCE_STATE.value,
                reference.reference_fact_id,
                breach.fact_id,
                ReferenceStatus.TAKEN.value,
            ),
            fact_type=FactType.REFERENCE_STATE,
            symbol=breach.symbol,
            timeframe=breach.timeframe,
            occurred_at=breach.occurred_at,
            confirmed_at=breach.confirmed_at,
            available_at=breach.available_at,
            geometry=(
                breach.geometry.model_copy(deep=True)
                if breach.geometry is not None
                else None
            ),
            source_fact_ids=[reference.reference_fact_id, breach.fact_id],
            metrics={
                "reference_fact_id": reference.reference_fact_id,
                "previous_status": ReferenceStatus.ACTIVE.value,
                "status": ReferenceStatus.TAKEN.value,
            },
            detector_name=self.name,
            detector_version=self.version,
        )
