from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import AwareDatetime

from .candidates import (
    ConceptCandidate,
    RaidEpisode,
    RaidEpisodeUpdate,
    SetupCandidate,
)
from .enums import CandidateType, FactType, Timeframe
from .facts import ObservableFact
from .lifecycle import SetupTransition, assert_setup_transition


class DuplicateRecordError(ValueError):
    pass


class FactStore:
    """Minimal append-only store contract for replay and reducer tests."""

    def __init__(self) -> None:
        self._records: dict[str, ObservableFact] = {}
        self._by_symbol: dict[str, list[str]] = defaultdict(list)
        self._by_timeframe: dict[Timeframe | None, list[str]] = defaultdict(list)
        self._by_fact_type: dict[FactType, list[str]] = defaultdict(list)
        self._by_available_at: dict[AwareDatetime, list[str]] = defaultdict(list)

    def append(self, fact: ObservableFact) -> None:
        if fact.fact_id in self._records:
            raise DuplicateRecordError(f"duplicate fact_id: {fact.fact_id}")
        self._records[fact.fact_id] = fact.model_copy(deep=True)
        self._by_symbol[fact.symbol].append(fact.fact_id)
        self._by_timeframe[fact.timeframe].append(fact.fact_id)
        self._by_fact_type[fact.fact_type].append(fact.fact_id)
        self._by_available_at[fact.available_at].append(fact.fact_id)

    def extend(self, facts: Iterable[ObservableFact]) -> None:
        for fact in facts:
            self.append(fact)

    def get(self, fact_id: str) -> ObservableFact:
        return self._records[fact_id].model_copy(deep=True)

    def get_optional(self, fact_id: str) -> ObservableFact | None:
        fact = self._records.get(fact_id)
        return fact.model_copy(deep=True) if fact is not None else None

    def get_view(self, fact_id: str) -> ObservableFact:
        return self._records[fact_id]

    def get_optional_view(self, fact_id: str) -> ObservableFact | None:
        return self._records.get(fact_id)

    def contains(self, fact_id: str) -> bool:
        return fact_id in self._records

    def existing_ids(self, fact_ids: Iterable[str]) -> set[str]:
        return {fact_id for fact_id in fact_ids if fact_id in self._records}

    def visible(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        fact_type: FactType | None = None,
        fact_types: Iterable[FactType] | None = None,
    ) -> tuple[ObservableFact, ...]:
        requested_types = set(fact_types or ())
        if fact_type is not None:
            requested_types.add(fact_type)
        indexed_groups: list[list[str]] = []
        if symbol is not None:
            indexed_groups.append(self._by_symbol.get(symbol, []))
        if timeframe is not None:
            indexed_groups.append(self._by_timeframe.get(timeframe, []))
        if requested_types:
            indexed_groups.append(
                [
                    fact_id
                    for requested_type in requested_types
                    for fact_id in self._by_fact_type.get(requested_type, [])
                ]
            )
        candidate_ids = (
            min(indexed_groups, key=len) if indexed_groups else self._records.keys()
        )
        records = (
            self._records[fact_id]
            for fact_id in candidate_ids
            if (
                (fact := self._records[fact_id]).available_at <= as_of
                and (symbol is None or fact.symbol == symbol)
                and (timeframe is None or fact.timeframe == timeframe)
                and (not requested_types or fact.fact_type in requested_types)
            )
        )
        return tuple(
            fact.model_copy(deep=True)
            for fact in sorted(
                records, key=lambda item: (item.available_at, item.fact_id)
            )
        )

    def visible_views(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        fact_type: FactType | None = None,
        fact_types: Iterable[FactType] | None = None,
    ) -> tuple[ObservableFact, ...]:
        """Return internal read-only views for detector/replay hot paths."""

        requested_types = set(fact_types or ())
        if fact_type is not None:
            requested_types.add(fact_type)
        indexed_groups: list[list[str]] = []
        if symbol is not None:
            indexed_groups.append(self._by_symbol.get(symbol, []))
        if timeframe is not None:
            indexed_groups.append(self._by_timeframe.get(timeframe, []))
        if requested_types:
            indexed_groups.append(
                [
                    fact_id
                    for requested_type in requested_types
                    for fact_id in self._by_fact_type.get(requested_type, [])
                ]
            )
        candidate_ids = (
            min(indexed_groups, key=len) if indexed_groups else self._records.keys()
        )
        records = (
            self._records[fact_id]
            for fact_id in candidate_ids
            if (
                (fact := self._records[fact_id]).available_at <= as_of
                and (symbol is None or fact.symbol == symbol)
                and (timeframe is None or fact.timeframe == timeframe)
                and (not requested_types or fact.fact_type in requested_types)
            )
        )
        return tuple(
            sorted(records, key=lambda item: (item.available_at, item.fact_id))
        )

    def available_views(
        self,
        *,
        at: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        fact_types: Iterable[FactType] | None = None,
    ) -> tuple[ObservableFact, ...]:
        """Return read-only facts becoming available at one exact instant."""

        requested_types = set(fact_types or ())
        records = (
            self._records[fact_id]
            for fact_id in self._by_available_at.get(at, [])
            if (
                (fact := self._records[fact_id])
                and (symbol is None or fact.symbol == symbol)
                and (timeframe is None or fact.timeframe == timeframe)
                and (not requested_types or fact.fact_type in requested_types)
            )
        )
        return tuple(sorted(records, key=lambda item: item.fact_id))

    def as_mapping(self) -> dict[str, ObservableFact]:
        return {
            key: value.model_copy(deep=True) for key, value in self._records.items()
        }


class CandidateStore:
    def __init__(self) -> None:
        self._records: dict[str, ConceptCandidate] = {}
        self._by_symbol: dict[str, list[str]] = defaultdict(list)
        self._by_timeframe: dict[Timeframe, list[str]] = defaultdict(list)
        self._by_candidate_type: dict[CandidateType, list[str]] = defaultdict(list)
        self._by_available_at: dict[AwareDatetime, list[str]] = defaultdict(list)
        self._by_occurred_at: dict[AwareDatetime, list[str]] = defaultdict(list)

    def append(self, candidate: ConceptCandidate) -> None:
        if candidate.candidate_id in self._records:
            raise DuplicateRecordError(
                f"duplicate candidate_id: {candidate.candidate_id}"
            )
        self._records[candidate.candidate_id] = candidate.model_copy(deep=True)
        self._by_symbol[candidate.symbol].append(candidate.candidate_id)
        self._by_timeframe[candidate.timeframe].append(candidate.candidate_id)
        self._by_candidate_type[candidate.candidate_type].append(
            candidate.candidate_id
        )
        self._by_available_at[candidate.available_at].append(candidate.candidate_id)
        self._by_occurred_at[candidate.occurred_at].append(candidate.candidate_id)

    def extend(self, candidates: Iterable[ConceptCandidate]) -> None:
        for candidate in candidates:
            self.append(candidate)

    def get(self, candidate_id: str) -> ConceptCandidate:
        return self._records[candidate_id].model_copy(deep=True)

    def get_optional(self, candidate_id: str) -> ConceptCandidate | None:
        candidate = self._records.get(candidate_id)
        return candidate.model_copy(deep=True) if candidate is not None else None

    def get_view(self, candidate_id: str) -> ConceptCandidate:
        return self._records[candidate_id]

    def get_optional_view(self, candidate_id: str) -> ConceptCandidate | None:
        return self._records.get(candidate_id)

    def contains(self, candidate_id: str) -> bool:
        return candidate_id in self._records

    def existing_ids(self, candidate_ids: Iterable[str]) -> set[str]:
        return {
            candidate_id
            for candidate_id in candidate_ids
            if candidate_id in self._records
        }

    def visible(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        candidate_type: CandidateType | None = None,
    ) -> tuple[ConceptCandidate, ...]:
        indexed_groups: list[list[str]] = []
        if symbol is not None:
            indexed_groups.append(self._by_symbol.get(symbol, []))
        if timeframe is not None:
            indexed_groups.append(self._by_timeframe.get(timeframe, []))
        if candidate_type is not None:
            indexed_groups.append(self._by_candidate_type.get(candidate_type, []))
        candidate_ids = (
            min(indexed_groups, key=len) if indexed_groups else self._records.keys()
        )
        records = (
            self._records[candidate_id]
            for candidate_id in candidate_ids
            if (
                (candidate := self._records[candidate_id]).available_at <= as_of
                and (symbol is None or candidate.symbol == symbol)
                and (timeframe is None or candidate.timeframe == timeframe)
                and (
                    candidate_type is None
                    or candidate.candidate_type == candidate_type
                )
            )
        )
        return tuple(
            candidate.model_copy(deep=True)
            for candidate in sorted(
                records,
                key=lambda item: (item.available_at, item.candidate_id),
            )
        )

    def visible_views(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        candidate_type: CandidateType | None = None,
    ) -> tuple[ConceptCandidate, ...]:
        """Return internal read-only views for detector/replay hot paths."""

        indexed_groups: list[list[str]] = []
        if symbol is not None:
            indexed_groups.append(self._by_symbol.get(symbol, []))
        if timeframe is not None:
            indexed_groups.append(self._by_timeframe.get(timeframe, []))
        if candidate_type is not None:
            indexed_groups.append(self._by_candidate_type.get(candidate_type, []))
        candidate_ids = (
            min(indexed_groups, key=len) if indexed_groups else self._records.keys()
        )
        records = (
            self._records[candidate_id]
            for candidate_id in candidate_ids
            if (
                (candidate := self._records[candidate_id]).available_at <= as_of
                and (symbol is None or candidate.symbol == symbol)
                and (timeframe is None or candidate.timeframe == timeframe)
                and (
                    candidate_type is None
                    or candidate.candidate_type == candidate_type
                )
            )
        )
        return tuple(
            sorted(
                records,
                key=lambda item: (item.available_at, item.candidate_id),
            )
        )

    def available_views(
        self,
        *,
        at: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        candidate_type: CandidateType | None = None,
    ) -> tuple[ConceptCandidate, ...]:
        """Return read-only candidates becoming available at one instant."""

        records = (
            self._records[candidate_id]
            for candidate_id in self._by_available_at.get(at, [])
            if (
                (candidate := self._records[candidate_id])
                and (symbol is None or candidate.symbol == symbol)
                and (timeframe is None or candidate.timeframe == timeframe)
                and (
                    candidate_type is None
                    or candidate.candidate_type == candidate_type
                )
            )
        )
        return tuple(sorted(records, key=lambda item: item.candidate_id))

    def occurred_views(
        self,
        *,
        at: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        candidate_type: CandidateType | None = None,
        as_of: AwareDatetime | None = None,
    ) -> tuple[ConceptCandidate, ...]:
        """Return read-only candidates associated with one source bar."""

        records = (
            self._records[candidate_id]
            for candidate_id in self._by_occurred_at.get(at, [])
            if (
                (candidate := self._records[candidate_id])
                and (symbol is None or candidate.symbol == symbol)
                and (timeframe is None or candidate.timeframe == timeframe)
                and (
                    candidate_type is None
                    or candidate.candidate_type == candidate_type
                )
                and (as_of is None or candidate.available_at <= as_of)
            )
        )
        return tuple(sorted(records, key=lambda item: item.candidate_id))

    def as_mapping(self) -> dict[str, ConceptCandidate]:
        return {
            key: value.model_copy(deep=True) for key, value in self._records.items()
        }


class RaidEpisodeStore:
    """Append-only raid origins and cross-timeframe observation updates."""

    def __init__(self) -> None:
        self._episodes: dict[str, RaidEpisode] = {}
        self._updates: dict[str, RaidEpisodeUpdate] = {}
        self._updates_by_episode: dict[str, list[RaidEpisodeUpdate]] = defaultdict(list)
        self._current: dict[str, RaidEpisode] = {}
        self._episode_by_reference: dict[str, str] = {}

    def append_episode(self, episode: RaidEpisode) -> None:
        if episode.raid_episode_id in self._episodes:
            raise DuplicateRecordError(
                f"duplicate raid_episode_id: {episode.raid_episode_id}"
            )
        if episode.reference_fact_id in self._episode_by_reference:
            raise DuplicateRecordError(
                f"duplicate raid reference: {episode.reference_fact_id}"
            )
        stored = episode.model_copy(deep=True)
        self._episodes[episode.raid_episode_id] = stored
        self._current[episode.raid_episode_id] = stored.model_copy(deep=True)
        self._episode_by_reference[episode.reference_fact_id] = episode.raid_episode_id

    def append_update(self, update: RaidEpisodeUpdate) -> None:
        if update.update_id in self._updates:
            raise DuplicateRecordError(f"duplicate raid update: {update.update_id}")
        current = self._current[update.raid_episode_id]
        if update.available_at < current.available_at:
            raise ValueError("raid updates must be appended in availability order")
        stored = update.model_copy(deep=True)
        self._updates[update.update_id] = stored
        self._updates_by_episode[update.raid_episode_id].append(stored)
        self._current[update.raid_episode_id] = self._apply_update(current, stored)

    def current(
        self,
        raid_episode_id: str,
        *,
        as_of: AwareDatetime | None = None,
    ) -> RaidEpisode:
        latest = self._current[raid_episode_id]
        if as_of is None or latest.available_at <= as_of:
            return latest.model_copy(deep=True)
        episode = self._episodes[raid_episode_id].model_copy(deep=True)
        updates = (
            item
            for item in self._updates_by_episode.get(raid_episode_id, [])
            if item.available_at <= as_of
        )
        for update in updates:
            episode = self._apply_update(episode, update)
        return episode

    @staticmethod
    def _apply_update(
        episode: RaidEpisode,
        update: RaidEpisodeUpdate,
    ) -> RaidEpisode:
        raid_ids = list(episode.raid_candidate_ids)
        observation_ids = list(episode.observation_fact_ids)
        timeframes = list(episode.observed_timeframes)
        states = dict(episode.observation_states)
        breached_at = dict(episode.breached_at)
        first_raid_candidate_id = episode.first_raid_candidate_id
        observation_ids.append(update.observation_fact_id)
        timeframes.append(update.observation_timeframe)
        if update.raid_candidate_id is not None:
            raid_ids.append(update.raid_candidate_id)
            if first_raid_candidate_id is None:
                first_raid_candidate_id = update.raid_candidate_id
        states[update.observation_timeframe] = update.observation_state
        if update.breached_at is not None:
            breached_at.setdefault(update.observation_timeframe, update.breached_at)
        extreme = (
            min(episode.extreme, update.extreme)
            if episode.direction.value == "bullish"
            else max(episode.extreme, update.extreme)
        )
        return episode.model_copy(
            update={
                "available_at": update.available_at,
                "raid_candidate_ids": list(dict.fromkeys(raid_ids)),
                "first_raid_candidate_id": first_raid_candidate_id,
                "observation_fact_ids": list(dict.fromkeys(observation_ids)),
                "observed_timeframes": list(dict.fromkeys(timeframes)),
                "observation_states": states,
                "breached_at": breached_at,
                "extreme": extreme,
            },
            deep=False,
        )

    def current_view(
        self,
        raid_episode_id: str,
        *,
        as_of: AwareDatetime | None = None,
    ) -> RaidEpisode:
        latest = self._current[raid_episode_id]
        if as_of is None or latest.available_at <= as_of:
            return latest
        return self.current(raid_episode_id, as_of=as_of)

    def by_reference(
        self,
        reference_fact_id: str,
        *,
        as_of: AwareDatetime,
    ) -> RaidEpisode | None:
        episode_id = self._episode_by_reference.get(reference_fact_id)
        if episode_id is None:
            return None
        origin = self._episodes[episode_id]
        if origin.available_at > as_of:
            return None
        return self.current(episode_id, as_of=as_of)

    def latest_observation_fact_id(
        self,
        raid_episode_id: str,
        timeframe: Timeframe,
        *,
        as_of: AwareDatetime,
    ) -> str | None:
        for update in reversed(self._updates_by_episode.get(raid_episode_id, [])):
            if update.available_at <= as_of and update.observation_timeframe == timeframe:
                return update.observation_fact_id
        origin = self._episodes[raid_episode_id]
        for observed_timeframe, fact_id in zip(
            origin.observed_timeframes,
            origin.observation_fact_ids,
            strict=True,
        ):
            if observed_timeframe == timeframe and origin.available_at <= as_of:
                return fact_id
        return None

    def visible(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
    ) -> tuple[RaidEpisode, ...]:
        episodes = (
            self.current(episode_id, as_of=as_of)
            for episode_id, origin in self._episodes.items()
            if origin.available_at <= as_of
            and (symbol is None or origin.symbol == symbol)
        )
        return tuple(
            sorted(episodes, key=lambda item: (item.available_at, item.raid_episode_id))
        )

    def visible_views(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
    ) -> tuple[RaidEpisode, ...]:
        """Return internal read-only views for the replay hot path."""

        episodes = (
            self.current_view(episode_id, as_of=as_of)
            for episode_id, origin in self._episodes.items()
            if origin.available_at <= as_of
            and (symbol is None or origin.symbol == symbol)
        )
        return tuple(
            sorted(episodes, key=lambda item: (item.available_at, item.raid_episode_id))
        )

    def updates(self, raid_episode_id: str) -> tuple[RaidEpisodeUpdate, ...]:
        return tuple(
            item.model_copy(deep=True)
            for item in self._updates_by_episode.get(raid_episode_id, [])
        )


class SetupStore:
    """Append-only setup origins and transition events with reconstructed views."""

    def __init__(self) -> None:
        self._setups: dict[str, SetupCandidate] = {}
        self._transitions: dict[str, SetupTransition] = {}
        self._transitions_by_setup: dict[str, list[SetupTransition]] = defaultdict(list)
        self._current: dict[str, SetupCandidate] = {}
        self._by_raid_episode: dict[str, list[str]] = defaultdict(list)
        self._processed_bars: set[tuple[Timeframe, AwareDatetime]] = set()

    def append_setup(self, setup: SetupCandidate) -> None:
        if setup.setup_candidate_id in self._setups:
            raise DuplicateRecordError(
                f"duplicate setup_candidate_id: {setup.setup_candidate_id}"
            )
        stored = setup.model_copy(deep=True)
        self._setups[setup.setup_candidate_id] = stored
        self._current[setup.setup_candidate_id] = stored.model_copy(deep=True)
        raid_episode_id = stored.metrics.get("raid_episode_id")
        if raid_episode_id is not None:
            self._by_raid_episode[str(raid_episode_id)].append(
                stored.setup_candidate_id
            )

    def append_transition(self, transition: SetupTransition) -> None:
        if transition.transition_id in self._transitions:
            raise DuplicateRecordError(
                f"duplicate transition_id: {transition.transition_id}"
            )
        current = self._current[transition.setup_candidate_id]
        if transition.from_status != current.status:
            raise ValueError(
                "transition.from_status does not match current setup state"
            )
        assert_setup_transition(current.status, transition.to_status)
        if transition.available_at < current.available_at:
            raise ValueError("setup transitions must be appended in availability order")
        stored = transition.model_copy(deep=True)
        self._transitions[transition.transition_id] = stored
        self._transitions_by_setup[transition.setup_candidate_id].append(stored)
        self._current[transition.setup_candidate_id] = self._apply_transition(
            current, stored
        )

    def get_origin(self, setup_candidate_id: str) -> SetupCandidate:
        return self._setups[setup_candidate_id].model_copy(deep=True)

    def transitions(self, setup_candidate_id: str) -> tuple[SetupTransition, ...]:
        return tuple(
            item.model_copy(deep=True)
            for item in self._transitions_by_setup.get(setup_candidate_id, [])
        )

    def current(
        self,
        setup_candidate_id: str,
        *,
        as_of: AwareDatetime | None = None,
    ) -> SetupCandidate:
        latest = self._current[setup_candidate_id]
        if as_of is None or latest.available_at <= as_of:
            return latest.model_copy(deep=True)
        setup = self.get_origin(setup_candidate_id)
        for transition in self._transitions_by_setup.get(setup_candidate_id, []):
            if transition.available_at <= as_of:
                setup = self._apply_transition(setup, transition)
        return setup

    @staticmethod
    def _apply_transition(
        setup: SetupCandidate,
        transition: SetupTransition,
    ) -> SetupCandidate:
        candidate_ids = list(setup.evidence_candidate_ids)
        fact_ids = list(setup.evidence_fact_ids)
        zone_ids = list(setup.entry_zone_candidate_ids)
        metrics = dict(setup.metrics)
        expires_at = setup.expires_at
        hard_invalidation_price = setup.hard_invalidation_price
        if transition.from_status != setup.status:
            raise ValueError("stored setup transition chain is inconsistent")
        candidate_ids.extend(transition.evidence_candidate_ids)
        fact_ids.extend(transition.evidence_fact_ids)
        zone_ids.extend(transition.entry_zone_candidate_ids)
        metrics.update(transition.metrics)
        if transition.expires_at is not None:
            expires_at = transition.expires_at
        if transition.hard_invalidation_price is not None:
            hard_invalidation_price = transition.hard_invalidation_price
        return setup.model_copy(
            update={
                "status": transition.to_status,
                "available_at": transition.available_at,
                "evidence_candidate_ids": list(dict.fromkeys(candidate_ids)),
                "evidence_fact_ids": list(dict.fromkeys(fact_ids)),
                "entry_zone_candidate_ids": list(dict.fromkeys(zone_ids)),
                "expires_at": expires_at,
                "hard_invalidation_price": hard_invalidation_price,
                "metrics": metrics,
            },
            deep=False,
        )

    def current_view(
        self,
        setup_candidate_id: str,
        *,
        as_of: AwareDatetime | None = None,
    ) -> SetupCandidate:
        latest = self._current[setup_candidate_id]
        if as_of is None or latest.available_at <= as_of:
            return latest
        return self.current(setup_candidate_id, as_of=as_of)

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

    def visible_views(
        self,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
    ) -> tuple[SetupCandidate, ...]:
        """Return internal read-only views for the replay hot path."""

        setups = (
            self.current_view(setup_id, as_of=as_of)
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

    def by_raid_episode_views(
        self,
        raid_episode_id: str,
        *,
        as_of: AwareDatetime,
        symbol: str | None = None,
    ) -> tuple[SetupCandidate, ...]:
        """Return read-only current setups belonging to one raid episode."""

        setups = (
            self.current_view(setup_id, as_of=as_of)
            for setup_id in self._by_raid_episode.get(raid_episode_id, [])
            if symbol is None or self._setups[setup_id].symbol == symbol
        )
        return tuple(
            setup
            for setup in setups
            if setup.created_at <= as_of and setup.available_at <= as_of
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
