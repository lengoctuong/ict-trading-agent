from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import (
    CandidateType,
    Direction,
    SetupStatus,
    Session,
    TargetScope,
    TargetSide,
    TargetType,
    Timeframe,
)


class ConceptCandidate(SchemaModel):
    candidate_id: NonEmptyStr
    candidate_type: CandidateType
    symbol: NonEmptyStr
    timeframe: Timeframe | None = None
    direction: Direction | None = None
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    evidence_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    related_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    raw_features: dict[str, Any] = Field(default_factory=dict)
    machine_labels: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_candidate(self) -> "ConceptCandidate":
        if self.available_at < self.occurred_at:
            raise ValueError("available_at cannot precede occurred_at")
        if self.candidate_id in self.related_candidate_ids:
            raise ValueError("a candidate cannot relate to itself")
        return self


class TargetCandidate(SchemaModel):
    candidate_id: NonEmptyStr
    symbol: NonEmptyStr
    price: float = Field(gt=0.0)
    side: TargetSide
    target_type: TargetType
    scope: TargetScope
    session: Session | None = None
    source_timeframe: Timeframe | None = None
    source_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    available_at: AwareDatetime
    already_taken: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_session_target(self) -> "TargetCandidate":
        is_session_target = self.target_type in {
            TargetType.SESSION_HIGH,
            TargetType.SESSION_LOW,
        }
        if is_session_target and self.session in {None, Session.OFF_SESSION}:
            raise ValueError("session high/low targets require a concrete session")
        if not is_session_target and self.session is not None:
            raise ValueError("session metadata is only valid for session targets")
        return self


class SetupCandidate(SchemaModel):
    setup_candidate_id: NonEmptyStr
    setup_type: NonEmptyStr
    setup_version: NonEmptyStr
    symbol: NonEmptyStr
    direction: Direction
    setup_timeframe: Timeframe
    entry_timeframe: Timeframe
    created_at: AwareDatetime
    available_at: AwareDatetime
    status: SetupStatus
    evidence_candidate_ids: list[NonEmptyStr]
    evidence_fact_ids: list[NonEmptyStr]
    entry_zone_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    target_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    hard_invalidation_price: float | None = Field(default=None, gt=0.0)
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_setup_candidate(self) -> "SetupCandidate":
        if self.direction == Direction.NEUTRAL:
            raise ValueError("a setup candidate direction cannot be neutral")
        if self.available_at < self.created_at:
            raise ValueError("available_at cannot precede created_at")
        if self.expires_at is not None and self.expires_at <= self.available_at:
            raise ValueError("expires_at must be after available_at")
        return self
