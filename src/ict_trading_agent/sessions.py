from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import Session


class SessionWindow(SchemaModel):
    session: Session
    timezone: NonEmptyStr
    start_local: time
    end_local: time

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def reject_off_session_window(self) -> SessionWindow:
        if self.session == Session.OFF_SESSION:
            raise ValueError("OFF_SESSION cannot have a configured window")
        if self.start_local == self.end_local:
            raise ValueError("session window cannot span exactly 24 hours")
        return self

    def contains(self, timestamp: AwareDatetime) -> bool:
        local = (
            timestamp.astimezone(ZoneInfo(self.timezone)).timetz().replace(tzinfo=None)
        )
        if self.start_local < self.end_local:
            return self.start_local <= local < self.end_local
        return local >= self.start_local or local < self.end_local


class SessionSchedule(SchemaModel):
    windows: list[SessionWindow]
    priority: list[Session] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_priority(self) -> SessionSchedule:
        if len(self.priority) != len(set(self.priority)):
            raise ValueError("session priority cannot contain duplicates")
        configured = {window.session for window in self.windows}
        unknown = set(self.priority) - configured
        if unknown:
            raise ValueError("priority references an unconfigured session")
        return self

    def sessions_at(self, timestamp: AwareDatetime) -> tuple[Session, ...]:
        matches = {
            window.session for window in self.windows if window.contains(timestamp)
        }
        ordered = [session for session in self.priority if session in matches]
        ordered.extend(sorted(matches - set(ordered), key=lambda item: item.value))
        return tuple(ordered)

    def primary_session_at(self, timestamp: AwareDatetime) -> Session:
        matches = self.sessions_at(timestamp)
        if not matches:
            return Session.OFF_SESSION
        if len(matches) > 1 and not self.priority:
            raise ValueError("overlapping sessions require explicit priority")
        return matches[0]

    def optional_primary_session_at(self, timestamp: AwareDatetime) -> Session | None:
        """Return no primary label when an unprioritized overlap is intentional."""

        matches = self.sessions_at(timestamp)
        if not matches:
            return None
        if len(matches) == 1 or self.priority:
            return matches[0]
        return None
