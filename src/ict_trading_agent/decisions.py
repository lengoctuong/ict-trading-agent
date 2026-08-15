from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import TradeAction


class TradeDecision(SchemaModel):
    decision_id: NonEmptyStr
    symbol: NonEmptyStr
    created_at: AwareDatetime
    setup_candidate_id: NonEmptyStr
    semantic_assessment_id: str | None = None
    action: TradeAction
    entry_price: float | None = Field(default=None, gt=0.0)
    stop_loss: float | None = Field(default=None, gt=0.0)
    take_profit: float | None = Field(default=None, gt=0.0)
    target_candidate_id: str | None = None
    position_size: float | None = Field(default=None, gt=0.0)
    risk_per_trade_pct: float | None = Field(default=None, gt=0.0, le=100.0)
    expected_r: float | None = Field(default=None, ge=0.0)
    safety_passed: bool
    rejection_codes: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_executable_trade(self) -> "TradeDecision":
        if self.action == TradeAction.NO_TRADE:
            return self
        if not self.safety_passed:
            raise ValueError("LONG/SHORT decisions require a passed safety gate")
        required = {
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "target_candidate_id": self.target_candidate_id,
            "position_size": self.position_size,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "expected_r": self.expected_r,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"executable trade is missing: {', '.join(missing)}")
        assert self.entry_price is not None
        assert self.stop_loss is not None
        assert self.take_profit is not None
        if self.action == TradeAction.LONG and not (
            self.stop_loss < self.entry_price < self.take_profit
        ):
            raise ValueError("LONG requires stop_loss < entry_price < take_profit")
        if self.action == TradeAction.SHORT and not (
            self.take_profit < self.entry_price < self.stop_loss
        ):
            raise ValueError("SHORT requires take_profit < entry_price < stop_loss")
        return self

