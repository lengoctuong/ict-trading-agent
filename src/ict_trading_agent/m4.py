from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from io import StringIO
from itertools import groupby, pairwise
from pathlib import Path
from typing import Any, ClassVar

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .candidates import ConceptCandidate, SetupCandidate, TargetCandidate
from .detectors import CandleFeatureConfig, DisplacementThresholds
from .enums import CandidateType, Direction, FactType, SetupStatus, Timeframe
from .facts import ObservableFact
from .lifecycle import SetupEvidenceLink, SetupTransition
from .m3 import M3DetectionBatch, M3Policy, M3SetupPipeline, ReadyForLLMPayload
from .m4_support import (
    CausalReferenceBuilder,
    M4ExperimentManifest,
    M4SourceFingerprint,
    M4StudyWindow,
    M4SymbolMetadata,
    TemporalContextProvider,
)
from .market import (
    TIMEFRAME_DURATIONS,
    BarAdjacencyPolicy,
    ClosedBarFeed,
    ExplicitClosureCalendar,
    MarketSequenceAdjacencyPolicy,
    OHLCBar,
)
from .pipeline import M2DetectionBatch, M2PrimitivePipeline
from .reference_lifecycle import ReferenceLifecyclePolicy
from .stores import CandidateStore, FactStore, RaidEpisodeStore, SetupStore


class DataQualityError(ValueError):
    def __init__(self, message: str, report: DataQualityReport) -> None:
        super().__init__(message)
        self.report = report


class DataIssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class M4EventKind(str, Enum):
    BAR = "bar"
    FACT = "fact"
    CANDIDATE = "candidate"
    RAID_EPISODE = "raid_episode"
    RAID_UPDATE = "raid_update"
    SETUP = "setup"
    SETUP_EVIDENCE = "setup_evidence"
    TRANSITION = "transition"
    READY_PAYLOAD = "ready_payload"


class DataGap(SchemaModel):
    timeframe: Timeframe
    previous_close_at: AwareDatetime
    next_open_at: AwareDatetime
    missing_bars: int = Field(ge=1)
    covered_by_calendar: bool = False


class DataQualityIssue(SchemaModel):
    code: NonEmptyStr
    severity: DataIssueSeverity
    timeframe: Timeframe | None = None
    row_numbers: list[int] = Field(default_factory=list)
    message: NonEmptyStr
    metrics: dict[str, Any] = Field(default_factory=dict)


class DataQualityReport(SchemaModel):
    source_name: NonEmptyStr
    source_timezone: str = "UTC"
    rows_read: int = Field(ge=0)
    rows_accepted: int = Field(ge=0)
    duplicate_rows: int = Field(default=0, ge=0)
    out_of_order_rows: int = Field(default=0, ge=0)
    abnormal_spread_rows: int = Field(default=0, ge=0)
    abnormal_spread_threshold_points: float | None = Field(default=None, ge=0.0)
    gaps: list[DataGap] = Field(default_factory=list)
    issues: list[DataQualityIssue] = Field(default_factory=list)

    @property
    def unexplained_gap_count(self) -> int:
        return sum(not item.covered_by_calendar for item in self.gaps)

    @property
    def has_errors(self) -> bool:
        return any(item.severity == DataIssueSeverity.ERROR for item in self.issues)


class ExnessBarRecord(SchemaModel):
    bar: OHLCBar
    spread_points: float | None = Field(default=None, ge=0.0)
    source_name: NonEmptyStr
    source_row_number: int = Field(ge=2)
    source_timezone: str = "UTC"
    source_metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_exness_clock(self) -> ExnessBarRecord:
        if self.source_timezone.upper() != "UTC":
            raise ValueError("Exness v0 ingestion requires an explicit UTC source")
        if self.bar.open_time.utcoffset() != UTC.utcoffset(None):
            raise ValueError("Exness bar timestamps must be UTC")
        return self


class ExnessDataset(SchemaModel):
    symbol: NonEmptyStr
    records: list[ExnessBarRecord]
    quality: DataQualityReport
    content_sha256: NonEmptyStr
    source_rows: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> ExnessDataset:
        if not self.records:
            raise ValueError("Exness dataset cannot be empty")
        if any(item.bar.symbol != self.symbol for item in self.records):
            raise ValueError("dataset records must use one symbol")
        expected_source_rows = self.source_rows or len(self.records)
        if self.quality.rows_accepted != expected_source_rows:
            raise ValueError(
                "quality accepted-row count must match the source dataset"
            )
        if self.source_rows is not None and len(self.records) > self.source_rows:
            raise ValueError("a replay slice cannot exceed its source row count")
        return self

    def window(
        self,
        *,
        start_at: AwareDatetime,
        end_at: AwareDatetime | None,
    ) -> ExnessDataset:
        """Retain only replay rows while preserving full-source provenance."""

        selected = [
            record
            for record in self.records
            if record.bar.open_time >= start_at
            and (end_at is None or record.bar.close_time <= end_at)
        ]
        if not selected:
            raise ValueError("dataset window excludes every record")
        return ExnessDataset(
            symbol=self.symbol,
            records=selected,
            quality=self.quality,
            content_sha256=self.content_sha256,
            source_rows=self.source_rows or len(self.records),
        )


def _normalized_header(value: str) -> str:
    return value.strip().lstrip("\ufeff").strip("<>").lower().replace(" ", "_")


def _first(row: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def _parse_number(value: str | None, *, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise ValueError("required numeric value is missing")
        return None
    return float(value.replace(",", "."))


def _parse_timestamp(row: Mapping[str, str]) -> datetime:
    raw = _first(row, "datetime", "date_time", "timestamp")
    if raw is None:
        date = _first(row, "date")
        time = _first(row, "time")
        if date is None or time is None:
            raise ValueError("CSV requires datetime/timestamp or date + time columns")
        raw = f"{date} {time}"
    parsed: datetime | None = None
    for parser in (
        datetime.fromisoformat,
        lambda item: datetime.strptime(item, "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC),
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC),
        lambda item: datetime.strptime(item, "%Y.%m.%d %H:%M").replace(tzinfo=UTC),
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M").replace(tzinfo=UTC),
    ):
        try:
            parsed = parser(raw.replace("Z", "+00:00"))
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"unsupported Exness timestamp: {raw}")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    if parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError("Exness v0 timestamps must be UTC/GMT+0")
    return parsed.astimezone(UTC)


class ExnessCsvLoader:
    """Parse native MT5/Exness rate exports without silently repairing data."""

    def __init__(
        self,
        *,
        symbol: str = "XAUUSD",
        timeframe: Timeframe | None = None,
        strict: bool = True,
        closure_calendar: ExplicitClosureCalendar | None = None,
        abnormal_spread_threshold_points: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.strict = strict
        self.closure_calendar = closure_calendar
        self.abnormal_spread_threshold_points = abnormal_spread_threshold_points

    def load(self, path: str | Path) -> ExnessDataset:
        source = Path(path)
        return self.loads(
            source.read_text(encoding="utf-8-sig"),
            source_name=str(source),
        )

    def loads(self, text: str, *, source_name: str = "<memory>") -> ExnessDataset:
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(StringIO(text.lstrip("\ufeff")), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("Exness CSV requires a header row")
        reader.fieldnames = [_normalized_header(item) for item in reader.fieldnames]
        records: list[ExnessBarRecord] = []
        issues: list[DataQualityIssue] = []
        seen: dict[tuple[Timeframe, datetime], int] = {}
        last_seen: dict[Timeframe, datetime] = {}
        duplicate_rows = 0
        out_of_order_rows = 0
        abnormal_spread_rows = 0
        rows_read = 0
        for row_number, raw_row in enumerate(reader, start=2):
            rows_read += 1
            row = {
                _normalized_header(str(key)): str(value or "")
                for key, value in raw_row.items()
                if key is not None
            }
            try:
                raw_timeframe = _first(row, "timeframe", "tf")
                timeframe = self.timeframe or (
                    Timeframe(raw_timeframe.upper()) if raw_timeframe else None
                )
                if timeframe is None:
                    raise ValueError("timeframe is required when CSV has no TF column")
                opened = _parse_timestamp(row)
                row_symbol = _first(row, "symbol") or self.symbol
                if row_symbol != self.symbol:
                    raise ValueError(
                        f"row symbol {row_symbol!r} does not match {self.symbol!r}"
                    )
                bar = OHLCBar(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=opened,
                    close_time=opened + TIMEFRAME_DURATIONS[timeframe],
                    open=_parse_number(_first(row, "open"), required=True),
                    high=_parse_number(_first(row, "high"), required=True),
                    low=_parse_number(_first(row, "low"), required=True),
                    close=_parse_number(_first(row, "close"), required=True),
                    tick_volume=(
                        int(float(value))
                        if (value := _first(row, "tickvol", "tick_volume"))
                        else None
                    ),
                    volume=_parse_number(_first(row, "vol", "volume", "real_volume")),
                )
                spread = _parse_number(_first(row, "spread", "spread_points"))
            except (TypeError, ValueError) as exc:
                issues.append(
                    DataQualityIssue(
                        code="INVALID_ROW",
                        severity=DataIssueSeverity.ERROR,
                        row_numbers=[row_number],
                        message=str(exc),
                    )
                )
                continue
            key = (timeframe, opened)
            if key in seen:
                duplicate_rows += 1
                issues.append(
                    DataQualityIssue(
                        code="DUPLICATE_BAR",
                        severity=DataIssueSeverity.ERROR,
                        timeframe=timeframe,
                        row_numbers=[seen[key], row_number],
                        message="duplicate timeframe/open_time",
                    )
                )
                continue
            seen[key] = row_number
            if timeframe in last_seen and opened < last_seen[timeframe]:
                out_of_order_rows += 1
                issues.append(
                    DataQualityIssue(
                        code="OUT_OF_ORDER_BAR",
                        severity=DataIssueSeverity.ERROR,
                        timeframe=timeframe,
                        row_numbers=[row_number],
                        message=(
                            "bar appears before an earlier row on the same timeframe"
                        ),
                    )
                )
            last_seen[timeframe] = max(opened, last_seen.get(timeframe, opened))
            if (
                spread is not None
                and self.abnormal_spread_threshold_points is not None
                and spread > self.abnormal_spread_threshold_points
            ):
                abnormal_spread_rows += 1
                issues.append(
                    DataQualityIssue(
                        code="ABNORMAL_SPREAD",
                        severity=DataIssueSeverity.WARNING,
                        timeframe=timeframe,
                        row_numbers=[row_number],
                        message="spread exceeds the configured source threshold",
                        metrics={"spread_points": spread},
                    )
                )
            known = {
                "date",
                "time",
                "datetime",
                "date_time",
                "timestamp",
                "symbol",
                "timeframe",
                "tf",
                "open",
                "high",
                "low",
                "close",
                "tickvol",
                "tick_volume",
                "vol",
                "volume",
                "real_volume",
                "spread",
                "spread_points",
            }
            records.append(
                ExnessBarRecord(
                    bar=bar,
                    spread_points=spread,
                    source_name=source_name,
                    source_row_number=row_number,
                    source_metrics={
                        key: value for key, value in row.items() if key not in known
                    },
                )
            )
        records.sort(key=lambda item: (item.bar.open_time, item.bar.timeframe.value))
        gaps: list[DataGap] = []
        by_timeframe: dict[Timeframe, list[ExnessBarRecord]] = defaultdict(list)
        for record in records:
            by_timeframe[record.bar.timeframe].append(record)
        for timeframe, items in by_timeframe.items():
            duration = TIMEFRAME_DURATIONS[timeframe]
            for left, right in pairwise(items):
                if right.bar.open_time <= left.bar.open_time + duration:
                    continue
                missing = int((right.bar.open_time - left.bar.close_time) / duration)
                covered = bool(
                    self.closure_calendar
                    and self.closure_calendar.covers_gap(
                        left.bar.close_time, right.bar.open_time
                    )
                )
                gaps.append(
                    DataGap(
                        timeframe=timeframe,
                        previous_close_at=left.bar.close_time,
                        next_open_at=right.bar.open_time,
                        missing_bars=missing,
                        covered_by_calendar=covered,
                    )
                )
                issues.append(
                    DataQualityIssue(
                        code=("EXPECTED_MARKET_CLOSURE" if covered else "MISSING_BARS"),
                        severity=(
                            DataIssueSeverity.WARNING
                            if covered
                            else DataIssueSeverity.ERROR
                        ),
                        timeframe=timeframe,
                        message="gap found between consecutive source bars",
                        metrics={"missing_bars": missing},
                    )
                )
        report = DataQualityReport(
            source_name=source_name,
            rows_read=rows_read,
            rows_accepted=len(records),
            duplicate_rows=duplicate_rows,
            out_of_order_rows=out_of_order_rows,
            abnormal_spread_rows=abnormal_spread_rows,
            abnormal_spread_threshold_points=self.abnormal_spread_threshold_points,
            gaps=gaps,
            issues=issues,
        )
        if self.strict and report.has_errors:
            raise DataQualityError("Exness CSV failed strict data validation", report)
        return ExnessDataset(
            symbol=self.symbol,
            records=records,
            quality=report,
            content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        )


class M4AuditEvent(SchemaModel):
    event_id: NonEmptyStr
    kind: M4EventKind
    category: NonEmptyStr
    record_id: NonEmptyStr
    symbol: NonEmptyStr
    timeframe: Timeframe | None = None
    direction: Direction | None = None
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    observed_at: AwareDatetime | None = None
    setup_candidate_id: str | None = None
    raid_episode_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    study_phase: str = "analysis"
    included_in_analysis: bool = True
    temporal_context: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]


class M4NearMiss(SchemaModel):
    near_miss_id: NonEmptyStr
    source_event_id: NonEmptyStr
    symbol: NonEmptyStr
    timeframe: Timeframe | None = None
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    reason_code: NonEmptyStr
    setup_candidate_id: str | None = None
    distance_bars: int | None = Field(default=None, ge=0)
    threshold_bars: int | None = Field(default=None, ge=0)
    excess_bars: int | None = Field(default=None, ge=0)
    study_phase: str = "analysis"
    included_in_analysis: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class M4ReplayStep(SchemaModel):
    as_of: AwareDatetime
    study_phase: str = "analysis"
    processed_timeframes: list[Timeframe]
    event_ids: list[NonEmptyStr]


class M4Summary(SchemaModel):
    bars: int = 0
    bars_by_timeframe: dict[str, int] = Field(default_factory=dict)
    facts_by_type: dict[str, int] = Field(default_factory=dict)
    candidates_by_type: dict[str, int] = Field(default_factory=dict)
    setups_by_status: dict[str, int] = Field(default_factory=dict)
    near_misses_by_reason: dict[str, int] = Field(default_factory=dict)
    breakdowns: dict[str, dict[str, int]] = Field(default_factory=dict)
    liquidity_raids: int = 0
    same_bar_sweeps: int = 0
    multi_bar_reclaims: int = 0
    shifts: int = 0
    same_bar_raid_shifts: int = 0
    linked_fvgs: int = 0
    failed_fvgs: int = 0
    reactions: int = 0
    late_shifts: int = 0
    late_reactions: int = 0
    ready_for_llm: int = 0
    invalidated_setups: int = 0
    expired_setups: int = 0


class M4ReplayResult(SchemaModel):
    run_id: NonEmptyStr
    symbol: NonEmptyStr
    started_at: AwareDatetime
    completed_at: AwareDatetime
    study_window: M4StudyWindow
    manifest: M4ExperimentManifest
    events: list[M4AuditEvent]
    near_misses: list[M4NearMiss]
    steps: list[M4ReplayStep]
    data_quality: list[DataQualityReport]
    summary: M4Summary

    @model_validator(mode="after")
    def validate_replay_causality(self) -> M4ReplayResult:
        if self.completed_at <= self.started_at:
            raise ValueError("replay completion must follow its first bar open")
        if any(item.available_at > self.completed_at for item in self.events):
            raise ValueError("audit event cannot be available after replay completion")
        if any(
            item.observed_at is not None
            and (
                item.observed_at < item.available_at
                or item.observed_at > self.completed_at
            )
            for item in self.events
        ):
            raise ValueError("audit observation must follow availability within replay")
        if self.events != sorted(
            self.events,
            key=lambda item: (item.available_at, item.kind.value, item.event_id),
        ):
            raise ValueError("audit events must be ordered by causal availability")
        event_map = {item.event_id: item for item in self.events}
        for step in self.steps:
            for event_id in step.event_ids:
                if event_id not in event_map:
                    raise ValueError("replay step references an unknown audit event")
                observed_at = (
                    event_map[event_id].observed_at or event_map[event_id].available_at
                )
                if observed_at != step.as_of:
                    raise ValueError("replay step contains an event from another close")
        return self

    def export_jsonl(self, output_directory: str | Path) -> dict[str, Path]:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        paths = {
            "events": output / "audit_events.jsonl",
            "near_misses": output / "near_misses.jsonl",
            "steps": output / "replay_steps.jsonl",
            "summary": output / "summary.json",
            "data_quality": output / "data_quality.json",
            "manifest": output / "manifest.json",
        }
        for key, items in (
            ("events", self.events),
            ("near_misses", self.near_misses),
            ("steps", self.steps),
        ):
            with paths[key].open("w", encoding="utf-8", newline="\n") as stream:
                for item in items:
                    stream.write(item.model_dump_json())
                    stream.write("\n")
        paths["summary"].write_text(
            self.summary.model_dump_json(indent=2), encoding="utf-8"
        )
        paths["data_quality"].write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in self.data_quality],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        paths["manifest"].write_text(
            self.manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        return paths


def _audit_id(kind: M4EventKind, record_id: str) -> str:
    raw = f"{kind.value}|{record_id}"
    return "audit-" + sha256(raw.encode()).hexdigest()[:24]


class _AuditCollector:
    def __init__(
        self,
        policy: M3Policy,
        *,
        study_window: M4StudyWindow | None = None,
        retain_research_events: bool = True,
    ) -> None:
        self.policy = policy
        self.study_window = study_window
        self.retain_research_events = retain_research_events
        self.events: dict[str, M4AuditEvent] = {}
        self._sequence: list[str] = []
        self._setup_origin_times: dict[str, datetime] = {}
        self._streamed_misses: list[M4NearMiss] = []
        self.analysis_research_count = 0
        self.analysis_research_reasons: Counter[str] = Counter()

    def add(self, event: M4AuditEvent) -> None:
        if event.kind == M4EventKind.SETUP and event.setup_candidate_id is not None:
            self._setup_origin_times[event.setup_candidate_id] = event.available_at
        if (
            not self.retain_research_events
            and event.kind == M4EventKind.FACT
            and event.category == FactType.RESEARCH_OBSERVATION.value
        ):
            if self.study_window is not None:
                phase = self.study_window.phase_at(event.available_at)
                origin = self._setup_origin_times.get(event.setup_candidate_id or "")
                included = phase == "analysis" and (
                    origin is None or self.study_window.phase_at(origin) == "analysis"
                )
                event = event.model_copy(
                    update={"study_phase": phase, "included_in_analysis": included}
                )
            self._streamed_misses.extend(self._near_misses_for_event(event))
            if event.included_in_analysis:
                self.analysis_research_count += 1
                self.analysis_research_reasons.update(event.reason_codes)
            return
        existing = self.events.get(event.event_id)
        if existing is not None and existing != event:
            raise ValueError(f"conflicting audit event: {event.event_id}")
        if existing is None:
            self.events[event.event_id] = event
            self._sequence.append(event.event_id)

    def checkpoint(self) -> int:
        return len(self._sequence)

    def since(self, checkpoint: int) -> tuple[M4AuditEvent, ...]:
        return tuple(
            self.events[event_id] for event_id in self._sequence[checkpoint:]
        )

    def add_bar(self, record: ExnessBarRecord) -> None:
        bar = record.bar
        record_id = f"{bar.timeframe.value}:{bar.open_time.isoformat()}"
        self.add(
            M4AuditEvent(
                event_id=_audit_id(M4EventKind.BAR, record_id),
                kind=M4EventKind.BAR,
                category="closed_bar",
                record_id=record_id,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                occurred_at=bar.open_time,
                available_at=bar.close_time,
                payload=record.model_dump(mode="json"),
            )
        )

    def add_m2(self, batch: M2DetectionBatch) -> None:
        for fact in batch.facts:
            self._add_fact(fact)
        for candidate in batch.candidates:
            self._add_candidate(candidate)

    def add_m3(self, batch: M3DetectionBatch) -> None:
        for fact in batch.facts:
            self._add_fact(fact)
        for candidate in batch.candidates:
            self._add_candidate(candidate)
        for episode in batch.raid_episodes_created:
            self._add_model(
                M4EventKind.RAID_EPISODE,
                "raid_episode",
                episode.raid_episode_id,
                episode.symbol,
                episode.created_at,
                episode.available_at,
                {
                    "reference_fact_id": episode.reference_fact_id,
                    "first_take_fact_id": episode.first_take_fact_id,
                    "first_raid_candidate_id": episode.first_raid_candidate_id,
                    "observation_states": {
                        key.value: value.value
                        for key, value in episode.observation_states.items()
                    },
                    "observation_extremes": {
                        key.value: value
                        for key, value in episode.observation_extremes.items()
                    },
                    "extreme": episode.extreme,
                },
                direction=episode.direction,
                raid_episode_id=episode.raid_episode_id,
            )
        for update in batch.raid_updates:
            self._add_model(
                M4EventKind.RAID_UPDATE,
                update.observation_state.value,
                update.update_id,
                batch.symbol,
                update.occurred_at,
                update.available_at,
                {
                    "observation_fact_id": update.observation_fact_id,
                    "raid_candidate_id": update.raid_candidate_id,
                    "observation_state": update.observation_state.value,
                    "breached_at": update.breached_at,
                    "extreme": update.extreme,
                },
                timeframe=update.observation_timeframe,
                raid_episode_id=update.raid_episode_id,
            )
        for setup in batch.setups_created:
            self._add_setup(setup)
        for link in batch.evidence_links:
            self._add_evidence_link(batch.symbol, batch.timeframe, link)
        for transition in batch.transitions:
            self._add_transition(batch.symbol, batch.timeframe, transition)
        for payload in batch.ready_for_llm:
            self._add_ready(payload)

    def _add_evidence_link(
        self,
        symbol: str,
        timeframe: Timeframe,
        link: SetupEvidenceLink,
    ) -> None:
        self._add_model(
            M4EventKind.SETUP_EVIDENCE,
            "setup_evidence_link",
            link.evidence_link_id,
            symbol,
            link.occurred_at,
            link.available_at,
            {
                "evidence_candidate_ids": link.evidence_candidate_ids,
                "evidence_fact_ids": link.evidence_fact_ids,
                "entry_zone_candidate_ids": link.entry_zone_candidate_ids,
                "reason_codes": link.reason_codes,
                "metrics": link.metrics,
            },
            timeframe=timeframe,
            setup_candidate_id=link.setup_candidate_id,
        )

    def _add_fact(self, fact: ObservableFact) -> None:
        observed_at = fact.metrics.get("observed_at")
        self._add_model(
            M4EventKind.FACT,
            fact.fact_type.value,
            fact.fact_id,
            fact.symbol,
            fact.occurred_at,
            fact.available_at,
            {
                "geometry": (
                    fact.geometry.model_dump(mode="json")
                    if fact.geometry is not None
                    else None
                ),
                "source_fact_ids": fact.source_fact_ids,
                "metrics": fact.metrics,
                "detector_name": fact.detector_name,
                "detector_version": fact.detector_version,
            },
            timeframe=fact.timeframe,
            direction=fact.direction,
            setup_candidate_id=fact.metrics.get("setup_candidate_id"),
            raid_episode_id=fact.metrics.get("raid_episode_id"),
            reason_codes=(
                [str(fact.metrics["reason_code"])]
                if fact.metrics.get("reason_code")
                else []
            ),
            observed_at=(
                datetime.fromisoformat(observed_at)
                if isinstance(observed_at, str)
                else fact.available_at
            ),
        )

    def _add_candidate(self, candidate: ConceptCandidate) -> None:
        self._add_model(
            M4EventKind.CANDIDATE,
            candidate.candidate_type.value,
            candidate.candidate_id,
            candidate.symbol,
            candidate.occurred_at,
            candidate.available_at,
            {
                "evidence_fact_ids": candidate.evidence_fact_ids,
                "related_candidate_ids": candidate.related_candidate_ids,
                "raw_features": candidate.raw_features,
                "machine_labels": candidate.machine_labels,
            },
            timeframe=candidate.timeframe,
            direction=candidate.direction,
            setup_candidate_id=candidate.raw_features.get("setup_candidate_id"),
        )

    def _add_setup(self, setup: SetupCandidate) -> None:
        self._add_model(
            M4EventKind.SETUP,
            setup.status.value,
            setup.setup_candidate_id,
            setup.symbol,
            setup.created_at,
            setup.available_at,
            {
                "setup_type": setup.setup_type,
                "setup_version": setup.setup_version,
                "entry_timeframe": setup.entry_timeframe.value,
                "evidence_candidate_ids": setup.evidence_candidate_ids,
                "evidence_fact_ids": setup.evidence_fact_ids,
                "entry_zone_candidate_ids": setup.entry_zone_candidate_ids,
                "target_candidate_ids": setup.target_candidate_ids,
                "hard_invalidation_price": setup.hard_invalidation_price,
                "expires_at": setup.expires_at,
                "metrics": setup.metrics,
            },
            timeframe=setup.setup_timeframe,
            direction=setup.direction,
            setup_candidate_id=setup.setup_candidate_id,
            raid_episode_id=setup.metrics.get("raid_episode_id"),
        )

    def _add_transition(
        self,
        symbol: str,
        timeframe: Timeframe,
        transition: SetupTransition,
    ) -> None:
        self._add_model(
            M4EventKind.TRANSITION,
            transition.to_status.value,
            transition.transition_id,
            symbol,
            transition.occurred_at,
            transition.available_at,
            {
                "from_status": (
                    transition.from_status.value
                    if transition.from_status is not None
                    else None
                ),
                "to_status": transition.to_status.value,
                "evidence_candidate_ids": transition.evidence_candidate_ids,
                "evidence_fact_ids": transition.evidence_fact_ids,
                "entry_zone_candidate_ids": transition.entry_zone_candidate_ids,
                "hard_invalidation_price": transition.hard_invalidation_price,
                "reason_codes": transition.reason_codes,
                "expires_at": transition.expires_at,
                "metrics": transition.metrics,
            },
            timeframe=timeframe,
            setup_candidate_id=transition.setup_candidate_id,
            reason_codes=transition.reason_codes,
        )

    def _add_ready(self, payload: ReadyForLLMPayload) -> None:
        self._add_model(
            M4EventKind.READY_PAYLOAD,
            "ready_for_llm",
            payload.payload_id,
            payload.setup.symbol,
            payload.setup.created_at,
            payload.as_of,
            {
                "setup": {
                    "setup_candidate_id": payload.setup.setup_candidate_id,
                    "status": payload.setup.status.value,
                    "evidence_candidate_ids": payload.setup.evidence_candidate_ids,
                    "evidence_fact_ids": payload.setup.evidence_fact_ids,
                    "entry_zone_candidate_ids": payload.setup.entry_zone_candidate_ids,
                },
                "targets": [item.model_dump(mode="json") for item in payload.targets],
                "context": payload.context,
            },
            timeframe=payload.setup.setup_timeframe,
            direction=payload.setup.direction,
            setup_candidate_id=payload.setup.setup_candidate_id,
        )

    def _add_model(
        self,
        kind: M4EventKind,
        category: str,
        record_id: str,
        symbol: str,
        occurred_at: datetime,
        available_at: datetime,
        payload: dict[str, Any],
        *,
        timeframe: Timeframe | None = None,
        direction: Direction | None = None,
        setup_candidate_id: str | None = None,
        raid_episode_id: str | None = None,
        reason_codes: Sequence[str] = (),
        observed_at: datetime | None = None,
    ) -> None:
        self.add(
            M4AuditEvent(
                event_id=_audit_id(kind, record_id),
                kind=kind,
                category=category,
                record_id=record_id,
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                occurred_at=occurred_at,
                available_at=available_at,
                observed_at=observed_at or available_at,
                setup_candidate_id=setup_candidate_id,
                raid_episode_id=raid_episode_id,
                reason_codes=list(reason_codes),
                payload=payload,
            )
        )

    def ordered(self) -> list[M4AuditEvent]:
        return sorted(
            self.events.values(),
            key=lambda item: (item.available_at, item.kind.value, item.event_id),
        )

    def near_misses(self) -> list[M4NearMiss]:
        misses: list[M4NearMiss] = list(self._streamed_misses)
        for event in self.ordered():
            misses.extend(self._near_misses_for_event(event))
        return misses

    def _near_misses_for_event(self, event: M4AuditEvent) -> list[M4NearMiss]:
        misses: list[M4NearMiss] = []
        reasons = list(event.reason_codes)
        if event.kind == M4EventKind.TRANSITION and event.category == "expired":
            reasons = reasons or ["EXPIRED_SETUP"]
        for reason in reasons:
            is_reclaim = reason == "RECLAIM_OUTSIDE_WINDOW"
            is_research = event.category == FactType.RESEARCH_OBSERVATION.value
            is_expiry = (
                event.kind == M4EventKind.TRANSITION and event.category == "expired"
            )
            if not (is_reclaim or is_research or is_expiry):
                continue
            metrics = event.payload.get("metrics", {})
            distance: int | None = None
            threshold: int | None = None
            if is_reclaim:
                distance = int(metrics.get("reclaim_span_bars", 0))
                threshold = self.policy.reclaim_window_bars
            elif "SHIFT" in reason and metrics.get("bars_after_raid") is not None:
                distance = int(metrics["bars_after_raid"])
                if event.timeframe in self.policy.shift_window_bars:
                    threshold = self.policy.shift_window_bars[event.timeframe]
            elif "REACTION_OUTSIDE" in reason:
                distance = int(metrics.get("reaction_lag_bars", 0))
                threshold = self.policy.reaction_confirmation_bars
            elif reason in {"NO_CAUSALLY_LINKED_FVG", "FVG_LINK_WINDOW_EXPIRED"}:
                distance = int(metrics.get("bars_after_shift", 0))
                threshold = self.policy.repricing_max_lag_bars + 1
            elif reason == "LATE_FVG_AFTER_TERMINAL":
                threshold = self.policy.repricing_max_lag_bars + 1
                distance = threshold + int(metrics.get("bars_after_terminal", 0))
            elif (
                reason == "LATE_RETRACE_AFTER_TERMINAL"
                and metrics.get("terminal_status") == SetupStatus.EXPIRED.value
                and event.timeframe in self.policy.fvg_expiry_bars
            ):
                threshold = self.policy.fvg_expiry_bars[event.timeframe]
                distance = threshold + int(metrics.get("bars_after_terminal", 0))
            elif (
                reason == "FVG_RETRACE_WINDOW_EXPIRED"
                and event.timeframe in self.policy.fvg_expiry_bars
            ):
                threshold = self.policy.fvg_expiry_bars[event.timeframe]
                distance = threshold
            elif metrics.get("bars_after_terminal") is not None:
                distance = int(metrics["bars_after_terminal"])
            excess = (
                max(0, distance - threshold)
                if distance is not None and threshold is not None
                else None
            )
            miss_id = "near-miss-" + sha256(
                f"{event.event_id}|{reason}".encode()
            ).hexdigest()[:24]
            misses.append(
                M4NearMiss(
                    near_miss_id=miss_id,
                    source_event_id=event.event_id,
                    symbol=event.symbol,
                    timeframe=event.timeframe,
                    occurred_at=event.occurred_at,
                    available_at=event.available_at,
                    reason_code=reason,
                    setup_candidate_id=event.setup_candidate_id,
                    distance_bars=distance,
                    threshold_bars=threshold,
                    excess_bars=excess,
                    study_phase=event.study_phase,
                    included_in_analysis=event.included_in_analysis,
                    payload={
                        "category": event.category,
                        "metrics": dict(metrics),
                        "reason_codes": list(event.reason_codes),
                    },
                )
            )
        return misses


def _summary(
    events: Sequence[M4AuditEvent],
    misses: Sequence[M4NearMiss],
    setups: Sequence[SetupCandidate],
    *,
    streamed_research_count: int = 0,
    streamed_research_reasons: Mapping[str, int] | None = None,
) -> M4Summary:
    bars = [item for item in events if item.kind == M4EventKind.BAR]
    facts = [item for item in events if item.kind == M4EventKind.FACT]
    candidates = [item for item in events if item.kind == M4EventKind.CANDIDATE]
    transitions = [item for item in events if item.kind == M4EventKind.TRANSITION]
    raids = [
        item
        for item in candidates
        if item.category == CandidateType.LIQUIDITY_EVENT.value
    ]
    shifts = [item for item in candidates if item.category == CandidateType.SHIFT.value]
    reactions = [item for item in facts if item.category == FactType.FVG_REACTION.value]
    research_reasons = Counter(reason for item in facts for reason in item.reason_codes)
    research_reasons.update(streamed_research_reasons or {})
    fact_counts = Counter(item.category for item in facts)
    fact_counts[FactType.RESEARCH_OBSERVATION.value] += streamed_research_count
    setup_events = [item for item in events if item.kind == M4EventKind.SETUP]
    ready_payloads = [item for item in events if item.kind == M4EventKind.READY_PAYLOAD]
    session_labels: list[str] = []
    session_overlaps: list[str] = []
    for item in ready_payloads:
        context = item.payload.get("context", {})
        labels = context.get("sessions") or []
        if not labels and context.get("session"):
            labels = [context["session"]]
        session_labels.extend(str(label) for label in labels)
        if len(labels) > 1:
            session_overlaps.append("+".join(str(label) for label in labels))
    breakdowns = {
        "setup_timeframe": dict(
            Counter(
                item.timeframe.value
                for item in setup_events
                if item.timeframe is not None
            )
        ),
        "setup_direction": dict(
            Counter(
                item.direction.value
                for item in setup_events
                if item.direction is not None
            )
        ),
        "raid_detection_timeframe": dict(
            Counter(
                item.timeframe.value for item in raids if item.timeframe is not None
            )
        ),
        "raid_reference_timeframe": dict(
            Counter(
                str(item.payload["raw_features"].get("reference_timeframe", "unknown"))
                for item in raids
            )
        ),
        "raid_type": dict(
            Counter(
                (
                    "same_bar"
                    if item.payload["raw_features"].get("same_bar_reclaim")
                    else "multi_bar"
                )
                for item in raids
            )
        ),
        "shift_timeframe": dict(
            Counter(
                item.timeframe.value for item in shifts if item.timeframe is not None
            )
        ),
        "shift_effective_rank": dict(
            Counter(
                str(
                    item.payload["raw_features"].get(
                        "effective_rank_as_of_break", "mixed_or_unknown"
                    )
                    or "mixed_or_unknown"
                )
                for item in shifts
            )
        ),
        "shift_lag_bars": dict(
            Counter(
                str(item.payload["raw_features"].get("bars_after_raid", "unknown"))
                for item in shifts
            )
        ),
        "fvg_lifecycle": dict(
            Counter(
                str(item.payload.get("metrics", {}).get("lifecycle", "unknown"))
                for item in reactions
            )
        ),
        "reaction_lag_bars": dict(
            Counter(
                str(item.payload.get("metrics", {}).get("reaction_lag_bars", "none"))
                for item in reactions
            )
        ),
        "near_miss_timeframe": dict(
            Counter(
                item.timeframe.value for item in misses if item.timeframe is not None
            )
        ),
        "session": dict(
            Counter(session_labels or (["unspecified"] if ready_payloads else []))
        ),
        "session_overlap": dict(Counter(session_overlaps)),
    }
    return M4Summary(
        bars=len(bars),
        bars_by_timeframe=dict(
            Counter(item.timeframe.value for item in bars if item.timeframe)
        ),
        facts_by_type=dict(fact_counts),
        candidates_by_type=dict(Counter(item.category for item in candidates)),
        setups_by_status=dict(Counter(item.status.value for item in setups)),
        near_misses_by_reason=dict(Counter(item.reason_code for item in misses)),
        breakdowns=breakdowns,
        liquidity_raids=len(raids),
        same_bar_sweeps=sum(
            bool(item.payload["raw_features"].get("same_bar_reclaim")) for item in raids
        ),
        multi_bar_reclaims=sum(
            not bool(item.payload["raw_features"].get("same_bar_reclaim"))
            for item in raids
        ),
        shifts=len(shifts),
        same_bar_raid_shifts=sum(
            bool(item.payload["raw_features"].get("same_bar_raid_shift"))
            for item in shifts
        ),
        linked_fvgs=sum(
            item.category == CandidateType.FVG.value for item in candidates
        ),
        failed_fvgs=sum(
            item.payload.get("metrics", {}).get("lifecycle") == "failed"
            for item in reactions
        ),
        reactions=sum(
            item.payload.get("metrics", {}).get("lifecycle") == "reacted"
            for item in reactions
        ),
        late_shifts=research_reasons["LATE_SHIFT_AFTER_TERMINAL"],
        late_reactions=research_reasons["FVG_REACTION_OUTSIDE_CONFIRMATION_WINDOW"],
        ready_for_llm=sum(item.category == "ready_for_llm" for item in transitions),
        invalidated_setups=sum(
            item.status == SetupStatus.INVALIDATED for item in setups
        ),
        expired_setups=sum(item.status == SetupStatus.EXPIRED for item in setups),
    )


class M4ReplayEngine:
    """Append bars at their close and run the production M2/M3 path once."""

    version = "0.2.0"
    _timeframe_priority: ClassVar[dict[Timeframe, int]] = {
        Timeframe.M1: 0,
        Timeframe.M5: 1,
        Timeframe.M15: 2,
        Timeframe.H1: 3,
        Timeframe.H4: 4,
        Timeframe.D1: 5,
        Timeframe.W1: 6,
    }

    def __init__(
        self,
        *,
        symbol: str,
        symbol_metadata: M4SymbolMetadata,
        git_commit_sha: str,
        initial_facts: Sequence[ObservableFact] = (),
        target_candidates: Sequence[TargetCandidate] = (),
        context: Mapping[str, Any] | None = None,
        candle_config: CandleFeatureConfig | None = None,
        displacement_thresholds: DisplacementThresholds | None = None,
        m3_policy: M3Policy | None = None,
        adjacency_policy: BarAdjacencyPolicy | None = None,
        reference_lifecycle_policy: ReferenceLifecyclePolicy | None = None,
        context_provider: TemporalContextProvider | None = None,
        reference_builder: CausalReferenceBuilder | None = None,
        retain_research_facts: bool = True,
    ) -> None:
        if symbol_metadata.symbol != symbol:
            raise ValueError("symbol metadata must match the replay symbol")
        if not git_commit_sha.strip():
            raise ValueError("git_commit_sha is required for a reproducible replay")
        self.symbol = symbol
        self.symbol_metadata = symbol_metadata.model_copy(deep=True)
        self.git_commit_sha = git_commit_sha.strip()
        self.context_provider = context_provider
        self.reference_builder = reference_builder or CausalReferenceBuilder()
        self.base_context = dict(context or {})
        self.adjacency_policy = adjacency_policy
        self.reference_lifecycle_policy = (
            reference_lifecycle_policy or ReferenceLifecyclePolicy()
        )
        self.retain_research_facts = retain_research_facts
        self.target_candidates = tuple(
            item.model_copy(deep=True) for item in target_candidates
        )
        self.initial_facts = tuple(item.model_copy(deep=True) for item in initial_facts)
        self.feed = ClosedBarFeed(symbol)
        self.facts = FactStore()
        self.facts.extend(self.initial_facts)
        self.candidates = CandidateStore()
        self.setups = SetupStore()
        self.raids = RaidEpisodeStore()
        self.policy = m3_policy or M3Policy()
        self.m2 = M2PrimitivePipeline(
            bar_feed=self.feed,
            fact_store=self.facts,
            candidate_store=self.candidates,
            tick_size=self.symbol_metadata.trade_tick_size,
            candle_config=candle_config,
            displacement_thresholds=displacement_thresholds,
            adjacency_policy=adjacency_policy,
            reference_lifecycle_policy=self.reference_lifecycle_policy,
        )
        self.m3 = M3SetupPipeline(
            bar_feed=self.feed,
            fact_store=self.facts,
            candidate_store=self.candidates,
            setup_store=self.setups,
            raid_store=self.raids,
            tick_size=self.symbol_metadata.trade_tick_size,
            policy=self.policy,
            target_candidates=target_candidates,
            context=self.base_context,
            retain_research_facts=retain_research_facts,
        )
        self._has_run = False

    def run(
        self,
        inputs: Sequence[ExnessDataset | ExnessBarRecord | OHLCBar],
        *,
        study_window: M4StudyWindow,
        progress_callback: Callable[[int, int, datetime], None] | None = None,
        retain_steps: bool = True,
    ) -> M4ReplayResult:
        if self._has_run:
            raise ValueError("M4ReplayEngine instances are single-run")
        self._has_run = True
        records: list[ExnessBarRecord] = []
        direct_records: list[ExnessBarRecord] = []
        quality: list[DataQualityReport] = []
        datasets: list[ExnessDataset] = []
        for item in inputs:
            if isinstance(item, ExnessDataset):
                datasets.append(item)
                records.extend(item.records)
                quality.append(item.quality)
            elif isinstance(item, ExnessBarRecord):
                records.append(item)
                direct_records.append(item)
            else:
                direct = ExnessBarRecord(
                    bar=item,
                    source_name="direct",
                    source_row_number=2,
                )
                records.append(direct)
                direct_records.append(direct)
        if not records:
            raise ValueError("M4 replay requires at least one closed bar")
        if any(item.bar.symbol != self.symbol for item in records):
            raise ValueError("all replay bars must match the engine symbol")
        records = [
            item
            for item in records
            if item.bar.open_time >= study_window.replay_start
            and (
                study_window.analysis_end is None
                or item.bar.close_time <= study_window.analysis_end
            )
        ]
        included_keys = {(item.bar.timeframe, item.bar.open_time) for item in records}
        direct_records = [
            item
            for item in direct_records
            if (item.bar.timeframe, item.bar.open_time) in included_keys
        ]
        if not records:
            raise ValueError("study window excludes every replay bar")
        if min(item.bar.open_time for item in records) > study_window.replay_start:
            raise ValueError("source data does not cover replay_start")
        if max(item.bar.close_time for item in records) < study_window.analysis_start:
            raise ValueError("source data does not reach analysis_start")
        keys = [(item.bar.timeframe, item.bar.open_time) for item in records]
        if len(keys) != len(set(keys)):
            raise ValueError("replay input contains duplicate timeframe/open_time")
        records.sort(
            key=lambda item: (
                item.bar.close_time,
                self._timeframe_priority[item.bar.timeframe],
                item.bar.open_time,
            )
        )
        collector = _AuditCollector(
            self.policy,
            study_window=study_window,
            retain_research_events=self.retain_research_facts,
        )
        completed_at = records[-1].bar.close_time
        for fact in self.initial_facts:
            if fact.available_at <= completed_at:
                collector._add_fact(fact)
        steps: list[M4ReplayStep] = []
        supported_m3 = set(self.policy.raid_observation_timeframes) | set(
            self.policy.post_terminal_research_bars
        )
        minimum_bars = max(2, self.m2.candle_features.config.baseline_period) + 1
        processed_records = 0
        total_records = len(records)
        for as_of, grouped in groupby(records, key=lambda item: item.bar.close_time):
            current = list(grouped)
            checkpoint = collector.checkpoint() if retain_steps else 0
            for record in current:
                self.feed.append(record.bar, observed_at=as_of)
                collector.add_bar(record)
                reference_facts = self.reference_builder.ingest(record.bar)
                self.facts.extend(reference_facts)
                for fact in reference_facts:
                    collector._add_fact(fact)
            processed: list[Timeframe] = []
            for record in current:
                timeframe = record.bar.timeframe
                if len(self.feed.bars(timeframe, as_of=as_of)) < minimum_bars:
                    continue
                m2_batch = self.m2.process_latest(timeframe=timeframe, as_of=as_of)
                collector.add_m2(m2_batch)
                if timeframe in supported_m3:
                    if self.context_provider is not None:
                        self.m3.context = (
                            self.base_context | self.context_provider.context_at(as_of)
                        )
                    m3_batch = self.m3.process_latest(timeframe=timeframe, as_of=as_of)
                    collector.add_m3(m3_batch)
                processed.append(timeframe)
            if retain_steps:
                event_ids = [
                    event.event_id
                    for event in collector.since(checkpoint)
                    if (event.observed_at or event.available_at) == as_of
                ]
                steps.append(
                    M4ReplayStep(
                        as_of=as_of,
                        study_phase=study_window.phase_at(as_of),
                        processed_timeframes=processed,
                        event_ids=event_ids,
                    )
                )
            processed_records += len(current)
            if progress_callback is not None:
                progress_callback(processed_records, total_records, as_of)
        raw_events = collector.ordered()
        setup_origins = {
            item.setup_candidate_id: item.available_at
            for item in raw_events
            if item.kind == M4EventKind.SETUP and item.setup_candidate_id is not None
        }
        events: list[M4AuditEvent] = []
        for item in raw_events:
            phase = study_window.phase_at(item.available_at)
            setup_origin = setup_origins.get(item.setup_candidate_id)
            included = phase == "analysis" and (
                setup_origin is None
                or study_window.phase_at(setup_origin) == "analysis"
            )
            temporal = (
                self.context_provider.context_at(item.available_at)
                if self.context_provider is not None
                else {}
            )
            events.append(
                item.model_copy(
                    update={
                        "study_phase": phase,
                        "included_in_analysis": included,
                        "temporal_context": temporal,
                    }
                )
            )
        event_map = {item.event_id: item for item in events}
        misses: list[M4NearMiss] = []
        for item in collector.near_misses():
            source = event_map.get(item.source_event_id)
            misses.append(
                item
                if source is None
                else item.model_copy(
                    update={
                        "study_phase": source.study_phase,
                        "included_in_analysis": source.included_in_analysis,
                    }
                )
            )
        final_setups = self.setups.visible(as_of=completed_at, symbol=self.symbol)
        eligible_setup_ids = {
            item.setup_candidate_id
            for item in events
            if item.kind == M4EventKind.SETUP and item.included_in_analysis
        }
        final_setups = [
            item
            for item in final_setups
            if item.setup_candidate_id in eligible_setup_ids
        ]
        source_data = _source_fingerprints(datasets, direct_records)
        manifest = M4ExperimentManifest(
            git_commit_sha=self.git_commit_sha,
            m2_version=self.m2.version,
            m3_version=self.m3.version,
            m4_version=self.version,
            symbol_metadata=self.symbol_metadata,
            study_window=study_window,
            source_data=source_data,
            candle_config=self.m2.candle_features.config.model_dump(mode="json"),
            displacement_config=self.m2.displacement.thresholds.model_dump(mode="json"),
            m3_policy=self.policy.model_dump(mode="json"),
            adjacency_calendar_policy=_adjacency_manifest(self.adjacency_policy),
            reference_policy={
                "lifecycle": self.reference_lifecycle_policy.model_dump(mode="json"),
                "builder": self.reference_builder.manifest(),
                "initial_facts": [
                    item.model_dump(mode="json") for item in self.initial_facts
                ],
            },
            context_policy=(
                self.context_provider.manifest()
                if self.context_provider is not None
                else {"provider": "static", "context": self.base_context}
            ),
            target_policy={
                "kind": "explicit_candidates",
                "targets": [
                    item.model_dump(mode="json") for item in self.target_candidates
                ],
            },
        )
        run_hash = manifest.fingerprint()[:24]
        analysis_events = [item for item in events if item.included_in_analysis]
        analysis_misses = [item for item in misses if item.included_in_analysis]
        return M4ReplayResult(
            run_id=f"m4-replay-{run_hash}",
            symbol=self.symbol,
            started_at=min(item.bar.open_time for item in records),
            completed_at=completed_at,
            study_window=study_window,
            manifest=manifest,
            events=events,
            near_misses=misses,
            steps=steps,
            data_quality=quality,
            summary=_summary(
                analysis_events,
                analysis_misses,
                final_setups,
                streamed_research_count=collector.analysis_research_count,
                streamed_research_reasons=collector.analysis_research_reasons,
            ),
        )


def _source_fingerprints(
    datasets: Sequence[ExnessDataset], direct_records: Sequence[ExnessBarRecord]
) -> list[M4SourceFingerprint]:
    fingerprints = [
        M4SourceFingerprint(
            source_name=item.quality.source_name,
            content_sha256=item.content_sha256,
            rows=item.source_rows or len(item.records),
            timeframes=sorted(
                {record.bar.timeframe for record in item.records},
                key=lambda value: value.value,
            ),
        )
        for item in datasets
    ]
    if not direct_records:
        return fingerprints
    payload = json.dumps(
        [item.model_dump(mode="json") for item in direct_records],
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprints.append(
        M4SourceFingerprint(
            source_name="direct",
            content_sha256=sha256(payload.encode()).hexdigest(),
            rows=len(direct_records),
            timeframes=sorted(
                {item.bar.timeframe for item in direct_records},
                key=lambda value: value.value,
            ),
        )
    )
    return fingerprints


def _adjacency_manifest(policy: BarAdjacencyPolicy | None) -> dict[str, Any]:
    if policy is None:
        return {"policy": "WallClockAdjacencyPolicy"}
    payload: dict[str, Any] = {"policy": type(policy).__name__}
    if isinstance(policy, MarketSequenceAdjacencyPolicy):
        payload["calendar"] = policy.calendar.model_dump(mode="json")
    return payload
