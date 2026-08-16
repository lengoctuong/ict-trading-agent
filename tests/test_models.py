from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from ict_trading_agent.candidates import (
    ConceptCandidate,
    SetupCandidate,
    TargetCandidate,
)
from ict_trading_agent.config import (
    ConceptUsageSpec,
    SetupRuleSpec,
    TradingDayPolicy,
    build_xauusd_intraday_v0,
)
from ict_trading_agent.decisions import TradeDecision
from ict_trading_agent.enums import (
    CandidateType,
    Direction,
    FactType,
    RuleOperator,
    RuleSeverity,
    SemanticAction,
    SemanticClass,
    Session,
    SetupStatus,
    TargetScope,
    TargetSide,
    TargetType,
    Timeframe,
    TimeframeRole,
    TradeAction,
)
from ict_trading_agent.facts import ObservableFact, PriceGeometry
from ict_trading_agent.lifecycle import assert_setup_transition, can_transition_setup
from ict_trading_agent.presets import CORE_CONCEPT_SPECS, FVG_SPEC, SWING_POINT_SPEC
from ict_trading_agent.safety import (
    SafetyAssessment,
    build_v0_close_acceptance_policy,
)
from ict_trading_agent.semantics import (
    CandidateAssessment,
    SemanticAssessment,
    SetupSemanticDecision,
)
from ict_trading_agent.state import MarketState, TemporalContext, TimeframeState

T0 = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def make_fact(*, available_at: datetime = T0) -> ObservableFact:
    return ObservableFact(
        fact_id="fact-1",
        fact_type=FactType.SWING_POINT,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        occurred_at=T0 - timedelta(minutes=5),
        confirmed_at=available_at,
        available_at=available_at,
        geometry=PriceGeometry(price=3340.0),
        detector_name="ICTThreeBarSwingDetector",
        detector_version="0.1.0",
    )


def make_candidate(*, available_at: datetime = T0) -> ConceptCandidate:
    return ConceptCandidate(
        candidate_id="candidate-1",
        candidate_type=CandidateType.STRUCTURE_BREAK,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        direction=Direction.BULLISH,
        occurred_at=T0 - timedelta(minutes=5),
        available_at=available_at,
        evidence_fact_ids=["fact-1"],
        raw_features={"close_above_reference": True},
    )


def make_state(*, as_of: datetime = T0) -> MarketState:
    return MarketState(
        state_id="state-1",
        symbol="XAUUSD",
        as_of=as_of,
        temporal=TemporalContext(
            trading_day="2026-08-15",
            session=Session.ASIA,
            ny_time=as_of,
        ),
        timeframes={
            Timeframe.M5: TimeframeState(
                timeframe=Timeframe.M5,
                role=TimeframeRole.ENTRY,
                last_closed_bar_at=as_of,
            )
        },
        visible_fact_ids=["fact-1"],
        visible_candidate_ids=["candidate-1"],
        target_candidate_ids=[],
    )


def test_fact_rejects_naive_datetimes_and_lookahead() -> None:
    with pytest.raises(ValidationError):
        make_fact(available_at=datetime(2026, 8, 15, 10, 0))

    with pytest.raises(ValidationError, match="available_at cannot precede confirmed_at"):
        ObservableFact(
            fact_id="fact-1",
            fact_type=FactType.SWING_POINT,
            symbol="XAUUSD",
            timeframe=Timeframe.M5,
            occurred_at=T0,
            confirmed_at=T0 + timedelta(minutes=10),
            available_at=T0 + timedelta(minutes=5),
            detector_name="detector",
            detector_version="0.1.0",
        )


def test_geometry_requires_coordinates_and_orders_zone() -> None:
    with pytest.raises(ValidationError, match="at least one coordinate"):
        PriceGeometry()
    with pytest.raises(ValidationError, match="low cannot exceed"):
        PriceGeometry(low=3345.0, high=3340.0)


def test_market_state_enforces_point_in_time_visibility() -> None:
    state = make_state()
    state.assert_point_in_time_visibility(
        {"fact-1": make_fact()},
        {"candidate-1": make_candidate()},
    )

    with pytest.raises(ValueError, match="fact fact-1 is not available"):
        state.assert_point_in_time_visibility(
            {"fact-1": make_fact(available_at=T0 + timedelta(minutes=5))},
            {"candidate-1": make_candidate()},
        )

    with pytest.raises(ValueError, match="candidate candidate-1 is not available"):
        state.assert_point_in_time_visibility(
            {"fact-1": make_fact()},
            {"candidate-1": make_candidate(available_at=T0 + timedelta(minutes=5))},
        )


def test_market_state_timeframe_key_must_match_payload() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        MarketState(
            state_id="state-1",
            symbol="XAUUSD",
            as_of=T0,
            temporal=TemporalContext(
                trading_day="2026-08-15",
                session=Session.ASIA,
                ny_time=T0,
            ),
            timeframes={
                Timeframe.H1: TimeframeState(
                    timeframe=Timeframe.M5,
                    role=TimeframeRole.ENTRY,
                    last_closed_bar_at=T0,
                )
            },
            visible_fact_ids=[],
            visible_candidate_ids=[],
            target_candidate_ids=[],
        )


def test_setup_candidate_rejects_neutral_and_invalid_expiry() -> None:
    payload = dict(
        setup_candidate_id="setup-1",
        setup_type="liquidity_sweep_mss_fvg_reversal",
        setup_version="0.1.0",
        symbol="XAUUSD",
        setup_timeframe=Timeframe.M15,
        entry_timeframe=Timeframe.M5,
        created_at=T0,
        available_at=T0,
        status=SetupStatus.DETECTED,
        evidence_candidate_ids=[],
        evidence_fact_ids=[],
    )
    with pytest.raises(ValidationError, match="cannot be neutral"):
        SetupCandidate(direction=Direction.NEUTRAL, **payload)
    with pytest.raises(ValidationError, match="expires_at must be after"):
        SetupCandidate(direction=Direction.BULLISH, expires_at=T0, **payload)


def test_setup_lifecycle_matches_frozen_flow() -> None:
    assert can_transition_setup(SetupStatus.DETECTED, SetupStatus.FORMING)
    assert can_transition_setup(SetupStatus.FORMING, SetupStatus.READY_FOR_LLM)
    assert can_transition_setup(SetupStatus.READY_FOR_LLM, SetupStatus.ACCEPTED)
    assert can_transition_setup(SetupStatus.ACCEPTED, SetupStatus.RISK_REJECTED)
    assert can_transition_setup(SetupStatus.ENTERED, SetupStatus.CLOSED)
    with pytest.raises(ValueError, match="invalid setup transition"):
        assert_setup_transition(SetupStatus.DETECTED, SetupStatus.ENTERED)


def test_trade_decision_requires_safety_and_directional_prices() -> None:
    payload = dict(
        decision_id="decision-1",
        symbol="XAUUSD",
        created_at=T0,
        setup_candidate_id="setup-1",
        semantic_decision_id="semantic-decision-1",
        action=TradeAction.LONG,
        entry_price=3340.0,
        stop_loss=3335.0,
        take_profit=3355.0,
        target_candidate_id="target-1",
        position_size=0.1,
        risk_per_trade_pct=0.5,
        expected_r=3.0,
    )
    with pytest.raises(ValidationError, match="passed safety gate"):
        TradeDecision(safety_passed=False, **payload)
    with pytest.raises(ValidationError, match="stop_loss < entry_price"):
        TradeDecision(safety_passed=True, **(payload | {"stop_loss": 3345.0}))
    decision = TradeDecision(safety_passed=True, **payload)
    assert decision.action is TradeAction.LONG
    assert decision.semantic_decision_id == "semantic-decision-1"
    with pytest.raises(ValidationError, match="semantic_assessment_id"):
        TradeDecision(
            safety_passed=True,
            **(payload | {"semantic_assessment_id": "legacy-assessment"}),
        )


def test_xauusd_profile_preserves_frozen_roles_and_sessions() -> None:
    profile = build_xauusd_intraday_v0(
        TradingDayPolicy(
            timezone="America/New_York",
            rollover_local_time=time(17, 0),
        )
    )
    assert profile.instrument == "XAUUSD"
    assert profile.timeframes.role_map() == {
        Timeframe.W1: TimeframeRole.MACRO,
        Timeframe.D1: TimeframeRole.BIAS,
        Timeframe.H4: TimeframeRole.BIAS,
        Timeframe.H1: TimeframeRole.SETUP,
        Timeframe.M15: TimeframeRole.SETUP,
        Timeframe.M5: TimeframeRole.ENTRY,
        Timeframe.M1: TimeframeRole.REFINEMENT,
    }
    assert set(profile.sessions) == {
        Session.ASIA,
        Session.LONDON,
        Session.NY_AM,
        Session.NY_PM,
    }
    assert profile.session_policy.hard_filter is False
    assert profile.session_policy.contextual_feature is True


def test_core_concept_specs_capture_point_in_time_rules() -> None:
    assert len(CORE_CONCEPT_SPECS) == 7
    assert SWING_POINT_SPEC.formalization.value == "exact"
    assert all(criterion.requires_future_data for criterion in SWING_POINT_SPEC.criteria)
    assert FVG_SPEC.criteria[0].expression == "L[n+1] > H[n-1]"
    assert FVG_SPEC.confirmed_at_semantics.startswith("Close of candle n+1")


def test_json_round_trip_preserves_contract() -> None:
    fact = make_fact()
    restored = ObservableFact.model_validate_json(fact.model_dump_json())
    assert restored == fact
    assert restored.timeframe is Timeframe.M5


def test_semantic_scores_are_bounded_without_becoming_probabilities() -> None:
    assessment = SemanticAssessment(
        assessment_id="assessment-1",
        symbol="XAUUSD",
        as_of=T0,
        candidate_assessments=[
            CandidateAssessment(
                candidate_id="candidate-1",
                classification="meaningful_ssl_sweep",
                semantic_class=SemanticClass.VALID,
                quality=0.84,
            )
        ],
        multi_timeframe_coherence=0.73,
        effective_direction=Direction.BULLISH,
        overall_context_score=0.77,
        model="model-name",
        model_version="model-version",
        prompt_version="semantic-v0",
        temperature=0.0,
        input_state_hash="sha256:state",
        created_at=T0,
        knowledge_version="knowledge-v0",
    )
    assert assessment.overall_context_score == 0.77
    decision = SetupSemanticDecision(
        decision_id="semantic-decision-1",
        assessment_id=assessment.assessment_id,
        setup_candidate_id="setup-1",
        action=SemanticAction.ACCEPT,
        context_score=0.77,
        model="model-name",
        model_version="model-version",
        prompt_version="semantic-v0",
        temperature=0.0,
        input_state_hash="sha256:state",
        created_at=T0,
        knowledge_version="knowledge-v0",
    )
    assert decision.input_state_hash == assessment.input_state_hash
    assert decision.assessment_id == assessment.assessment_id
    with pytest.raises(ValidationError):
        CandidateAssessment(
            candidate_id="candidate-1",
            classification="invalid-score",
            semantic_class=SemanticClass.UNCERTAIN,
            quality=1.01,
        )


def test_passed_safety_cannot_hide_failed_checks() -> None:
    with pytest.raises(ValidationError, match="cannot contain failed checks"):
        SafetyAssessment(
            setup_candidate_id="setup-1",
            passed=True,
            checks={"spread_ok": False},
        )

    safety = SafetyAssessment(
        setup_candidate_id="setup-1",
        passed=False,
        checks={"spread_ok": False},
        rejection_codes=["SPREAD_TOO_WIDE"],
    )
    assert safety.passed is False


def test_session_targets_are_generic_and_require_session_metadata() -> None:
    target = TargetCandidate(
        candidate_id="target-asia-high",
        symbol="XAUUSD",
        price=3370.2,
        side=TargetSide.UPSIDE,
        target_type=TargetType.SESSION_HIGH,
        scope=TargetScope.SESSION,
        session=Session.ASIA,
        available_at=T0,
    )
    assert target.session is Session.ASIA
    with pytest.raises(ValidationError, match="require a concrete session"):
        TargetCandidate(
            candidate_id="target-invalid",
            symbol="XAUUSD",
            price=3370.2,
            side=TargetSide.UPSIDE,
            target_type=TargetType.SESSION_HIGH,
            scope=TargetScope.SESSION,
            available_at=T0,
        )
    with pytest.raises(ValidationError, match="only valid for session targets"):
        TargetCandidate(
            candidate_id="target-invalid-metadata",
            symbol="XAUUSD",
            price=3370.2,
            side=TargetSide.UPSIDE,
            target_type=TargetType.PREVIOUS_DAY_HIGH,
            scope=TargetScope.INTRADAY,
            session=Session.ASIA,
            available_at=T0,
        )


def test_legacy_rule_scoring_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="scoring_feature"):
        ConceptUsageSpec(
            concept_id="imbalance.fvg",
            timeframe=Timeframe.M15,
            role=TimeframeRole.SETUP,
            scoring_feature=True,
        )
    with pytest.raises(ValidationError, match="weight"):
        SetupRuleSpec(
            id="observable_fvg",
            description="FVG geometry exists.",
            severity=RuleSeverity.HARD,
            operator=RuleOperator.EXISTS,
            weight=0.2,
        )


def test_v0_close_acceptance_uses_one_setup_timeframe_close() -> None:
    policy = build_v0_close_acceptance_policy()
    rule = policy.to_rule()
    assert rule.parameters == {
        "timeframe": "setup_timeframe",
        "consecutive_closes": 1,
        "distance_buffer": 0.0,
    }
