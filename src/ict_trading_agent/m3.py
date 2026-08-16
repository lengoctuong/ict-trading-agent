from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .base import NonEmptyStr, SchemaModel
from .candidates import (
    ConceptCandidate,
    RaidEpisode,
    RaidEpisodeUpdate,
    SetupCandidate,
    TargetCandidate,
)
from .detectors import LevelInteractionDetector, LiquidityRaidCandidateDetector
from .detectors.common import stable_candidate_id, stable_fact_id
from .detectors.levels import ReferenceLevel
from .enums import (
    CandidateType,
    Direction,
    FactType,
    FvgLifecycle,
    LiquiditySide,
    SetupStatus,
    Timeframe,
)
from .facts import ObservableFact, PriceGeometry
from .lifecycle import SetupTransition
from .market import ClosedBarFeed, OHLCBar
from .stores import (
    CandidateStore,
    DuplicateRecordError,
    FactStore,
    RaidEpisodeStore,
    SetupStore,
)

TERMINAL_SETUP_STATUSES = {
    SetupStatus.REJECTED,
    SetupStatus.CLOSED,
    SetupStatus.INVALIDATED,
    SetupStatus.EXPIRED,
    SetupStatus.RISK_REJECTED,
    SetupStatus.ENTERED,
}


class M3Policy(SchemaModel):
    """Versioned research windows from the frozen M3 planning directive."""

    reclaim_window_bars: int = Field(default=3, ge=1)
    shift_window_bars: dict[Timeframe, int] = Field(
        default_factory=lambda: {
            Timeframe.M5: 12,
            Timeframe.M15: 8,
            Timeframe.H1: 4,
        }
    )
    fvg_expiry_bars: dict[Timeframe, int] = Field(
        default_factory=lambda: {
            Timeframe.M5: 24,
            Timeframe.M15: 16,
            Timeframe.H1: 6,
        }
    )
    repricing_max_lag_bars: int = Field(default=1, ge=0)
    setup_timeframes: tuple[Timeframe, ...] = (Timeframe.H1, Timeframe.M15)
    entry_timeframe: Timeframe = Timeframe.M5
    raid_observation_timeframes: tuple[Timeframe, ...] = (
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
    )
    reaction_confirmation_bars: int = Field(default=3, ge=1)
    post_terminal_research_bars: dict[Timeframe, int] = Field(
        default_factory=lambda: {
            Timeframe.M1: 64,
            Timeframe.M5: 64,
            Timeframe.M15: 32,
            Timeframe.H1: 32,
        }
    )

    @model_validator(mode="after")
    def validate_windows(self) -> "M3Policy":
        if set(self.shift_window_bars) != set(self.fvg_expiry_bars):
            raise ValueError("shift and FVG-expiry policies must cover the same TFs")
        if any(value < 1 for value in self.shift_window_bars.values()):
            raise ValueError("shift windows must be positive")
        if any(value < 1 for value in self.fvg_expiry_bars.values()):
            raise ValueError("FVG expiry windows must be positive")
        if not self.setup_timeframes or len(set(self.setup_timeframes)) != len(
            self.setup_timeframes
        ):
            raise ValueError("setup timeframes must be non-empty and unique")
        if any(item not in self.shift_window_bars for item in self.setup_timeframes):
            raise ValueError("every setup timeframe requires a shift window")
        if self.entry_timeframe not in self.fvg_expiry_bars:
            raise ValueError("entry timeframe requires an FVG expiry window")
        if any(value < 1 for value in self.post_terminal_research_bars.values()):
            raise ValueError("post-terminal research windows must be positive")
        return self


class ReadyForLLMPayload(SchemaModel):
    payload_id: NonEmptyStr
    setup: SetupCandidate
    as_of: AwareDatetime
    facts: list[ObservableFact]
    candidates: list[ConceptCandidate]
    targets: list[TargetCandidate] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> "ReadyForLLMPayload":
        if self.setup.status != SetupStatus.READY_FOR_LLM:
            raise ValueError("LLM payload requires a READY_FOR_LLM setup")
        if self.setup.available_at > self.as_of:
            raise ValueError("setup is not available at payload.as_of")
        if any(item.available_at > self.as_of for item in self.facts):
            raise ValueError("payload contains a future fact")
        if any(item.available_at > self.as_of for item in self.candidates):
            raise ValueError("payload contains a future candidate")
        return self


class M3DetectionBatch(SchemaModel):
    symbol: NonEmptyStr
    timeframe: Timeframe
    as_of: AwareDatetime
    processed_bar_open_at: AwareDatetime
    facts: list[ObservableFact] = Field(default_factory=list)
    candidates: list[ConceptCandidate] = Field(default_factory=list)
    raid_episodes_created: list[RaidEpisode] = Field(default_factory=list)
    raid_updates: list[RaidEpisodeUpdate] = Field(default_factory=list)
    setups_created: list[SetupCandidate] = Field(default_factory=list)
    transitions: list[SetupTransition] = Field(default_factory=list)
    ready_for_llm: list[ReadyForLLMPayload] = Field(default_factory=list)


def _event_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return prefix + "-" + sha256(raw.encode("utf-8")).hexdigest()[:24]


class M3SetupPipeline:
    """Append-only RAID -> SHIFT -> ENTRY_ZONE -> READY setup lifecycle."""

    version = "0.1.1"

    def __init__(
        self,
        *,
        bar_feed: ClosedBarFeed,
        fact_store: FactStore,
        candidate_store: CandidateStore,
        setup_store: SetupStore,
        raid_store: RaidEpisodeStore | None = None,
        tick_size: float,
        policy: M3Policy | None = None,
        target_candidates: Sequence[TargetCandidate] = (),
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.bar_feed = bar_feed
        self.fact_store = fact_store
        self.candidate_store = candidate_store
        self.setup_store = setup_store
        self.raid_store = raid_store or RaidEpisodeStore()
        self.policy = policy or M3Policy()
        self.level_interactions = LevelInteractionDetector(tick_size=tick_size)
        self.liquidity_raids = LiquidityRaidCandidateDetector()
        self.target_candidates = tuple(
            target.model_copy(deep=True) for target in target_candidates
        )
        self.context = dict(context or {})

    def process_latest(
        self,
        *,
        timeframe: Timeframe,
        as_of: AwareDatetime,
    ) -> M3DetectionBatch:
        bars = self.bar_feed.bars(timeframe, as_of=as_of)
        if not bars:
            raise ValueError("M3 requires a closed bar")
        return self._process_bar(bars[-1])

    def process_range(
        self,
        *,
        timeframe: Timeframe,
        start_after: AwareDatetime | None,
        as_of: AwareDatetime,
    ) -> tuple[M3DetectionBatch, ...]:
        batches: list[M3DetectionBatch] = []
        started = start_after is not None
        for bar in self.bar_feed.bars(timeframe, as_of=as_of):
            if start_after is not None and bar.open_time <= start_after:
                continue
            if self.setup_store.is_bar_processed(timeframe, bar.open_time):
                started = True
                continue
            if not self._m2_processed(bar):
                if started:
                    raise ValueError(
                        "M3 cannot skip a missing M2 bar inside its cursor"
                    )
                continue
            started = True
            batches.append(self._process_bar(bar))
        return tuple(batches)

    def catch_up(
        self,
        *,
        timeframe: Timeframe,
        as_of: AwareDatetime,
    ) -> tuple[M3DetectionBatch, ...]:
        return self.process_range(
            timeframe=timeframe,
            start_after=self.setup_store.last_processed(timeframe),
            as_of=as_of,
        )

    def ready_payload(
        self,
        setup_candidate_id: str,
        *,
        as_of: AwareDatetime,
    ) -> ReadyForLLMPayload:
        setup = self.setup_store.current(setup_candidate_id, as_of=as_of)
        fact_map = self.fact_store.as_mapping()
        candidate_map = self.candidate_store.as_mapping()
        facts = [fact_map[item] for item in setup.evidence_fact_ids if item in fact_map]
        candidates = [
            candidate_map[item]
            for item in setup.evidence_candidate_ids
            if item in candidate_map
        ]
        targets = [
            target.model_copy(deep=True)
            for target in self.target_candidates
            if target.available_at <= as_of and not target.already_taken
        ]
        return ReadyForLLMPayload(
            payload_id=_event_id("llm-payload", setup_candidate_id, as_of.isoformat()),
            setup=setup,
            as_of=as_of,
            facts=facts,
            candidates=candidates,
            targets=targets,
            context=dict(self.context),
        )

    def _process_bar(self, bar: OHLCBar) -> M3DetectionBatch:
        supported = set(self.policy.raid_observation_timeframes) | set(
            self.policy.post_terminal_research_bars
        )
        if bar.timeframe not in supported:
            raise ValueError("M3 does not support this timeframe")
        if self.setup_store.is_bar_processed(bar.timeframe, bar.open_time):
            raise DuplicateRecordError("M3 bar was already processed")
        if not self._m2_processed(bar):
            raise ValueError("M2 must process the bar before M3")

        facts: list[ObservableFact] = []
        candidates: list[ConceptCandidate] = []
        episodes_created: list[RaidEpisode] = []
        raid_updates: list[RaidEpisodeUpdate] = []
        setups_created: list[SetupCandidate] = []
        transitions: list[SetupTransition] = []
        ready: list[ReadyForLLMPayload] = []

        reclaim_facts, raid_candidates = self._later_reclaims(bar)
        self._append_facts(reclaim_facts)
        self._append_candidates(raid_candidates)
        facts.extend(reclaim_facts)
        candidates.extend(raid_candidates)

        current_raids = [
            candidate
            for candidate in self.candidate_store.visible(
                as_of=bar.close_time,
                symbol=bar.symbol,
                candidate_type=CandidateType.LIQUIDITY_EVENT,
            )
            if candidate.available_at == bar.close_time
            and candidate.timeframe == bar.timeframe
        ]
        for raid in current_raids:
            observation, episode, update, created, merged = self._record_raid(raid)
            if observation.fact_id not in self.fact_store.as_mapping():
                self._append_facts([observation])
                facts.append(observation)
            if episode is not None:
                episodes_created.append(episode)
            if update is not None:
                raid_updates.append(update)
            setups_created.extend(created)
            transitions.extend(merged)

        observed_facts, observed_updates, merged = self._observe_existing_episodes(bar)
        self._append_facts(observed_facts)
        facts.extend(observed_facts)
        raid_updates.extend(observed_updates)
        transitions.extend(merged)

        for setup in list(
            self.setup_store.visible(
                as_of=bar.close_time,
                symbol=bar.symbol,
            )
        ):
            if setup.status in TERMINAL_SETUP_STATUSES:
                research = self._observe_terminal_setup(setup, bar)
                self._append_facts(research)
                facts.extend(research)
                continue

            if bar.timeframe == setup.setup_timeframe:
                transition = self._maybe_invalidate(setup, bar)
                if transition is not None:
                    transitions.append(transition)
                    continue

            setup = self.setup_store.current(setup.setup_candidate_id)
            if (
                setup.status == SetupStatus.DETECTED
                and bar.timeframe == setup.setup_timeframe
            ):
                new_candidates, transition, research = self._maybe_shift(setup, bar)
                self._append_candidates(new_candidates)
                self._append_facts(research)
                candidates.extend(new_candidates)
                facts.extend(research)
                if transition is not None:
                    transitions.append(transition)

            setup = self.setup_store.current(setup.setup_candidate_id)
            if (
                setup.status == SetupStatus.FORMING
                and bar.timeframe == setup.entry_timeframe
            ):
                zones, transition, research = self._maybe_link_fvg(setup, bar)
                self._append_candidates(zones)
                self._append_facts(research)
                candidates.extend(zones)
                facts.extend(research)
                if transition is not None:
                    transitions.append(transition)

            setup = self.setup_store.current(setup.setup_candidate_id)
            if (
                setup.status == SetupStatus.FORMING
                and setup.entry_zone_candidate_ids
                and bar.timeframe == setup.entry_timeframe
            ):
                reaction_facts, transition = self._maybe_react_or_expire(setup, bar)
                self._append_facts(reaction_facts)
                facts.extend(reaction_facts)
                if transition is not None:
                    transitions.append(transition)
                    if transition.to_status == SetupStatus.READY_FOR_LLM:
                        ready.append(
                            self.ready_payload(
                                setup.setup_candidate_id,
                                as_of=bar.close_time,
                            )
                        )

        self.setup_store.mark_bar_processed(bar.timeframe, bar.open_time)
        return M3DetectionBatch(
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            as_of=bar.close_time,
            processed_bar_open_at=bar.open_time,
            facts=facts,
            candidates=candidates,
            raid_episodes_created=episodes_created,
            raid_updates=raid_updates,
            setups_created=setups_created,
            transitions=transitions,
            ready_for_llm=ready,
        )

    def _m2_processed(self, bar: OHLCBar) -> bool:
        return any(
            fact.occurred_at == bar.open_time
            for fact in self.fact_store.visible(
                as_of=bar.close_time,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                fact_type=FactType.CANDLE_FEATURES,
            )
        )

    def _later_reclaims(
        self,
        bar: OHLCBar,
    ) -> tuple[list[ObservableFact], list[ConceptCandidate]]:
        visible_at_open = self.fact_store.visible(
            as_of=bar.open_time,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
        )
        all_visible = self.fact_store.visible(as_of=bar.open_time, symbol=bar.symbol)
        reclaimed_breach_ids = {
            source_id
            for fact in all_visible
            if fact.fact_type == FactType.LEVEL_RECLAIM
            for source_id in fact.source_fact_ids
            if source_id.startswith("fact-")
        }
        fact_map = self.fact_store.as_mapping()
        bars = self.bar_feed.bars(bar.timeframe, as_of=bar.close_time)
        current_index = next(
            index for index, item in enumerate(bars) if item.open_time == bar.open_time
        )
        facts: list[ObservableFact] = []
        raids: list[ConceptCandidate] = []
        for breach in visible_at_open:
            if breach.fact_type != FactType.LEVEL_BREACH:
                continue
            if breach.fact_id in reclaimed_breach_ids:
                continue
            reference_id = str(breach.metrics["reference_fact_id"])
            reference_fact = fact_map.get(reference_id)
            if reference_fact is None:
                continue
            breach_index = next(
                (
                    index
                    for index, item in enumerate(bars)
                    if item.open_time == breach.occurred_at
                ),
                None,
            )
            if breach_index is None or breach_index >= current_index:
                continue
            span = current_index - breach_index
            episode_bars = bars[breach_index : current_index + 1]
            side = str(breach.metrics["reference_side"])
            episode_extreme = (
                max(item.high for item in episode_bars)
                if side == "buy_side"
                else min(item.low for item in episode_bars)
            )
            reclaim = self.level_interactions.detect_reclaim(
                bar,
                ReferenceLevel.from_fact(reference_fact),
                breach,
                reclaim_span_bars=span,
                episode_extreme=episode_extreme,
            )
            if reclaim is None:
                continue
            eligible = span <= self.policy.reclaim_window_bars
            reclaim = reclaim.model_copy(
                update={
                    "metrics": reclaim.metrics
                    | {
                        "promotion_eligible": eligible,
                        "reason_code": (
                            "MULTI_BAR_RECLAIM_WITHIN_WINDOW"
                            if eligible
                            else "RECLAIM_OUTSIDE_WINDOW"
                        ),
                    }
                },
                deep=True,
            )
            facts.append(reclaim)
            if eligible:
                raids.append(self.liquidity_raids.detect(breach, reclaim))
        return facts, raids

    def _raid_observation_from_candidate(
        self,
        raid: ConceptCandidate,
    ) -> ObservableFact:
        if raid.timeframe is None:
            raise ValueError("raid candidate requires a detection timeframe")
        if raid.direction not in {Direction.BULLISH, Direction.BEARISH}:
            raise ValueError("raid candidate requires a directional outcome")
        reference_id = str(raid.raw_features["reference_fact_id"])
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.RAID_OBSERVATION.value,
                reference_id,
                raid.timeframe.value,
                raid.occurred_at.isoformat(),
            ),
            fact_type=FactType.RAID_OBSERVATION,
            symbol=raid.symbol,
            timeframe=raid.timeframe,
            occurred_at=raid.occurred_at,
            confirmed_at=raid.available_at,
            available_at=raid.available_at,
            direction=raid.direction,
            geometry=PriceGeometry(
                price=float(raid.raw_features["reference_price"]),
                extreme=float(raid.raw_features["extreme"]),
            ),
            source_fact_ids=list(raid.evidence_fact_ids),
            metrics={
                "reference_fact_id": reference_id,
                "raid_candidate_id": raid.candidate_id,
                "raid_detection_timeframe": raid.timeframe.value,
                "same_bar_reclaim": raid.raw_features.get("same_bar_reclaim"),
                "reclaim_span_bars": raid.raw_features.get("reclaim_span_bars", 0),
                "extreme": float(raid.raw_features["extreme"]),
            },
            detector_name="RaidEpisodeObserver",
            detector_version=self.version,
        )

    def _record_raid(
        self,
        raid: ConceptCandidate,
    ) -> tuple[
        ObservableFact,
        RaidEpisode | None,
        RaidEpisodeUpdate | None,
        list[SetupCandidate],
        list[SetupTransition],
    ]:
        observation = self._raid_observation_from_candidate(raid)
        reference_id = str(raid.raw_features["reference_fact_id"])
        existing = self.raid_store.by_reference(reference_id, as_of=raid.available_at)
        if existing is None:
            episode = RaidEpisode(
                raid_episode_id=_event_id("raid-episode", reference_id),
                reference_fact_id=reference_id,
                symbol=raid.symbol,
                direction=raid.direction,
                created_at=raid.occurred_at,
                available_at=raid.available_at,
                first_raid_candidate_id=raid.candidate_id,
                raid_candidate_ids=[raid.candidate_id],
                observation_fact_ids=[observation.fact_id],
                observed_timeframes=[raid.timeframe],
                extreme=float(raid.raw_features["extreme"]),
            )
            self.raid_store.append_episode(episode)
            setups = self._create_setups(episode, raid, observation)
            return observation, episode, None, setups, []
        if observation.fact_id in existing.observation_fact_ids:
            return observation, None, None, [], []
        update = self._append_raid_update(
            existing,
            observation,
            raid_candidate_id=raid.candidate_id,
        )
        transitions = self._merge_episode_evidence(
            existing.raid_episode_id,
            observation,
            raid_candidate_id=raid.candidate_id,
        )
        return observation, None, update, [], transitions

    def _create_setups(
        self,
        episode: RaidEpisode,
        raid: ConceptCandidate,
        observation: ObservableFact,
    ) -> list[SetupCandidate]:
        setups: list[SetupCandidate] = []
        for setup_timeframe in self.policy.setup_timeframes:
            setup = SetupCandidate(
                setup_candidate_id=_event_id(
                    "setup", episode.raid_episode_id, setup_timeframe.value
                ),
                setup_type="liquidity_sweep_shift_fvg_reversal",
                setup_version=self.version,
                symbol=raid.symbol,
                direction=raid.direction,
                setup_timeframe=setup_timeframe,
                entry_timeframe=self.policy.entry_timeframe,
                created_at=raid.occurred_at,
                available_at=raid.available_at,
                status=SetupStatus.DETECTED,
                evidence_candidate_ids=[raid.candidate_id],
                evidence_fact_ids=list(
                    dict.fromkeys([*raid.evidence_fact_ids, observation.fact_id])
                ),
                hard_invalidation_price=episode.extreme,
                target_candidate_ids=[
                    target.candidate_id
                    for target in self.target_candidates
                    if target.available_at <= raid.available_at
                    and not target.already_taken
                ],
                metrics={
                    "raid_episode_id": episode.raid_episode_id,
                    "reference_fact_id": episode.reference_fact_id,
                    "reference_timeframe": raid.raw_features.get("reference_timeframe"),
                    "raid_detection_timeframe": raid.timeframe.value,
                    "setup_timeframe": setup_timeframe.value,
                    "entry_timeframe": self.policy.entry_timeframe.value,
                    "raid_candidate_id": raid.candidate_id,
                    "raid_bar_open_at": raid.occurred_at.isoformat(),
                    "raid_available_at": raid.available_at.isoformat(),
                    "raid_extreme": episode.extreme,
                    "same_bar_reclaim": raid.raw_features.get("same_bar_reclaim"),
                    "reclaim_span_bars": raid.raw_features.get("reclaim_span_bars", 0),
                },
            )
            self.setup_store.append_setup(setup)
            setups.append(setup)
        return setups

    def _append_raid_update(
        self,
        episode: RaidEpisode,
        observation: ObservableFact,
        *,
        raid_candidate_id: str | None = None,
    ) -> RaidEpisodeUpdate:
        assert observation.timeframe is not None
        assert observation.geometry is not None
        assert observation.geometry.extreme is not None
        update = RaidEpisodeUpdate(
            update_id=_event_id(
                "raid-update", episode.raid_episode_id, observation.fact_id
            ),
            raid_episode_id=episode.raid_episode_id,
            occurred_at=observation.occurred_at,
            available_at=observation.available_at,
            observation_fact_id=observation.fact_id,
            observation_timeframe=observation.timeframe,
            raid_candidate_id=raid_candidate_id,
            extreme=observation.geometry.extreme,
        )
        self.raid_store.append_update(update)
        return update

    def _merge_episode_evidence(
        self,
        episode_id: str,
        observation: ObservableFact,
        *,
        raid_candidate_id: str | None,
    ) -> list[SetupTransition]:
        current_episode = self.raid_store.current(episode_id)
        transitions: list[SetupTransition] = []
        for setup in self.setup_store.visible(
            as_of=observation.available_at,
            symbol=observation.symbol,
        ):
            if setup.metrics.get("raid_episode_id") != episode_id:
                continue
            if setup.status in TERMINAL_SETUP_STATUSES:
                continue
            transitions.append(
                self._append_transition(
                    setup,
                    setup.status,
                    observation.occurred_at,
                    observation.available_at,
                    evidence_candidate_ids=(
                        [raid_candidate_id] if raid_candidate_id is not None else []
                    ),
                    evidence_fact_ids=[observation.fact_id],
                    reason_codes=["RAID_EPISODE_EVIDENCE_MERGED"],
                    hard_invalidation_price=current_episode.extreme,
                    metrics={
                        "raid_episode_merged": True,
                        "raid_observation_timeframes": [
                            item.value for item in current_episode.observed_timeframes
                        ],
                        "raid_extreme": current_episode.extreme,
                    },
                )
            )
        return transitions

    def _observe_existing_episodes(
        self,
        bar: OHLCBar,
    ) -> tuple[list[ObservableFact], list[RaidEpisodeUpdate], list[SetupTransition]]:
        if bar.timeframe not in self.policy.raid_observation_timeframes:
            return [], [], []
        fact_map = self.fact_store.as_mapping()
        facts: list[ObservableFact] = []
        updates: list[RaidEpisodeUpdate] = []
        transitions: list[SetupTransition] = []
        for episode in self.raid_store.visible(as_of=bar.close_time, symbol=bar.symbol):
            observation_id = stable_fact_id(
                FactType.RAID_OBSERVATION.value,
                episode.reference_fact_id,
                bar.timeframe.value,
                bar.open_time.isoformat(),
            )
            if observation_id in fact_map:
                continue
            reference_fact = fact_map.get(episode.reference_fact_id)
            if reference_fact is None or reference_fact.available_at > bar.open_time:
                continue
            reference = ReferenceLevel.from_fact(reference_fact)
            level = reference.price
            if reference.side == LiquiditySide.BUY_SIDE:
                breached = bar.high > level
                reclaimed = bar.close < level
                extreme = bar.high
                direction = Direction.BEARISH
            else:
                breached = bar.low < level
                reclaimed = bar.close > level
                extreme = bar.low
                direction = Direction.BULLISH
            if not (breached and reclaimed and direction == episode.direction):
                continue
            overlaps_first_take = bar.open_time <= episode.created_at < bar.close_time
            elapsed = self._bars_since(episode.available_at, bar)
            if not overlaps_first_take and elapsed > self.policy.reclaim_window_bars:
                continue
            observation = ObservableFact(
                fact_id=observation_id,
                fact_type=FactType.RAID_OBSERVATION,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                occurred_at=bar.open_time,
                confirmed_at=bar.close_time,
                available_at=bar.close_time,
                direction=episode.direction,
                geometry=PriceGeometry(price=level, extreme=extreme),
                source_fact_ids=[episode.reference_fact_id],
                metrics={
                    "raid_episode_id": episode.raid_episode_id,
                    "reference_fact_id": episode.reference_fact_id,
                    "raid_detection_timeframe": bar.timeframe.value,
                    "cross_timeframe_observation": True,
                    "same_bar_reclaim": True,
                    "extreme": extreme,
                },
                detector_name="RaidEpisodeObserver",
                detector_version=self.version,
            )
            update = self._append_raid_update(episode, observation)
            facts.append(observation)
            updates.append(update)
            transitions.extend(
                self._merge_episode_evidence(
                    episode.raid_episode_id,
                    observation,
                    raid_candidate_id=None,
                )
            )
        return facts, updates, transitions

    def _maybe_invalidate(
        self,
        setup: SetupCandidate,
        bar: OHLCBar,
    ) -> SetupTransition | None:
        level = setup.hard_invalidation_price
        if level is None or bar.open_time <= setup.created_at:
            return None
        invalid = (
            bar.close < level
            if setup.direction == Direction.BULLISH
            else bar.close > level
        )
        if not invalid:
            return None
        return self._append_transition(
            setup,
            SetupStatus.INVALIDATED,
            bar.open_time,
            bar.close_time,
            evidence_fact_ids=self._bar_fact_ids(bar),
            reason_codes=["SETUP_TF_CLOSE_BEYOND_RAID_EXTREME"],
            metrics={
                "invalidation_level": level,
                "invalidation_close": bar.close,
                "consecutive_closes": 1,
                "distance_buffer": 0.0,
                "invalidation_timeframe": bar.timeframe.value,
            },
        )

    def _maybe_shift(
        self,
        setup: SetupCandidate,
        bar: OHLCBar,
    ) -> tuple[list[ConceptCandidate], SetupTransition | None, list[ObservableFact]]:
        raid_candidate = self.candidate_store.as_mapping()[
            str(setup.metrics["raid_candidate_id"])
        ]
        episode = self.raid_store.current(
            str(setup.metrics["raid_episode_id"]), as_of=bar.close_time
        )
        distance = self._bars_since(episode.available_at, bar)
        window = self.policy.shift_window_bars[bar.timeframe]
        structure_candidates = [
            candidate
            for candidate in self.candidate_store.visible(
                as_of=bar.close_time,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                candidate_type=CandidateType.STRUCTURE_BREAK,
            )
            if candidate.available_at == bar.close_time
            and candidate.direction == setup.direction
            and candidate.raw_features.get("same_timeframe_structure_eligible") is True
        ]
        if 1 <= distance <= window and structure_candidates:
            fact_ids = list(
                dict.fromkeys(
                    fact_id
                    for candidate in structure_candidates
                    for fact_id in candidate.evidence_fact_ids
                )
            )
            shift = ConceptCandidate(
                candidate_id=stable_candidate_id(
                    CandidateType.SHIFT.value,
                    setup.setup_candidate_id,
                    bar.open_time.isoformat(),
                ),
                candidate_type=CandidateType.SHIFT,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                direction=setup.direction,
                occurred_at=bar.open_time,
                available_at=bar.close_time,
                evidence_fact_ids=fact_ids,
                related_candidate_ids=[
                    raid_candidate.candidate_id,
                    *(candidate.candidate_id for candidate in structure_candidates),
                ],
                raw_features={
                    "setup_candidate_id": setup.setup_candidate_id,
                    "raid_candidate_id": raid_candidate.candidate_id,
                    "bars_after_raid": distance,
                    "shift_window_bars": window,
                    "broken_reference_fact_ids": [
                        str(candidate.raw_features["reference_fact_id"])
                        for candidate in structure_candidates
                    ],
                    "broken_reference_effective_ranks": {
                        str(
                            candidate.raw_features["reference_fact_id"]
                        ): candidate.raw_features.get(
                            "effective_rank_as_of_break", "short_term"
                        )
                        for candidate in structure_candidates
                    },
                    "effective_rank_as_of_break": (
                        structure_candidates[0].raw_features.get(
                            "effective_rank_as_of_break", "short_term"
                        )
                        if len(structure_candidates) == 1
                        else None
                    ),
                    "structure_break_type": "unclassified",
                    "same_timeframe_structure_eligible": True,
                },
                machine_labels=[
                    "shift_candidate",
                    "unclassified_bos_choch",
                    "same_timeframe_close_through",
                ],
            )
            transition = self._append_transition(
                setup,
                SetupStatus.FORMING,
                bar.open_time,
                bar.close_time,
                evidence_candidate_ids=[
                    shift.candidate_id,
                    *(candidate.candidate_id for candidate in structure_candidates),
                ],
                evidence_fact_ids=[*fact_ids, *self._bar_fact_ids(bar)],
                reason_codes=["DIRECTIONAL_SHIFT_WITHIN_WINDOW"],
                metrics={
                    "shift_candidate_id": shift.candidate_id,
                    "shift_bar_open_at": bar.open_time.isoformat(),
                    "bars_after_raid": distance,
                },
            )
            return [shift], transition, []

        research: list[ObservableFact] = []
        if distance > window and structure_candidates:
            for candidate in structure_candidates:
                research.append(
                    self._research_observation(
                        setup,
                        bar,
                        "SHIFT_OUTSIDE_WINDOW",
                        candidate_ids=[candidate.candidate_id],
                        metrics={"bars_after_raid": distance, "window": window},
                    )
                )
        if distance >= window:
            transition = self._append_transition(
                setup,
                SetupStatus.EXPIRED,
                bar.open_time,
                bar.close_time,
                evidence_fact_ids=self._bar_fact_ids(bar),
                reason_codes=["SHIFT_WINDOW_EXPIRED"],
                metrics={"bars_after_raid": distance, "shift_window_bars": window},
            )
            return [], transition, research
        return [], None, research

    def _maybe_link_fvg(
        self,
        setup: SetupCandidate,
        bar: OHLCBar,
    ) -> tuple[list[ConceptCandidate], SetupTransition | None, list[ObservableFact]]:
        candidate_map = self.candidate_store.as_mapping()
        shifts = [
            candidate_map[candidate_id]
            for candidate_id in setup.evidence_candidate_ids
            if candidate_id in candidate_map
            and candidate_map[candidate_id].candidate_type == CandidateType.SHIFT
        ]
        displacements = self.candidate_store.visible(
            as_of=bar.close_time,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            candidate_type=CandidateType.DISPLACEMENT,
        )
        fvg_facts = self.fact_store.visible(
            as_of=bar.close_time,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            fact_type=FactType.FVG_GEOMETRY,
        )
        zones: list[ConceptCandidate] = []
        for shift in shifts:
            for displacement in displacements:
                lag = self._repricing_lag(shift, displacement)
                if lag is None or not 0 <= lag <= self.policy.repricing_max_lag_bars:
                    continue
                if displacement.direction != setup.direction:
                    continue
                for fvg in fvg_facts:
                    if fvg.direction != setup.direction:
                        continue
                    if fvg.occurred_at != displacement.occurred_at:
                        continue
                    candidate_id = stable_candidate_id(
                        CandidateType.FVG.value,
                        setup.setup_candidate_id,
                        fvg.fact_id,
                        displacement.candidate_id,
                    )
                    if candidate_id in candidate_map or any(
                        zone.candidate_id == candidate_id for zone in zones
                    ):
                        continue
                    zones.append(
                        ConceptCandidate(
                            candidate_id=candidate_id,
                            candidate_type=CandidateType.FVG,
                            symbol=bar.symbol,
                            timeframe=bar.timeframe,
                            direction=setup.direction,
                            occurred_at=fvg.occurred_at,
                            available_at=fvg.available_at,
                            evidence_fact_ids=[fvg.fact_id],
                            related_candidate_ids=[
                                shift.candidate_id,
                                displacement.candidate_id,
                            ],
                            raw_features={
                                "setup_candidate_id": setup.setup_candidate_id,
                                "shift_candidate_id": shift.candidate_id,
                                "displacement_candidate_id": displacement.candidate_id,
                                "repricing_lag_bars": lag,
                                "low": fvg.geometry.low,
                                "high": fvg.geometry.high,
                                "ce": fvg.metrics["ce"],
                                "expiry_window_bars": self.policy.fvg_expiry_bars[
                                    bar.timeframe
                                ],
                            },
                            machine_labels=[
                                "linked_repricing_fvg",
                                FvgLifecycle.FRESH.value,
                            ],
                        )
                    )
        if zones:
            transition = self._append_transition(
                setup,
                SetupStatus.FORMING,
                bar.open_time,
                bar.close_time,
                evidence_candidate_ids=list(
                    dict.fromkeys(
                        candidate_id
                        for zone in zones
                        for candidate_id in [
                            zone.candidate_id,
                            *zone.related_candidate_ids,
                        ]
                    )
                ),
                evidence_fact_ids=[
                    *(fact_id for zone in zones for fact_id in zone.evidence_fact_ids),
                    *self._bar_fact_ids(bar),
                ],
                entry_zone_candidate_ids=[zone.candidate_id for zone in zones],
                reason_codes=["LINKED_REPRICING_FVG_AVAILABLE"],
                metrics={
                    "entry_zone_available_at": min(
                        zone.available_at for zone in zones
                    ).isoformat(),
                    "fvg_expiry_window_bars": self.policy.fvg_expiry_bars[
                        bar.timeframe
                    ],
                },
            )
            return zones, transition, []

        if setup.entry_zone_candidate_ids:
            return [], None, []

        elapsed = min(self._entry_lag_from_shift(shift, bar) for shift in shifts)
        max_wait = self.policy.repricing_max_lag_bars + 1
        if elapsed >= max_wait:
            research = [
                self._research_observation(
                    setup,
                    bar,
                    "NO_CAUSALLY_LINKED_FVG",
                    candidate_ids=[shift.candidate_id for shift in shifts],
                    metrics={"bars_after_shift": elapsed, "max_wait": max_wait},
                )
            ]
            transition = self._append_transition(
                setup,
                SetupStatus.EXPIRED,
                bar.open_time,
                bar.close_time,
                evidence_fact_ids=self._bar_fact_ids(bar),
                reason_codes=["FVG_LINK_WINDOW_EXPIRED"],
                metrics={"bars_after_shift": elapsed},
            )
            return [], transition, research
        return [], None, []

    def _maybe_react_or_expire(
        self,
        setup: SetupCandidate,
        bar: OHLCBar,
    ) -> tuple[list[ObservableFact], SetupTransition | None]:
        candidate_map = self.candidate_store.as_mapping()
        zones = [
            candidate_map[candidate_id]
            for candidate_id in setup.entry_zone_candidate_ids
            if candidate_id in candidate_map
        ]
        stored_reactions = [
            fact
            for fact in self.fact_store.visible(
                as_of=bar.open_time,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                fact_type=FactType.FVG_REACTION,
            )
            if fact.metrics.get("setup_candidate_id") == setup.setup_candidate_id
        ]
        reaction_facts: list[ObservableFact] = []
        favorable: list[ObservableFact] = []
        terminal_zone_ids: set[str] = set()
        failed_zone_ids: set[str] = set()
        for zone in zones:
            if bar.open_time < zone.available_at:
                continue
            history = [
                fact
                for fact in stored_reactions
                if fact.metrics.get("entry_zone_candidate_id") == zone.candidate_id
            ]
            if history:
                lifecycle = FvgLifecycle(str(history[-1].metrics["lifecycle"]))
                if lifecycle in {
                    FvgLifecycle.REACTED,
                    FvgLifecycle.FAILED,
                    FvgLifecycle.EXPIRED,
                }:
                    terminal_zone_ids.add(zone.candidate_id)
                    if lifecycle == FvgLifecycle.FAILED:
                        failed_zone_ids.add(zone.candidate_id)
                    continue
            low = float(zone.raw_features["low"])
            high = float(zone.raw_features["high"])
            touched = bar.low <= high and bar.high >= low
            size = high - low
            if setup.direction == Direction.BULLISH:
                penetration = (high - max(bar.low, low)) / size
                favorable_close = bar.close > high
                failed = bar.close < low
            else:
                penetration = (min(bar.high, high) - low) / size
                favorable_close = bar.close < low
                failed = bar.close > high
            penetration = min(1.0, max(0.0, penetration))
            touch_fact = next(
                (
                    fact
                    for fact in history
                    if fact.metrics.get("lifecycle") == FvgLifecycle.TOUCHED.value
                ),
                None,
            )
            reaction_lag = (
                self._bar_distance(bar.timeframe, touch_fact.occurred_at, bar.open_time)
                if touch_fact is not None
                else 0
            )
            lifecycle: FvgLifecycle | None = None
            reason_code: str | None = None
            if failed:
                lifecycle = FvgLifecycle.FAILED
                reason_code = "FVG_CLOSE_THROUGH_FAR_EDGE"
            elif favorable_close and (touched or touch_fact is not None):
                if reaction_lag <= self.policy.reaction_confirmation_bars:
                    lifecycle = FvgLifecycle.REACTED
                    reason_code = "FVG_FAVORABLE_REACTION_CLOSE"
                else:
                    reaction_facts.append(
                        self._research_observation(
                            setup,
                            bar,
                            "FVG_REACTION_OUTSIDE_CONFIRMATION_WINDOW",
                            candidate_ids=[zone.candidate_id],
                            metrics={
                                "reaction_lag_bars": reaction_lag,
                                "window": self.policy.reaction_confirmation_bars,
                            },
                        )
                    )
            elif touched and touch_fact is None:
                lifecycle = FvgLifecycle.TOUCHED
                reason_code = "FVG_TOUCHED"

            elapsed = self._bars_after_available(zone.available_at, bar)
            if (
                lifecycle is None
                and elapsed >= self.policy.fvg_expiry_bars[bar.timeframe]
            ):
                lifecycle = FvgLifecycle.EXPIRED
                reason_code = "FVG_RETRACE_WINDOW_EXPIRED"
            if lifecycle is None:
                continue
            fact = ObservableFact(
                fact_id=stable_fact_id(
                    FactType.FVG_REACTION.value,
                    zone.candidate_id,
                    bar.open_time.isoformat(),
                    lifecycle.value,
                ),
                fact_type=FactType.FVG_REACTION,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                occurred_at=bar.open_time,
                confirmed_at=bar.close_time,
                available_at=bar.close_time,
                direction=setup.direction,
                geometry=PriceGeometry(low=low, high=high, extreme=bar.close),
                source_fact_ids=list(
                    dict.fromkeys([*zone.evidence_fact_ids, *self._bar_fact_ids(bar)])
                ),
                metrics={
                    "setup_candidate_id": setup.setup_candidate_id,
                    "entry_zone_candidate_id": zone.candidate_id,
                    "touched": touched,
                    "penetration_fraction": penetration,
                    "favorable_close_outside": favorable_close,
                    "fully_failed": failed,
                    "close_price": bar.close,
                    "lifecycle": lifecycle.value,
                    "reason_code": reason_code,
                    "reaction_lag_bars": reaction_lag if favorable_close else None,
                },
                detector_name="FVGReactionDetector",
                detector_version=self.version,
            )
            reaction_facts.append(fact)
            if lifecycle == FvgLifecycle.REACTED:
                favorable.append(fact)
                terminal_zone_ids.add(zone.candidate_id)
            elif lifecycle in {FvgLifecycle.FAILED, FvgLifecycle.EXPIRED}:
                terminal_zone_ids.add(zone.candidate_id)
                if lifecycle == FvgLifecycle.FAILED:
                    failed_zone_ids.add(zone.candidate_id)

        if favorable:
            transition = self._append_transition(
                setup,
                SetupStatus.READY_FOR_LLM,
                bar.open_time,
                bar.close_time,
                evidence_fact_ids=[
                    *(fact.fact_id for fact in favorable),
                    *self._bar_fact_ids(bar),
                ],
                reason_codes=["FVG_FAVORABLE_REACTION_CLOSE"],
                metrics={
                    "reaction_fact_ids": [fact.fact_id for fact in favorable],
                    "ready_at": bar.close_time.isoformat(),
                },
            )
            return reaction_facts, transition

        if zones and terminal_zone_ids == {zone.candidate_id for zone in zones}:
            all_failed = failed_zone_ids == {zone.candidate_id for zone in zones}
            transition = self._append_transition(
                setup,
                SetupStatus.EXPIRED,
                bar.open_time,
                bar.close_time,
                evidence_fact_ids=[
                    *(fact.fact_id for fact in reaction_facts),
                    *self._bar_fact_ids(bar),
                ],
                reason_codes=[
                    "ALL_ENTRY_ZONES_FAILED"
                    if all_failed
                    else "FVG_RETRACE_WINDOW_EXPIRED"
                ],
                metrics={
                    "terminal_entry_zone_ids": sorted(terminal_zone_ids),
                    "failed_entry_zone_ids": sorted(failed_zone_ids),
                    "fvg_expiry_window_bars": self.policy.fvg_expiry_bars[
                        bar.timeframe
                    ],
                },
            )
            return reaction_facts, transition
        return reaction_facts, None

    def _append_transition(
        self,
        setup: SetupCandidate,
        target: SetupStatus,
        occurred_at: AwareDatetime,
        available_at: AwareDatetime,
        *,
        evidence_candidate_ids: Sequence[str] = (),
        evidence_fact_ids: Sequence[str] = (),
        entry_zone_candidate_ids: Sequence[str] = (),
        hard_invalidation_price: float | None = None,
        reason_codes: Sequence[str],
        expires_at: AwareDatetime | None = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> SetupTransition:
        current = self.setup_store.current(setup.setup_candidate_id)
        transition = SetupTransition(
            transition_id=_event_id(
                "transition",
                setup.setup_candidate_id,
                current.status.value,
                target.value,
                available_at.isoformat(),
                ",".join(reason_codes),
            ),
            setup_candidate_id=setup.setup_candidate_id,
            from_status=current.status,
            to_status=target,
            occurred_at=occurred_at,
            available_at=available_at,
            evidence_candidate_ids=list(evidence_candidate_ids),
            evidence_fact_ids=list(evidence_fact_ids),
            entry_zone_candidate_ids=list(entry_zone_candidate_ids),
            hard_invalidation_price=hard_invalidation_price,
            reason_codes=list(reason_codes),
            expires_at=expires_at,
            metrics=dict(metrics or {}),
        )
        self.setup_store.append_transition(transition)
        return transition

    def _research_observation(
        self,
        setup: SetupCandidate,
        bar: OHLCBar,
        reason_code: str,
        *,
        candidate_ids: Sequence[str] = (),
        metrics: Mapping[str, Any] | None = None,
    ) -> ObservableFact:
        return ObservableFact(
            fact_id=stable_fact_id(
                FactType.RESEARCH_OBSERVATION.value,
                setup.setup_candidate_id,
                bar.open_time.isoformat(),
                reason_code,
                *candidate_ids,
            ),
            fact_type=FactType.RESEARCH_OBSERVATION,
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            occurred_at=bar.open_time,
            confirmed_at=bar.close_time,
            available_at=bar.close_time,
            source_fact_ids=list(
                dict.fromkeys([*setup.evidence_fact_ids, *self._bar_fact_ids(bar)])
            ),
            metrics={
                "setup_candidate_id": setup.setup_candidate_id,
                "candidate_ids": list(candidate_ids),
                "reason_code": reason_code,
                **dict(metrics or {}),
            },
            detector_name="M3ResearchLogger",
            detector_version=self.version,
        )

    def _observe_terminal_setup(
        self,
        setup: SetupCandidate,
        bar: OHLCBar,
    ) -> list[ObservableFact]:
        horizon = self.policy.post_terminal_research_bars.get(bar.timeframe)
        if horizon is None:
            return []
        bars_after_terminal = self._bars_since(setup.available_at, bar)
        if bars_after_terminal < 1 or bars_after_terminal > horizon:
            return []
        common = {
            "terminal_status": setup.status.value,
            "bars_after_terminal": bars_after_terminal,
            "post_terminal_horizon_bars": horizon,
        }
        observations: list[ObservableFact] = []
        if bar.timeframe == setup.setup_timeframe:
            shifts = [
                candidate
                for candidate in self.candidate_store.visible(
                    as_of=bar.close_time,
                    symbol=bar.symbol,
                    timeframe=bar.timeframe,
                    candidate_type=CandidateType.STRUCTURE_BREAK,
                )
                if candidate.available_at == bar.close_time
                and candidate.direction == setup.direction
                and candidate.raw_features.get("same_timeframe_structure_eligible")
                is True
            ]
            if shifts:
                observations.append(
                    self._research_observation(
                        setup,
                        bar,
                        "LATE_SHIFT_AFTER_TERMINAL",
                        candidate_ids=[item.candidate_id for item in shifts],
                        metrics=common
                        | {
                            "bars_after_raid": self._bars_since(
                                self.raid_store.current(
                                    str(setup.metrics["raid_episode_id"])
                                ).available_at,
                                bar,
                            )
                        },
                    )
                )

        if bar.timeframe == setup.entry_timeframe:
            displacements = [
                candidate
                for candidate in self.candidate_store.visible(
                    as_of=bar.close_time,
                    symbol=bar.symbol,
                    timeframe=bar.timeframe,
                    candidate_type=CandidateType.DISPLACEMENT,
                )
                if candidate.direction == setup.direction
            ]
            fvgs = [
                fact
                for fact in self.fact_store.visible(
                    as_of=bar.close_time,
                    symbol=bar.symbol,
                    timeframe=bar.timeframe,
                    fact_type=FactType.FVG_GEOMETRY,
                )
                if fact.available_at == bar.close_time
                and fact.direction == setup.direction
            ]
            linked_displacements = [
                candidate
                for candidate in displacements
                if any(fvg.occurred_at == candidate.occurred_at for fvg in fvgs)
            ]
            if linked_displacements and fvgs:
                observations.append(
                    self._research_observation(
                        setup,
                        bar,
                        "LATE_FVG_AFTER_TERMINAL",
                        candidate_ids=[
                            item.candidate_id for item in linked_displacements
                        ],
                        metrics=common
                        | {"fvg_fact_ids": [item.fact_id for item in fvgs]},
                    )
                )
            candidate_map = self.candidate_store.as_mapping()
            touched_zone_ids = []
            for zone_id in setup.entry_zone_candidate_ids:
                zone = candidate_map.get(zone_id)
                if zone is None or bar.open_time < zone.available_at:
                    continue
                low = float(zone.raw_features["low"])
                high = float(zone.raw_features["high"])
                if bar.low <= high and bar.high >= low:
                    touched_zone_ids.append(zone_id)
            if touched_zone_ids:
                observations.append(
                    self._research_observation(
                        setup,
                        bar,
                        "LATE_RETRACE_AFTER_TERMINAL",
                        candidate_ids=touched_zone_ids,
                        metrics=common,
                    )
                )
        return observations

    def _bars_since(
        self,
        available_at: AwareDatetime,
        current_bar: OHLCBar,
    ) -> int:
        return len(
            [
                bar
                for bar in self.bar_feed.bars(
                    current_bar.timeframe, as_of=current_bar.close_time
                )
                if available_at < bar.close_time <= current_bar.close_time
            ]
        )

    def _entry_lag_from_shift(
        self,
        shift: ConceptCandidate,
        current_bar: OHLCBar,
    ) -> int:
        if (
            shift.timeframe == current_bar.timeframe
            and shift.occurred_at == current_bar.open_time
        ):
            return 0
        eligible = [
            bar
            for bar in self.bar_feed.bars(
                current_bar.timeframe, as_of=current_bar.close_time
            )
            if shift.available_at <= bar.open_time <= current_bar.open_time
        ]
        if shift.timeframe == current_bar.timeframe:
            return len(eligible)
        return max(0, len(eligible) - 1)

    def _repricing_lag(
        self,
        shift: ConceptCandidate,
        displacement: ConceptCandidate,
    ) -> int | None:
        if (
            shift.timeframe == displacement.timeframe
            and shift.occurred_at == displacement.occurred_at
            and shift.available_at == displacement.available_at
        ):
            return 0
        if displacement.occurred_at < shift.available_at:
            return None
        bars = [
            bar
            for bar in self.bar_feed.bars(displacement.timeframe)
            if bar.open_time >= shift.available_at
            and bar.open_time <= displacement.occurred_at
        ]
        if not bars:
            return None
        if shift.timeframe == displacement.timeframe:
            return len(bars)
        return len(bars) - 1

    def _bar_distance(
        self,
        timeframe: Timeframe,
        earlier_open: AwareDatetime,
        later_open: AwareDatetime,
    ) -> int:
        bars = self.bar_feed.bars(timeframe)
        indexes = {bar.open_time: index for index, bar in enumerate(bars)}
        if earlier_open not in indexes or later_open not in indexes:
            raise ValueError("event cannot be mapped to the setup-timeframe feed")
        return indexes[later_open] - indexes[earlier_open]

    def _bar_fact_ids(self, bar: OHLCBar) -> list[str]:
        return [
            fact.fact_id
            for fact in self.fact_store.visible(
                as_of=bar.close_time,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                fact_type=FactType.CANDLE_FEATURES,
            )
            if fact.occurred_at == bar.open_time
        ]

    def _bars_after_available(
        self,
        available_at: AwareDatetime,
        current_bar: OHLCBar,
    ) -> int:
        bars = self.bar_feed.bars(current_bar.timeframe)
        available_index = next(
            (index for index, bar in enumerate(bars) if bar.close_time == available_at),
            None,
        )
        current_index = next(
            index
            for index, bar in enumerate(bars)
            if bar.open_time == current_bar.open_time
        )
        if available_index is None:
            raise ValueError("FVG availability cannot be mapped to its timeframe")
        return current_index - available_index

    def _append_facts(self, facts: Sequence[ObservableFact]) -> None:
        existing = self.fact_store.as_mapping()
        ids = [fact.fact_id for fact in facts]
        if len(ids) != len(set(ids)) or any(item in existing for item in ids):
            raise DuplicateRecordError("M3 attempted to append duplicate facts")
        self.fact_store.extend(facts)

    def _append_candidates(self, candidates: Sequence[ConceptCandidate]) -> None:
        existing = self.candidate_store.as_mapping()
        ids = [candidate.candidate_id for candidate in candidates]
        if len(ids) != len(set(ids)) or any(item in existing for item in ids):
            raise DuplicateRecordError("M3 attempted to append duplicate candidates")
        self.candidate_store.extend(candidates)
