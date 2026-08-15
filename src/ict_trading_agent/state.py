from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .candidates import ConceptCandidate
from .enums import Session, Timeframe, TimeframeRole
from .facts import ObservableFact


class TemporalContext(SchemaModel):
    trading_day: NonEmptyStr
    session: Session
    subsession: str | None = None
    minutes_from_session_open: int | None = Field(default=None, ge=0)
    ny_time: AwareDatetime


class TimeframeState(SchemaModel):
    timeframe: Timeframe
    role: TimeframeRole
    last_closed_bar_at: AwareDatetime
    active_swing_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    active_fvg_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    active_liquidity_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    latest_structure_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)


class MarketState(SchemaModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        use_enum_values=False,
    )

    state_id: NonEmptyStr
    symbol: NonEmptyStr
    as_of: AwareDatetime
    temporal: TemporalContext
    timeframes: dict[Timeframe, TimeframeState]
    visible_fact_ids: list[NonEmptyStr]
    visible_candidate_ids: list[NonEmptyStr]
    target_candidate_ids: list[NonEmptyStr]
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeframe_keys(self) -> "MarketState":
        for key, value in self.timeframes.items():
            if key != value.timeframe:
                raise ValueError(
                    f"timeframe key {key.value} does not match state {value.timeframe.value}"
                )
            if value.last_closed_bar_at > self.as_of:
                raise ValueError("last_closed_bar_at cannot be after state.as_of")
        return self

    def assert_point_in_time_visibility(
        self,
        facts: Mapping[str, ObservableFact],
        candidates: Mapping[str, ConceptCandidate],
    ) -> None:
        """Enforce the canonical available_at <= as_of invariant."""

        for fact_id in self.visible_fact_ids:
            if fact_id not in facts:
                raise ValueError(f"visible fact not supplied: {fact_id}")
            if facts[fact_id].available_at > self.as_of:
                raise ValueError(f"fact {fact_id} is not available at state.as_of")
        for candidate_id in self.visible_candidate_ids:
            if candidate_id not in candidates:
                raise ValueError(f"visible candidate not supplied: {candidate_id}")
            if candidates[candidate_id].available_at > self.as_of:
                raise ValueError(
                    f"candidate {candidate_id} is not available at state.as_of"
                )
