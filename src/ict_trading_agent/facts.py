from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import Direction, FactType, Timeframe


class PriceGeometry(SchemaModel):
    price: float | None = None
    low: float | None = None
    high: float | None = None
    extreme: float | None = None

    @model_validator(mode="after")
    def validate_geometry(self) -> "PriceGeometry":
        if all(value is None for value in (self.price, self.low, self.high, self.extreme)):
            raise ValueError("price geometry must contain at least one coordinate")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("geometry.low cannot exceed geometry.high")
        return self


class ObservableFact(SchemaModel):
    fact_id: NonEmptyStr
    fact_type: FactType
    symbol: NonEmptyStr
    timeframe: Timeframe | None = None
    occurred_at: AwareDatetime
    confirmed_at: AwareDatetime | None = None
    available_at: AwareDatetime
    direction: Direction | None = None
    geometry: PriceGeometry | None = None
    source_fact_ids: list[NonEmptyStr] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    detector_name: NonEmptyStr
    detector_version: NonEmptyStr

    @model_validator(mode="after")
    def validate_market_time(self) -> "ObservableFact":
        if self.confirmed_at is not None and self.confirmed_at < self.occurred_at:
            raise ValueError("confirmed_at cannot precede occurred_at")
        if self.available_at < self.occurred_at:
            raise ValueError("available_at cannot precede occurred_at")
        if self.confirmed_at is not None and self.available_at < self.confirmed_at:
            raise ValueError("available_at cannot precede confirmed_at")
        if self.fact_id in self.source_fact_ids:
            raise ValueError("a fact cannot reference itself as a source")
        return self

