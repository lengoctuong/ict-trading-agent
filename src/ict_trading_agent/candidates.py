from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import (
    CandidateType,
    Direction,
    RaidObservationState,
    Session,
    SetupStatus,
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
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_setup_candidate(self) -> "SetupCandidate":
        if self.direction == Direction.NEUTRAL:
            raise ValueError("a setup candidate direction cannot be neutral")
        if self.available_at < self.created_at:
            raise ValueError("available_at cannot precede created_at")
        if self.expires_at is not None and self.expires_at <= self.available_at:
            raise ValueError("expires_at must be after available_at")
        return self


class RaidEpisode(SchemaModel):
    """Global liquidity-take episode shared by all observing timeframes."""

    raid_episode_id: NonEmptyStr
    reference_fact_id: NonEmptyStr
    symbol: NonEmptyStr
    direction: Direction
    created_at: AwareDatetime
    available_at: AwareDatetime
    first_take_fact_id: NonEmptyStr
    first_raid_candidate_id: NonEmptyStr | None = None
    raid_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    observation_fact_ids: list[NonEmptyStr]
    observed_timeframes: list[Timeframe]
    observation_states: dict[Timeframe, RaidObservationState]
    breached_at: dict[Timeframe, AwareDatetime] = Field(default_factory=dict)
    extreme: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_episode(self) -> "RaidEpisode":
        if self.direction == Direction.NEUTRAL:
            raise ValueError("a raid episode direction cannot be neutral")
        if self.available_at < self.created_at:
            raise ValueError("episode availability cannot precede creation")
        if (
            self.first_raid_candidate_id is not None
            and self.first_raid_candidate_id not in self.raid_candidate_ids
        ):
            raise ValueError("first raid candidate must belong to the episode")
        if not self.observation_fact_ids or not self.observed_timeframes:
            raise ValueError("a raid episode requires an initial observation")
        if set(self.observed_timeframes) != set(self.observation_states):
            raise ValueError("every observed timeframe requires an observation state")
        if not set(self.observed_timeframes).issubset(self.breached_at):
            raise ValueError("every observed timeframe requires breached_at")
        return self


class RaidEpisodeUpdate(SchemaModel):
    update_id: NonEmptyStr
    raid_episode_id: NonEmptyStr
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    observation_fact_id: NonEmptyStr
    observation_timeframe: Timeframe
    raid_candidate_id: NonEmptyStr | None = None
    observation_state: RaidObservationState
    breached_at: AwareDatetime | None = None
    extreme: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_update(self) -> "RaidEpisodeUpdate":
        if self.available_at < self.occurred_at:
            raise ValueError("episode update availability cannot precede occurrence")
        if self.breached_at is None:
            raise ValueError("raid observation updates require breached_at")
        return self
