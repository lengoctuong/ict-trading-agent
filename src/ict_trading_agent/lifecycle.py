from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import SetupStatus

ALLOWED_SETUP_TRANSITIONS: dict[SetupStatus, frozenset[SetupStatus]] = {
    SetupStatus.DETECTED: frozenset(
        {SetupStatus.FORMING, SetupStatus.INVALIDATED, SetupStatus.EXPIRED}
    ),
    SetupStatus.FORMING: frozenset(
        {SetupStatus.READY_FOR_LLM, SetupStatus.INVALIDATED, SetupStatus.EXPIRED}
    ),
    SetupStatus.READY_FOR_LLM: frozenset(
        {
            SetupStatus.ACCEPTED,
            SetupStatus.REJECTED,
            SetupStatus.INVALIDATED,
            SetupStatus.EXPIRED,
        }
    ),
    SetupStatus.ACCEPTED: frozenset(
        {
            SetupStatus.ENTERED,
            SetupStatus.RISK_REJECTED,
            SetupStatus.INVALIDATED,
            SetupStatus.EXPIRED,
        }
    ),
    SetupStatus.ENTERED: frozenset({SetupStatus.CLOSED}),
    SetupStatus.REJECTED: frozenset(),
    SetupStatus.CLOSED: frozenset(),
    SetupStatus.INVALIDATED: frozenset(),
    SetupStatus.EXPIRED: frozenset(),
    SetupStatus.RISK_REJECTED: frozenset(),
}


def can_transition_setup(current: SetupStatus, target: SetupStatus) -> bool:
    return target in ALLOWED_SETUP_TRANSITIONS[current]


def assert_setup_transition(current: SetupStatus, target: SetupStatus) -> None:
    if not can_transition_setup(current, target):
        raise ValueError(f"invalid setup transition: {current.value} -> {target.value}")


class SetupTransition(SchemaModel):
    """Immutable lifecycle status change in a setup episode."""

    transition_id: NonEmptyStr
    setup_candidate_id: NonEmptyStr
    from_status: SetupStatus | None = None
    to_status: SetupStatus
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    evidence_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    evidence_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    entry_zone_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    hard_invalidation_price: float | None = Field(default=None, gt=0.0)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    expires_at: AwareDatetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transition(self) -> "SetupTransition":
        if self.available_at < self.occurred_at:
            raise ValueError("transition availability cannot precede occurrence")
        if self.from_status is not None:
            assert_setup_transition(self.from_status, self.to_status)
        if self.expires_at is not None and self.expires_at <= self.available_at:
            raise ValueError("transition expiry must follow availability")
        return self


class SetupEvidenceLink(SchemaModel):
    """Append-only evidence merged into a setup without changing its status."""

    evidence_link_id: NonEmptyStr
    setup_candidate_id: NonEmptyStr
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    evidence_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    evidence_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    entry_zone_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_link(self) -> "SetupEvidenceLink":
        if self.available_at < self.occurred_at:
            raise ValueError("evidence availability cannot precede occurrence")
        if not (
            self.evidence_candidate_ids
            or self.evidence_fact_ids
            or self.entry_zone_candidate_ids
            or self.metrics
        ):
            raise ValueError("an evidence link cannot be empty")
        return self
