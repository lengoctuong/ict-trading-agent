from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import FactType, Session, Timeframe
from .facts import ObservableFact, PriceGeometry
from .market import ExplicitClosureCalendar, MarketClosure, OHLCBar
from .references import CompletedSessionRange, CompletedTradingDay, ReferenceFactBuilder
from .sessions import SessionSchedule, SessionWindow

EXNESS_TRADING_HOURS_URL = (
    "https://get.exness.help/hc/en-us/articles/4405235684498-Instrument-trading-hours"
)


class M4StudyWindow(SchemaModel):
    """Replay warmup is executed normally but excluded from study statistics."""

    replay_start: AwareDatetime
    analysis_start: AwareDatetime
    analysis_end: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> M4StudyWindow:
        if self.analysis_start <= self.replay_start:
            raise ValueError("analysis_start must be after replay_start")
        if self.analysis_end is not None and self.analysis_end <= self.analysis_start:
            raise ValueError("analysis_end must be after analysis_start")
        return self

    def phase_at(self, timestamp: AwareDatetime) -> str:
        if timestamp < self.analysis_start:
            return "warmup"
        if self.analysis_end is not None and timestamp > self.analysis_end:
            return "post_analysis"
        return "analysis"


class M4SymbolMetadata(SchemaModel):
    """Snapshot of MT5 symbol metadata used by an experiment."""

    symbol: NonEmptyStr
    digits: int = Field(ge=0)
    point: float = Field(gt=0.0)
    trade_tick_size: float = Field(gt=0.0)
    source: NonEmptyStr = "MT5 symbol_info"
    captured_at: AwareDatetime | None = None

    @classmethod
    def from_mt5_symbol_info(
        cls, info: Any, *, captured_at: AwareDatetime
    ) -> M4SymbolMetadata:
        """Adapt MetaTrader5.symbol_info() without importing the optional SDK."""

        required = ("name", "digits", "point", "trade_tick_size")
        missing = [name for name in required if not hasattr(info, name)]
        if missing:
            raise ValueError(f"MT5 symbol_info missing fields: {', '.join(missing)}")
        return cls(
            symbol=str(info.name),
            digits=int(info.digits),
            point=float(info.point),
            trade_tick_size=float(info.trade_tick_size),
            captured_at=captured_at,
        )


class M4SourceFingerprint(SchemaModel):
    source_name: NonEmptyStr
    content_sha256: NonEmptyStr
    rows: int = Field(ge=1)
    timeframes: list[Timeframe]


class M4ExperimentManifest(SchemaModel):
    manifest_version: NonEmptyStr = "0.1.0"
    git_commit_sha: NonEmptyStr
    m2_version: NonEmptyStr
    m3_version: NonEmptyStr
    m4_version: NonEmptyStr
    symbol_metadata: M4SymbolMetadata
    study_window: M4StudyWindow
    source_data: list[M4SourceFingerprint]
    candle_config: dict[str, Any]
    displacement_config: dict[str, Any]
    m3_policy: dict[str, Any]
    adjacency_calendar_policy: dict[str, Any]
    reference_policy: dict[str, Any]
    context_policy: dict[str, Any]
    target_policy: dict[str, Any]

    def fingerprint(self) -> str:
        import json

        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()


class ExnessXauCalendarPreset(SchemaModel):
    """Official regular XAU hours; exceptional closures remain explicit inputs."""

    preset_id: NonEmptyStr = "exness.xau.regular-hours.us-dst"
    version: NonEmptyStr = "2026-08-13"
    source_url: NonEmptyStr = EXNESS_TRADING_HOURS_URL
    source_timezone: str = "UTC"

    def build(
        self,
        *,
        start_date: date,
        end_date: date,
        exceptional_closures: tuple[MarketClosure, ...] = (),
    ) -> ExplicitClosureCalendar:
        if end_date < start_date:
            raise ValueError("calendar end_date cannot precede start_date")
        closures: list[MarketClosure] = []
        current = start_date - timedelta(days=2)
        last = end_date + timedelta(days=2)
        while current <= last:
            weekday = current.weekday()
            if weekday in range(4):
                summer = _us_dst_at(current)
                closures.append(
                    MarketClosure(
                        start_at=_utc_at(
                            current, time(20, 58) if summer else time(21, 58)
                        ),
                        end_at=_utc_at(current, time(22, 2) if summer else time(23, 2)),
                        reason="Exness XAU regular daily break",
                    )
                )
            elif weekday == 4:
                sunday = current + timedelta(days=2)
                closures.append(
                    MarketClosure(
                        start_at=_utc_at(
                            current,
                            time(20, 58) if _us_dst_at(current) else time(21, 58),
                        ),
                        end_at=_utc_at(
                            sunday,
                            time(22, 5) if _us_dst_at(sunday) else time(23, 5),
                        ),
                        reason="Exness XAU regular weekend break",
                    )
                )
            current += timedelta(days=1)
        closures.extend(item.model_copy(deep=True) for item in exceptional_closures)
        return ExplicitClosureCalendar(
            calendar_id=f"{self.preset_id}@{self.version}",
            closures=closures,
            metadata={
                "instrument": "XAUUSD",
                "source_url": self.source_url,
                "source_timezone": self.source_timezone,
                "dst_rule": "US",
                "exceptional_closures_are_explicit": True,
            },
        )


def _utc_at(value: date, clock: time) -> datetime:
    return datetime.combine(value, clock, tzinfo=UTC)


def _us_dst_at(value: date) -> bool:
    local = datetime.combine(value, time(12), tzinfo=ZoneInfo("America/New_York"))
    return bool(local.dst())


class TemporalContextProvider(Protocol):
    def context_at(self, as_of: AwareDatetime) -> dict[str, Any]: ...

    def manifest(self) -> dict[str, Any]: ...


class SessionContextProvider:
    """DST-aware annotation only; it never filters bars or setups."""

    version = "0.1.1"

    def __init__(self, schedule: SessionSchedule) -> None:
        if not schedule.windows:
            raise ValueError("session context requires explicit reviewed windows")
        self.schedule = schedule.model_copy(deep=True)
        self.new_york = ZoneInfo("America/New_York")

    def context_at(self, as_of: AwareDatetime) -> dict[str, Any]:
        local = as_of.astimezone(self.new_york)
        sessions = self.schedule.sessions_at(as_of)
        primary = self.schedule.optional_primary_session_at(as_of)
        overlap_key = (
            "+".join(item.value for item in sessions) if len(sessions) > 1 else None
        )
        return {
            "session": primary.value if primary is not None else None,
            "primary_session": primary.value if primary is not None else None,
            "sessions": [item.value for item in sessions],
            "session_overlap": len(sessions) > 1,
            "session_overlap_key": overlap_key,
            "utc_at": as_of.astimezone(UTC).isoformat(),
            "new_york_at": local.isoformat(),
            "new_york_date": local.date().isoformat(),
            "new_york_utc_offset_seconds": int(local.utcoffset().total_seconds()),
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "provider": type(self).__name__,
            "version": self.version,
            "annotation_only": True,
            "schedule": self.schedule.model_dump(mode="json"),
        }


class CausalReferencePolicy(SchemaModel):
    policy_id: NonEmptyStr = "m4.references.exness-native"
    version: NonEmptyStr = "0.1.0"
    previous_day_from_native_d1: bool = True
    session_source_timeframe: Timeframe | None = None
    session_schedule: SessionSchedule | None = None
    true_day_open_source_timeframe: Timeframe | None = None
    true_day_open_timezone: str | None = None
    true_day_open_local: time | None = None

    @model_validator(mode="after")
    def validate_optional_sources(self) -> CausalReferencePolicy:
        if (self.session_source_timeframe is None) != (self.session_schedule is None):
            raise ValueError(
                "session source timeframe and schedule must be configured together"
            )
        tdo = (
            self.true_day_open_source_timeframe,
            self.true_day_open_timezone,
            self.true_day_open_local,
        )
        if any(item is not None for item in tdo) and not all(
            item is not None for item in tdo
        ):
            raise ValueError(
                "TDO source timeframe, timezone, and local time are all required"
            )
        if self.true_day_open_timezone is not None:
            ZoneInfo(self.true_day_open_timezone)
        return self


class CausalReferenceBuilder:
    """Build only references whose complete source period is observable as-of."""

    version = "0.1.1"

    def __init__(self, policy: CausalReferencePolicy | None = None) -> None:
        self.policy = policy or CausalReferencePolicy()
        self.builder = ReferenceFactBuilder()
        self._session_bars: dict[
            tuple[Session, datetime, datetime, str], list[OHLCBar]
        ] = {}
        self._emitted_sessions: set[tuple[Session, datetime, datetime, str]] = set()
        self._emitted_tdo: set[tuple[str, date]] = set()

    def ingest(self, bar: OHLCBar) -> tuple[ObservableFact, ...]:
        facts: list[ObservableFact] = []
        if self.policy.previous_day_from_native_d1 and bar.timeframe == Timeframe.D1:
            facts.extend(
                self.builder.previous_day(
                    CompletedTradingDay(
                        symbol=bar.symbol,
                        trading_day=bar.open_time.date().isoformat(),
                        start_at=bar.open_time,
                        end_at=bar.close_time,
                        available_at=bar.close_time,
                        high=bar.high,
                        low=bar.low,
                    )
                )
            )
        facts.extend(self._ingest_session(bar))
        facts.extend(self._ingest_tdo(bar))
        return tuple(facts)

    def manifest(self) -> dict[str, Any]:
        return {
            "builder": type(self).__name__,
            "version": self.version,
            "policy": self.policy.model_dump(mode="json"),
        }

    def _ingest_session(self, bar: OHLCBar) -> list[ObservableFact]:
        policy = self.policy
        if (
            bar.timeframe != policy.session_source_timeframe
            or policy.session_schedule is None
        ):
            return []
        for window in policy.session_schedule.windows:
            bounds = _window_bounds(window, bar.open_time)
            if bounds[0] <= bar.open_time and bar.close_time <= bounds[1]:
                self._session_bars.setdefault(
                    (window.session, *bounds, window.timezone), []
                ).append(bar)
        emitted: list[ObservableFact] = []
        for key, bars in list(self._session_bars.items()):
            session, start_at, end_at, timezone = key
            if key in self._emitted_sessions or bar.close_time < end_at:
                continue
            session_facts = self.builder.session(
                CompletedSessionRange(
                    symbol=bar.symbol,
                    trading_day=end_at.astimezone(ZoneInfo(timezone))
                    .date()
                    .isoformat(),
                    session=session,
                    start_at=start_at,
                    end_at=end_at,
                    available_at=bar.close_time,
                    high=max(item.high for item in bars),
                    low=min(item.low for item in bars),
                )
            )
            emitted.extend(
                fact.model_copy(
                    update={
                        "metrics": fact.metrics
                        | {
                            "source_timeframe": bar.timeframe.value,
                            "source_timezone": timezone,
                            "source_start_at": start_at.isoformat(),
                            "source_end_at": end_at.isoformat(),
                        }
                    }
                )
                for fact in session_facts
            )
            self._emitted_sessions.add(key)
        return emitted

    def _ingest_tdo(self, bar: OHLCBar) -> list[ObservableFact]:
        policy = self.policy
        if bar.timeframe != policy.true_day_open_source_timeframe:
            return []
        if policy.true_day_open_timezone is None or policy.true_day_open_local is None:
            return []
        local = bar.open_time.astimezone(ZoneInfo(policy.true_day_open_timezone))
        key = (bar.symbol, local.date())
        if (
            local.timetz().replace(tzinfo=None) != policy.true_day_open_local
            or key in self._emitted_tdo
        ):
            return []
        self._emitted_tdo.add(key)
        raw = f"tdo|{bar.symbol}|{local.date().isoformat()}"
        return [
            ObservableFact(
                fact_id="fact-" + sha256(raw.encode()).hexdigest()[:24],
                fact_type=FactType.TRUE_DAY_OPEN,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                occurred_at=bar.open_time,
                confirmed_at=bar.open_time,
                available_at=bar.open_time,
                geometry=PriceGeometry(price=bar.open),
                metrics={
                    "trading_day": local.date().isoformat(),
                    "timezone": policy.true_day_open_timezone,
                    "local_time": policy.true_day_open_local.isoformat(),
                    "observed_at": bar.close_time.isoformat(),
                    "observation_policy": "closed_source_bar",
                },
                detector_name=type(self).__name__,
                detector_version=self.version,
            )
        ]


def _window_bounds(
    window: SessionWindow, timestamp: AwareDatetime
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(window.timezone)
    local = timestamp.astimezone(zone)
    local_clock = local.timetz().replace(tzinfo=None)
    if window.start_local < window.end_local:
        start_date = local.date()
        end_date = local.date()
    elif local_clock >= window.start_local:
        start_date = local.date()
        end_date = local.date() + timedelta(days=1)
    else:
        start_date = local.date() - timedelta(days=1)
        end_date = local.date()
    start = datetime.combine(start_date, window.start_local, tzinfo=zone)
    end = datetime.combine(end_date, window.end_local, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)
