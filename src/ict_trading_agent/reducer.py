from __future__ import annotations

from hashlib import sha256

from pydantic import AwareDatetime

from .config import TradingProfile
from .enums import CandidateType, FactType
from .market import ClosedBarFeed
from .reference_lifecycle import (
    ReferenceLifecyclePolicy,
    ReferenceLifecycleTracker,
)
from .state import MarketState, TemporalContext, TimeframeState
from .stores import CandidateStore, FactStore


class MarketStateReducer:
    """Point-in-time reducer over append-only facts/candidates and closed bars."""

    def __init__(
        self,
        *,
        profile: TradingProfile,
        bar_feed: ClosedBarFeed,
        fact_store: FactStore,
        candidate_store: CandidateStore,
        reference_lifecycle_policy: ReferenceLifecyclePolicy | None = None,
    ) -> None:
        self.profile = profile
        self.bar_feed = bar_feed
        self.fact_store = fact_store
        self.candidate_store = candidate_store
        self.reference_lifecycle = ReferenceLifecycleTracker(
            reference_lifecycle_policy
        )

    def reduce(
        self,
        *,
        symbol: str,
        as_of: AwareDatetime,
        temporal: TemporalContext,
        target_candidate_ids: list[str] | None = None,
    ) -> MarketState:
        if symbol != self.profile.instrument or symbol != self.bar_feed.symbol:
            raise ValueError("symbol must match profile and bar feed")
        facts = self.fact_store.visible(as_of=as_of, symbol=symbol)
        candidates = self.candidate_store.visible(as_of=as_of, symbol=symbol)
        timeframes: dict = {}
        for timeframe, role in self.profile.timeframes.role_map().items():
            latest_bar = self.bar_feed.latest(timeframe, as_of=as_of)
            if latest_bar is None:
                continue
            tf_facts = [fact for fact in facts if fact.timeframe == timeframe]
            tf_candidates = [
                candidate for candidate in candidates if candidate.timeframe == timeframe
            ]
            timeframes[timeframe] = TimeframeState(
                timeframe=timeframe,
                role=role,
                last_closed_bar_at=latest_bar.close_time,
                active_swing_fact_ids=[
                    fact.fact_id
                    for fact in tf_facts
                    if fact.fact_type == FactType.SWING_POINT
                    and self.reference_lifecycle.is_eligible(
                        fact.fact_id,
                        facts,
                        as_of=as_of,
                    )
                ],
                active_fvg_candidate_ids=[
                    candidate.candidate_id
                    for candidate in tf_candidates
                    if candidate.candidate_type == CandidateType.FVG
                    and "fully_filled" not in candidate.machine_labels
                ],
                active_liquidity_candidate_ids=[
                    candidate.candidate_id
                    for candidate in tf_candidates
                    if candidate.candidate_type == CandidateType.LIQUIDITY_EVENT
                ],
                latest_structure_candidate_ids=[
                    candidate.candidate_id
                    for candidate in tf_candidates
                    if candidate.candidate_type == CandidateType.STRUCTURE_BREAK
                ],
            )
        visible_fact_ids = [fact.fact_id for fact in facts]
        visible_candidate_ids = [candidate.candidate_id for candidate in candidates]
        digest = sha256(
            "|".join(
                [symbol, as_of.isoformat(), *visible_fact_ids, *visible_candidate_ids]
            ).encode("utf-8")
        ).hexdigest()[:24]
        state = MarketState(
            state_id="state-" + digest,
            symbol=symbol,
            as_of=as_of,
            temporal=temporal,
            timeframes=timeframes,
            visible_fact_ids=visible_fact_ids,
            visible_candidate_ids=visible_candidate_ids,
            target_candidate_ids=target_candidate_ids or [],
            metrics={
                "visible_fact_count": len(visible_fact_ids),
                "visible_candidate_count": len(visible_candidate_ids),
            },
        )
        state.assert_point_in_time_visibility(
            self.fact_store.as_mapping(),
            self.candidate_store.as_mapping(),
        )
        return state
