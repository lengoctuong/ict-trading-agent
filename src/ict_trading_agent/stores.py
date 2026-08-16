from __future__ import annotations

from collections.abc import Iterable

from pydantic import AwareDatetime

from .candidates import ConceptCandidate, SetupCandidate
from .enums import CandidateType, FactType, Timeframe
from .facts import ObservableFact
from .lifecycle import SetupTransition, assert_setup_transition


class DuplicateRecordError(ValueError):
    pass


class FactStore:
    """Minimal append-only store contract for replay and reducer tests."""

    def __init__(self) -> None:
        self._records: dict[str, ObservableFact] = {}

    def append(self, fact: ObservableFact) -> None:
        if fact.fact_id in self._records:
            raise DuplicateRecordError(f"duplicate fact_id: {fact.fact_id}")
        self._records[fact.fact_id] = fact.model_copy(deep=True)

    def extend(self, facts: Iterable[ObservableFact]) -> None:
        for fact in facts:
            self.append(fact)

    def get(self, fact_id: str) -> ObservableFact:
        return self._records[fact_id].model_copy(deep=True)

    def visible(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        fact_type: FactType | None = None,
    ) -> tuple[ObservableFact, ...]:
        records = (
            fact
            for fact in self._records.values()
            if fact.available_at <= as_of
            and (symbol is None or fact.symbol == symbol)
            and (timeframe is None or fact.timeframe == timeframe)
            and (fact_type is None or fact.fact_type == fact_type)
        )
        return tuple(
            fact.model_copy(deep=True)
            for fact in sorted(
                records, key=lambda item: (item.available_at, item.fact_id)
            )
        )

    def as_mapping(self) -> dict[str, ObservableFact]:
        return {
            key: value.model_copy(deep=True) for key, value in self._records.items()
        }


class CandidateStore:
    def __init__(self) -> None:
        self._records: dict[str, ConceptCandidate] = {}

    def append(self, candidate: ConceptCandidate) -> None:
        if candidate.candidate_id in self._records:
            raise DuplicateRecordError(
                f"duplicate candidate_id: {candidate.candidate_id}"
            )
        self._records[candidate.candidate_id] = candidate.model_copy(deep=True)

    def extend(self, candidates: Iterable[ConceptCandidate]) -> None:
        for candidate in candidates:
            self.append(candidate)

    def visible(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        candidate_type: CandidateType | None = None,
    ) -> tuple[ConceptCandidate, ...]:
        records = (
            candidate
            for candidate in self._records.values()
            if candidate.available_at <= as_of
            and (symbol is None or candidate.symbol == symbol)
            and (timeframe is None or candidate.timeframe == timeframe)
            and (candidate_type is None or candidate.candidate_type == candidate_type)
        )
        return tuple(
            candidate.model_copy(deep=True)
            for candidate in sorted(
                records,
                key=lambda item: (item.available_at, item.candidate_id),
            )
        )

    def as_mapping(self) -> dict[str, ConceptCandidate]:
        return {
            key: value.model_copy(deep=True) for key, value in self._records.items()
        }


class SetupStore:
    """Append-only setup origins and transition events with reconstructed views."""

    def __init__(self) -> None:
        self._setups: dict[str, SetupCandidate] = {}
        self._transitions: dict[str, SetupTransition] = {}
        self._processed_bars: set[tuple[Timeframe, AwareDatetime]] = set()

    def append_setup(self, setup: SetupCandidate) -> None:
        if setup.setup_candidate_id in self._setups:
            raise DuplicateRecordError(
                f"duplicate setup_candidate_id: {setup.setup_candidate_id}"
            )
        self._setups[setup.setup_candidate_id] = setup.model_copy(deep=True)

    def append_transition(self, transition: SetupTransition) -> None:
        if transition.transition_id in self._transitions:
            raise DuplicateRecordError(
                f"duplicate transition_id: {transition.transition_id}"
            )
        current = self.current(transition.setup_candidate_id)
        if transition.from_status != current.status:
            raise ValueError(
                "transition.from_status does not match current setup state"
            )
        assert_setup_transition(current.status, transition.to_status)
        if transition.available_at < current.available_at:
            raise ValueError("setup transitions must be appended in availability order")
        self._transitions[transition.transition_id] = transition.model_copy(deep=True)

    def get_origin(self, setup_candidate_id: str) -> SetupCandidate:
        return self._setups[setup_candidate_id].model_copy(deep=True)

    def transitions(self, setup_candidate_id: str) -> tuple[SetupTransition, ...]:
        return tuple(
            item.model_copy(deep=True)
            for item in self._transitions.values()
            if item.setup_candidate_id == setup_candidate_id
        )

    def current(
        self,
        setup_candidate_id: str,
        *,
        as_of: AwareDatetime | None = None,
    ) -> SetupCandidate:
        setup = self.get_origin(setup_candidate_id)
        candidate_ids = list(setup.evidence_candidate_ids)
        fact_ids = list(setup.evidence_fact_ids)
        zone_ids = list(setup.entry_zone_candidate_ids)
        metrics = dict(setup.metrics)
        status = setup.status
        available_at = setup.available_at
        expires_at = setup.expires_at
        for transition in self.transitions(setup_candidate_id):
            if as_of is not None and transition.available_at > as_of:
                continue
            if transition.from_status != status:
                raise ValueError("stored setup transition chain is inconsistent")
            status = transition.to_status
            available_at = transition.available_at
            candidate_ids.extend(transition.evidence_candidate_ids)
            fact_ids.extend(transition.evidence_fact_ids)
            zone_ids.extend(transition.entry_zone_candidate_ids)
            metrics.update(transition.metrics)
            if transition.expires_at is not None:
                expires_at = transition.expires_at
        return setup.model_copy(
            update={
                "status": status,
                "available_at": available_at,
                "evidence_candidate_ids": list(dict.fromkeys(candidate_ids)),
                "evidence_fact_ids": list(dict.fromkeys(fact_ids)),
                "entry_zone_candidate_ids": list(dict.fromkeys(zone_ids)),
                "expires_at": expires_at,
                "metrics": metrics,
            },
            deep=True,
        )

    def visible(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
    ) -> tuple[SetupCandidate, ...]:
        setups = (
            self.current(setup_id, as_of=as_of)
            for setup_id, origin in self._setups.items()
            if origin.created_at <= as_of
            and (symbol is None or origin.symbol == symbol)
            and (timeframe is None or origin.setup_timeframe == timeframe)
        )
        return tuple(
            setup
            for setup in sorted(
                setups,
                key=lambda item: (item.created_at, item.setup_candidate_id),
            )
            if setup.available_at <= as_of
        )

    def as_mappings(
        self,
    ) -> tuple[dict[str, SetupCandidate], dict[str, SetupTransition]]:
        return (
            {key: value.model_copy(deep=True) for key, value in self._setups.items()},
            {
                key: value.model_copy(deep=True)
                for key, value in self._transitions.items()
            },
        )

    def mark_bar_processed(
        self,
        timeframe: Timeframe,
        open_time: AwareDatetime,
    ) -> None:
        key = (timeframe, open_time)
        if key in self._processed_bars:
            raise DuplicateRecordError("M3 bar was already processed")
        self._processed_bars.add(key)

    def is_bar_processed(
        self,
        timeframe: Timeframe,
        open_time: AwareDatetime,
    ) -> bool:
        return (timeframe, open_time) in self._processed_bars

    def last_processed(self, timeframe: Timeframe) -> AwareDatetime | None:
        return max(
            (
                opened
                for item_timeframe, opened in self._processed_bars
                if item_timeframe == timeframe
            ),
            default=None,
        )
