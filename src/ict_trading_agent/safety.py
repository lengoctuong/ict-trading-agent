from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import HardInvalidationRuleType


class HardInvalidationRule(SchemaModel):
    rule_id: NonEmptyStr
    rule_type: HardInvalidationRuleType
    parameters: dict[str, Any] = Field(default_factory=dict)


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

