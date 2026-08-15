from __future__ import annotations

from collections.abc import Iterable

from pydantic import AwareDatetime

from .candidates import ConceptCandidate
from .enums import CandidateType, FactType, Timeframe
from .facts import ObservableFact


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
            for fact in sorted(records, key=lambda item: (item.available_at, item.fact_id))
        )

    def as_mapping(self) -> dict[str, ObservableFact]:
        return {key: value.model_copy(deep=True) for key, value in self._records.items()}


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
            and (
                candidate_type is None
                or candidate.candidate_type == candidate_type
            )
        )
        return tuple(
            candidate.model_copy(deep=True)
            for candidate in sorted(
                records,
                key=lambda item: (item.available_at, item.candidate_id),
            )
        )

    def as_mapping(self) -> dict[str, ConceptCandidate]:
        return {key: value.model_copy(deep=True) for key, value in self._records.items()}
