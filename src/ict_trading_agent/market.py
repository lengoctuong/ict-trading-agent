from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta
from typing import Any, Protocol

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import Timeframe

TIMEFRAME_DURATIONS: dict[Timeframe, timedelta] = {
    Timeframe.W1: timedelta(days=7),
    Timeframe.D1: timedelta(days=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M1: timedelta(minutes=1),
}


class OHLCBar(SchemaModel):
    symbol: NonEmptyStr
    timeframe: Timeframe
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: float = Field(gt=0.0)
    high: float = Field(gt=0.0)
    low: float = Field(gt=0.0)
    close: float = Field(gt=0.0)
    volume: float | None = Field(default=None, ge=0.0)
    tick_volume: int | None = Field(default=None, ge=0)
    is_closed: bool = True

    @model_validator(mode="after")
    def validate_bar(self) -> OHLCBar:
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        if self.high < max(self.open, self.close):
            raise ValueError("high cannot be below open/close")
        if self.low > min(self.open, self.close):
            raise ValueError("low cannot be above open/close")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        return self

    @property
    def duration(self) -> timedelta:
        return self.close_time - self.open_time


class BarAdjacencyPolicy(Protocol):
    def are_adjacent(self, left: OHLCBar, right: OHLCBar) -> bool: ...


class WallClockAdjacencyPolicy:
    def are_adjacent(self, left: OHLCBar, right: OHLCBar) -> bool:
        return left.close_time == right.open_time


class MarketClosure(SchemaModel):
    start_at: AwareDatetime
    end_at: AwareDatetime
    reason: NonEmptyStr

    @model_validator(mode="after")
    def validate_interval(self) -> MarketClosure:
        if self.end_at <= self.start_at:
            raise ValueError("market closure end must be after start")
        return self


class ExplicitClosureCalendar(SchemaModel):
    """Data-source calendar made of explicit weekend/maintenance closures."""

    calendar_id: NonEmptyStr
    closures: list[MarketClosure] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def covers_gap(self, start_at: AwareDatetime, end_at: AwareDatetime) -> bool:
        if end_at <= start_at:
            return end_at == start_at
        cursor = start_at
        for closure in sorted(self.closures, key=lambda item: item.start_at):
            if closure.end_at <= cursor:
                continue
            if closure.start_at > cursor:
                return False
            cursor = max(cursor, closure.end_at)
            if cursor >= end_at:
                return True
        return False


class MarketSequenceAdjacencyPolicy:
    def __init__(self, calendar: ExplicitClosureCalendar) -> None:
        self.calendar = calendar

    def are_adjacent(self, left: OHLCBar, right: OHLCBar) -> bool:
        if left.close_time > right.open_time:
            return False
        if left.close_time == right.open_time:
            return True
        return self.calendar.covers_gap(left.close_time, right.open_time)


def bars_are_contiguous(
    left: OHLCBar,
    right: OHLCBar,
    adjacency_policy: BarAdjacencyPolicy | None = None,
) -> bool:
    policy = adjacency_policy or WallClockAdjacencyPolicy()
    return (
        left.symbol == right.symbol
        and left.timeframe == right.timeframe
        and left.open_time < right.open_time
        and policy.are_adjacent(left, right)
    )


class ClosedBarFeed:
    """Append-only multi-timeframe feed that never exposes developing bars."""

    def __init__(self, symbol: str) -> None:
        if not symbol.strip():
            raise ValueError("symbol cannot be empty")
        self.symbol = symbol.strip()
        self._bars: dict[Timeframe, list[OHLCBar]] = defaultdict(list)
        self._keys: set[tuple[Timeframe, object]] = set()
        self._open_indexes: dict[Timeframe, dict[object, int]] = defaultdict(dict)
        self._close_indexes: dict[Timeframe, dict[object, int]] = defaultdict(dict)
        self._open_times: dict[Timeframe, list[object]] = defaultdict(list)
        self._close_times: dict[Timeframe, list[object]] = defaultdict(list)

    def append(self, bar: OHLCBar, *, observed_at: AwareDatetime) -> None:
        if bar.symbol != self.symbol:
            raise ValueError("bar symbol does not match feed symbol")
        if not bar.is_closed:
            raise ValueError("closed-bar feed rejects developing bars")
        if bar.close_time > observed_at:
            raise ValueError("bar cannot be observed before close_time")
        key = (bar.timeframe, bar.open_time)
        if key in self._keys:
            raise ValueError("duplicate bar for timeframe/open_time")
        existing = self._bars[bar.timeframe]
        if existing and bar.open_time < existing[-1].close_time:
            raise ValueError("bars cannot overlap or be appended out of order")
        index = len(existing)
        existing.append(bar)
        self._keys.add(key)
        self._open_indexes[bar.timeframe][bar.open_time] = index
        self._close_indexes[bar.timeframe][bar.close_time] = index
        self._open_times[bar.timeframe].append(bar.open_time)
        self._close_times[bar.timeframe].append(bar.close_time)

    def bars(
        self,
        timeframe: Timeframe,
        *,
        as_of: AwareDatetime | None = None,
    ) -> tuple[OHLCBar, ...]:
        bars = self._bars.get(timeframe, [])
        if as_of is None:
            return tuple(bars)
        # Close times are append-only and strictly non-overlapping, so a binary
        # search keeps repeated point-in-time reads from re-scanning all prior
        # bars during M2/M3 replay.
        visible_stop = bisect_right(self._close_times.get(timeframe, []), as_of)
        return tuple(bars[:visible_stop])

    def latest(
        self,
        timeframe: Timeframe,
        *,
        as_of: AwareDatetime,
    ) -> OHLCBar | None:
        visible = self.bars(timeframe, as_of=as_of)
        return visible[-1] if visible else None

    def index_of_open(self, timeframe: Timeframe, open_time: AwareDatetime) -> int:
        return self._open_indexes[timeframe][open_time]

    def index_of_close(self, timeframe: Timeframe, close_time: AwareDatetime) -> int:
        return self._close_indexes[timeframe][close_time]

    def count_closed_between(
        self,
        timeframe: Timeframe,
        *,
        after: AwareDatetime,
        through: AwareDatetime,
    ) -> int:
        close_times = self._close_times.get(timeframe, [])
        return bisect_right(close_times, through) - bisect_right(close_times, after)

    def count_open_between(
        self,
        timeframe: Timeframe,
        *,
        start: AwareDatetime,
        end: AwareDatetime,
    ) -> int:
        open_times = self._open_times.get(timeframe, [])
        return bisect_right(open_times, end) - bisect_left(open_times, start)
