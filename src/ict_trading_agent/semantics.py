from __future__ import annotations

from pydantic import AwareDatetime, Field

from .base import NonEmptyStr, SchemaModel
from .enums import Direction, SemanticAction, SemanticClass


class CandidateAssessment(SchemaModel):
    candidate_id: NonEmptyStr
    classification: NonEmptyStr
    semantic_class: SemanticClass
    quality: float = Field(ge=0.0, le=1.0)
    supporting_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    conflicting_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)


class SemanticAssessment(SchemaModel):
    assessment_id: NonEmptyStr
    symbol: NonEmptyStr
    as_of: AwareDatetime
    candidate_assessments: list[CandidateAssessment]
    multi_timeframe_coherence: float = Field(ge=0.0, le=1.0)
    effective_direction: Direction | None = None
    selected_dol_candidate_id: str | None = None
    overall_context_score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    reasoning_summary: list[NonEmptyStr] = Field(default_factory=list)
    model: NonEmptyStr
    model_version: NonEmptyStr | None = None
    prompt_version: NonEmptyStr
    temperature: float | None = Field(default=None, ge=0.0)
    input_state_hash: NonEmptyStr
    created_at: AwareDatetime
    knowledge_version: NonEmptyStr | None = None


class SetupSemanticDecision(SchemaModel):
    setup_candidate_id: NonEmptyStr
    action: SemanticAction
    context_score: float = Field(ge=0.0, le=1.0)
    selected_target_candidate_id: str | None = None
    supporting_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    conflicting_candidate_ids: list[NonEmptyStr] = Field(default_factory=list)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    reasoning_summary: list[NonEmptyStr] = Field(default_factory=list)
    model: NonEmptyStr
    model_version: NonEmptyStr | None = None
    prompt_version: NonEmptyStr
    temperature: float | None = Field(default=None, ge=0.0)
    input_state_hash: NonEmptyStr
    created_at: AwareDatetime
    knowledge_version: NonEmptyStr | None = None
