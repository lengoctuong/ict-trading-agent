from __future__ import annotations

from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .base import MetricMap, NonEmptyStr, SchemaModel
from .enums import (
    ConceptKind,
    CriterionStage,
    FormalizationLevel,
    HoldingHorizon,
    ParameterDType,
    RuleOperator,
    RuleSeverity,
    Session,
    SetupDirection,
    TargetType,
    Timeframe,
    TimeframeRole,
    TimeframeScope,
    TradingStyle,
)


class ParameterSpec(SchemaModel):
    name: NonEmptyStr
    description: NonEmptyStr
    dtype: ParameterDType
    default: Any | None = None
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    research_parameter: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> "ParameterSpec":
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("min_value cannot exceed max_value")
        if isinstance(self.default, (int, float)) and not isinstance(
            self.default, bool
        ):
            if self.min_value is not None and self.default < self.min_value:
                raise ValueError("default is below min_value")
            if self.max_value is not None and self.default > self.max_value:
                raise ValueError("default is above max_value")
        return self


class CriterionSpec(SchemaModel):
    id: NonEmptyStr
    stage: CriterionStage
    description: NonEmptyStr
    expression: str | None = None
    requires_future_data: bool = False


class KnowledgeReference(SchemaModel):
    source: NonEmptyStr
    path: str | None = None
    section: str | None = None


class ConceptSpec(SchemaModel):
    id: NonEmptyStr
    version: NonEmptyStr
    name: NonEmptyStr
    category: NonEmptyStr
    kind: ConceptKind
    formalization: FormalizationLevel
    timeframe_scope: TimeframeScope
    description: NonEmptyStr
    dependencies: list[NonEmptyStr] = Field(default_factory=list)
    supported_timeframes: list[Timeframe] = Field(default_factory=list)
    parameters: list[ParameterSpec] = Field(default_factory=list)
    criteria: list[CriterionSpec] = Field(default_factory=list)
    occurred_at_semantics: NonEmptyStr
    confirmed_at_semantics: NonEmptyStr
    invalidated_at_semantics: str | None = None
    knowledge_refs: list[KnowledgeReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timeframe_scope(self) -> "ConceptSpec":
        if (
            self.timeframe_scope == TimeframeScope.BAR_BASED
            and not self.supported_timeframes
        ):
            raise ValueError("bar_based concepts require supported_timeframes")
        return self


class ConceptUsageSpec(SchemaModel):
    concept_id: NonEmptyStr
    timeframe: Timeframe
    role: TimeframeRole
    enabled: bool = True
    uses: list[NonEmptyStr] = Field(default_factory=list)
    hard_requirement: bool = False
    detector_parameters: MetricMap = Field(default_factory=dict)


class SetupRuleSpec(SchemaModel):
    id: NonEmptyStr
    description: NonEmptyStr
    severity: RuleSeverity
    concept_id: str | None = None
    timeframe: Timeframe | None = None
    operator: RuleOperator
    expected: Any | None = None
    expression: str | None = None

    @model_validator(mode="after")
    def validate_custom_expression(self) -> "SetupRuleSpec":
        if self.operator == RuleOperator.CUSTOM and not self.expression:
            raise ValueError("custom rules require expression")
        return self


class SetupSpec(SchemaModel):
    id: NonEmptyStr
    version: NonEmptyStr
    name: NonEmptyStr
    description: NonEmptyStr
    direction: SetupDirection
    allowed_setup_timeframes: list[Timeframe] = Field(min_length=1)
    allowed_entry_timeframes: list[Timeframe] = Field(min_length=1)
    rules: list[SetupRuleSpec] = Field(default_factory=list)
    target_policy_id: NonEmptyStr
    risk_policy_id: NonEmptyStr
    enabled: bool = True


class HoldingPolicy(SchemaModel):
    overnight: bool = False
    max_horizon: HoldingHorizon = HoldingHorizon.TRADING_DAY


class TimeframeHierarchy(SchemaModel):
    macro_context: list[Timeframe] = Field(min_length=1)
    directional_bias: list[Timeframe] = Field(min_length=1)
    setup: list[Timeframe] = Field(min_length=1)
    entry: list[Timeframe] = Field(min_length=1)
    optional_refinement: list[Timeframe] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_roles(self) -> "TimeframeHierarchy":
        assigned = (
            self.macro_context
            + self.directional_bias
            + self.setup
            + self.entry
            + self.optional_refinement
        )
        if len(assigned) != len(set(assigned)):
            raise ValueError("a timeframe cannot have multiple profile roles")
        return self

    def role_map(self) -> dict[Timeframe, TimeframeRole]:
        result: dict[Timeframe, TimeframeRole] = {}
        for timeframe in self.macro_context:
            result[timeframe] = TimeframeRole.MACRO
        for timeframe in self.directional_bias:
            result[timeframe] = TimeframeRole.BIAS
        for timeframe in self.setup:
            result[timeframe] = TimeframeRole.SETUP
        for timeframe in self.entry:
            result[timeframe] = TimeframeRole.ENTRY
        for timeframe in self.optional_refinement:
            result[timeframe] = TimeframeRole.REFINEMENT
        return result


class SessionConfig(SchemaModel):
    enabled: bool = True


class SessionPolicy(SchemaModel):
    hard_filter: bool = False
    contextual_feature: bool = True


class TradingDayPolicy(SchemaModel):
    """Data-source candle-day boundary; session clocks remain separate."""

    timezone: NonEmptyStr
    rollover_local_time: time | None = None
    source_candle_timeframe: Timeframe | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def validate_boundary_source(self) -> "TradingDayPolicy":
        configured = self.rollover_local_time is not None
        feed_defined = self.source_candle_timeframe is not None
        if configured == feed_defined:
            raise ValueError(
                "choose exactly one trading-day boundary: configured rollover "
                "or source candle timeframe"
            )
        return self


class TradingProfile(SchemaModel):
    profile_id: NonEmptyStr
    version: NonEmptyStr
    instrument: NonEmptyStr
    style: TradingStyle
    holding: HoldingPolicy
    timeframes: TimeframeHierarchy
    sessions: dict[Session, SessionConfig]
    session_policy: SessionPolicy
    trading_day: TradingDayPolicy
    targets: list[TargetType] = Field(min_length=1)
    decision_core: list[NonEmptyStr] = Field(default_factory=list)
    decision_context: list[NonEmptyStr] = Field(default_factory=list)


def build_xauusd_intraday_v0(trading_day: TradingDayPolicy) -> TradingProfile:
    """Build the frozen profile without guessing the unresolved day boundary."""

    return TradingProfile(
        profile_id="profile.xauusd.intraday",
        version="0.1.0",
        instrument="XAUUSD",
        style=TradingStyle.INTRADAY,
        holding=HoldingPolicy(overnight=False),
        timeframes=TimeframeHierarchy(
            macro_context=[Timeframe.W1],
            directional_bias=[Timeframe.D1, Timeframe.H4],
            setup=[Timeframe.H1, Timeframe.M15],
            entry=[Timeframe.M5],
            optional_refinement=[Timeframe.M1],
        ),
        sessions={
            Session.ASIA: SessionConfig(enabled=True),
            Session.LONDON: SessionConfig(enabled=True),
            Session.NY_AM: SessionConfig(enabled=True),
            Session.NY_PM: SessionConfig(enabled=True),
        },
        session_policy=SessionPolicy(hard_filter=False, contextual_feature=True),
        trading_day=trading_day,
        targets=[
            TargetType.LOCAL_SWING,
            TargetType.SESSION_HIGH,
            TargetType.SESSION_LOW,
            TargetType.PREVIOUS_DAY_HIGH,
            TargetType.PREVIOUS_DAY_LOW,
            TargetType.EXTERNAL_LIQUIDITY,
        ],
        decision_core=["liquidity_event", "displacement", "structure_shift"],
        decision_context=["htf_bias", "session", "volatility_regime"],
    )


def build_exness_xauusd_intraday_v0() -> TradingProfile:
    """Exness timestamps are UTC; PDH/PDL use completed source D1 candles."""

    return build_xauusd_intraday_v0(
        TradingDayPolicy(
            timezone="UTC",
            source_candle_timeframe=Timeframe.D1,
        )
    )
