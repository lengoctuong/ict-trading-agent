from __future__ import annotations

from hashlib import sha256

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import FactType, Session, Timeframe
from .facts import ObservableFact, PriceGeometry


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


class CompletedTradingDay(SchemaModel):
    symbol: NonEmptyStr
    trading_day: NonEmptyStr
    start_at: AwareDatetime
    end_at: AwareDatetime
    available_at: AwareDatetime
    high: float = Field(gt=0.0)
    low: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_period(self) -> "CompletedTradingDay":
        if self.end_at <= self.start_at:
            raise ValueError("trading day end must be after start")
        if self.available_at < self.end_at:
            raise ValueError("trading day levels cannot be available before day end")
        if self.low > self.high:
            raise ValueError("trading day low cannot exceed high")
        return self


class CompletedSessionRange(SchemaModel):
    symbol: NonEmptyStr
    trading_day: NonEmptyStr
    session: Session
    start_at: AwareDatetime
    end_at: AwareDatetime
    available_at: AwareDatetime
    high: float = Field(gt=0.0)
    low: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_period(self) -> "CompletedSessionRange":
        if self.session == Session.OFF_SESSION:
            raise ValueError("completed range requires a concrete session")
        if self.end_at <= self.start_at:
            raise ValueError("session end must be after start")
        if self.available_at < self.end_at:
            raise ValueError("session levels cannot be available before session end")
        if self.low > self.high:
            raise ValueError("session low cannot exceed high")
        return self


class ReferenceFactBuilder:
    detector_name = "ReferenceFactBuilder"
    detector_version = "0.1.0"

    def previous_day(self, period: CompletedTradingDay) -> tuple[ObservableFact, ...]:
        return (
            self._level_fact(
                period=period,
                side="high",
                price=period.high,
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                metrics={"trading_day": period.trading_day, "side": "high"},
            ),
            self._level_fact(
                period=period,
                side="low",
                price=period.low,
                fact_type=FactType.PREVIOUS_DAY_LEVEL,
                metrics={"trading_day": period.trading_day, "side": "low"},
            ),
        )

    def session(self, period: CompletedSessionRange) -> tuple[ObservableFact, ...]:
        common = {
            "trading_day": period.trading_day,
            "session": period.session.value,
        }
        return (
            self._level_fact(
                period=period,
                side="high",
                price=period.high,
                fact_type=FactType.SESSION_LEVEL,
                metrics=common | {"side": "high"},
            ),
            self._level_fact(
                period=period,
                side="low",
                price=period.low,
                fact_type=FactType.SESSION_LEVEL,
                metrics=common | {"side": "low"},
            ),
        )

    def _level_fact(
        self,
        *,
        period: CompletedTradingDay | CompletedSessionRange,
        side: str,
        price: float,
        fact_type: FactType,
        metrics: dict[str, object],
    ) -> ObservableFact:
        return ObservableFact(
            fact_id="fact-" + _stable_id(
                fact_type.value,
                period.symbol,
                period.start_at.isoformat(),
                side,
            ),
            fact_type=fact_type,
            symbol=period.symbol,
            timeframe=None if fact_type == FactType.SESSION_LEVEL else Timeframe.D1,
            occurred_at=period.end_at,
            confirmed_at=period.end_at,
            available_at=period.available_at,
            geometry=PriceGeometry(price=price),
            metrics=metrics,
            detector_name=self.detector_name,
            detector_version=self.detector_version,
        )

