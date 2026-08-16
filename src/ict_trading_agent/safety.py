from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import HardInvalidationRuleType, InvalidationTimeframeBasis


class HardInvalidationRule(SchemaModel):
    rule_id: NonEmptyStr
    rule_type: HardInvalidationRuleType
    parameters: dict[str, Any] = Field(default_factory=dict)


class CloseAcceptancePolicy(SchemaModel):
    timeframe_basis: InvalidationTimeframeBasis = (
        InvalidationTimeframeBasis.SETUP_TIMEFRAME
    )
    consecutive_closes: int = Field(default=1, ge=1)
    distance_buffer: float = Field(default=0.0, ge=0.0)

    def to_rule(
        self,
        *,
        rule_id: str = "hard-invalidation.close-acceptance.v0",
    ) -> HardInvalidationRule:
        return HardInvalidationRule(
            rule_id=rule_id,
            rule_type=HardInvalidationRuleType.PRICE_CLOSE_BEYOND_LEVEL,
            parameters={
                "timeframe": self.timeframe_basis.value,
                "consecutive_closes": self.consecutive_closes,
                "distance_buffer": self.distance_buffer,
            },
        )


def build_v0_close_acceptance_policy() -> CloseAcceptancePolicy:
    return CloseAcceptancePolicy(
        timeframe_basis=InvalidationTimeframeBasis.SETUP_TIMEFRAME,
        consecutive_closes=1,
        distance_buffer=0.0,
    )


class SafetyAssessment(SchemaModel):
    setup_candidate_id: NonEmptyStr
    passed: bool
    checks: dict[NonEmptyStr, bool]
    rejection_codes: list[NonEmptyStr] = Field(default_factory=list)
    entry_price: float | None = Field(default=None, gt=0.0)
    stop_loss: float | None = Field(default=None, gt=0.0)
    risk_per_trade_pct: float | None = Field(default=None, gt=0.0, le=100.0)
    position_size: float | None = Field(default=None, gt=0.0)
    expected_r: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_checks(self) -> "SafetyAssessment":
        if self.passed and not all(self.checks.values()):
            raise ValueError("passed safety assessment cannot contain failed checks")
        if self.passed and self.rejection_codes:
            raise ValueError("passed safety assessment cannot contain rejection codes")
        return self
