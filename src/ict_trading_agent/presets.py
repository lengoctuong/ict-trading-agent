from __future__ import annotations

from .config import ConceptSpec, CriterionSpec, ParameterSpec
from .enums import (
    ConceptKind,
    CriterionStage,
    FormalizationLevel,
    ParameterDType,
    Timeframe,
    TimeframeScope,
)


ALL_BAR_TIMEFRAMES = [
    Timeframe.W1,
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M15,
    Timeframe.M5,
    Timeframe.M1,
]


SWING_POINT_SPEC = ConceptSpec(
    id="structure.swing_point",
    version="0.1.0",
    name="Swing Point",
    category="market_structure",
    kind=ConceptKind.PRIMITIVE,
    formalization=FormalizationLevel.EXACT,
    timeframe_scope=TimeframeScope.BAR_BASED,
    supported_timeframes=ALL_BAR_TIMEFRAMES,
    description=(
        "Three-bar local high/low used as the primitive building block of "
        "market structure. Equality is strict after tick normalization."
    ),
    criteria=[
        CriterionSpec(
            id="swing_high",
            stage=CriterionStage.CONFIRMATION,
            description="Middle high exceeds both adjacent highs.",
            expression="H[n] > H[n-1] and H[n] > H[n+1]",
            requires_future_data=True,
        ),
        CriterionSpec(
            id="swing_low",
            stage=CriterionStage.CONFIRMATION,
            description="Middle low is below both adjacent lows.",
            expression="L[n] < L[n-1] and L[n] < L[n+1]",
            requires_future_data=True,
        ),
    ],
    occurred_at_semantics="Timestamp of middle candle n containing the swing extreme.",
    confirmed_at_semantics="Close timestamp of candle n+1.",
)


LIQUIDITY_POOL_SPEC = ConceptSpec(
    id="liquidity.pool",
    version="0.1.0",
    name="Liquidity Pool",
    category="liquidity",
    kind=ConceptKind.DERIVED,
    formalization=FormalizationLevel.EXACT,
    timeframe_scope=TimeframeScope.REFERENCE_LEVEL,
    description="An unswept buy-side or sell-side reference price.",
    dependencies=[
        "structure.swing_point",
        "reference.session_extreme",
        "reference.previous_day_extreme",
    ],
    occurred_at_semantics="Occurrence time of the source structural/reference level.",
    confirmed_at_semantics="Time the source level itself becomes confirmed.",
)


LIQUIDITY_SWEEP_SPEC = ConceptSpec(
    id="liquidity.sweep",
    version="0.1.0",
    name="Liquidity Sweep",
    category="liquidity",
    kind=ConceptKind.COMPOSITE,
    formalization=FormalizationLevel.EXACT,
    timeframe_scope=TimeframeScope.BAR_BASED,
    supported_timeframes=ALL_BAR_TIMEFRAMES,
    description="Same-bar breach and close reclaim of a known liquidity pool.",
    dependencies=["liquidity.pool"],
    criteria=[
        CriterionSpec(
            id="bsl_sweep",
            stage=CriterionStage.CONFIRMATION,
            description="Buy-side level is breached and reclaimed below on close.",
            expression="high[n] > pool.price and close[n] < pool.price",
        ),
        CriterionSpec(
            id="ssl_sweep",
            stage=CriterionStage.CONFIRMATION,
            description="Sell-side level is breached and reclaimed above on close.",
            expression="low[n] < pool.price and close[n] > pool.price",
        ),
    ],
    occurred_at_semantics="Bar that trades through the liquidity level.",
    confirmed_at_semantics="Close of the bar that reclaims the liquidity level.",
)


DISPLACEMENT_SPEC = ConceptSpec(
    id="delivery.displacement",
    version="0.1.0",
    name="Displacement",
    category="delivery",
    kind=ConceptKind.PRIMITIVE,
    formalization=FormalizationLevel.PARAMETRIC,
    timeframe_scope=TimeframeScope.BAR_BASED,
    supported_timeframes=ALL_BAR_TIMEFRAMES,
    description="Single-candle directional repricing measured against a local baseline.",
    parameters=[
        ParameterSpec(
            name="body_multiplier",
            description="Body size relative to recent baseline.",
            dtype=ParameterDType.FLOAT,
            default=1.5,
            min_value=0.0,
            research_parameter=True,
        ),
        ParameterSpec(
            name="min_body_ratio",
            description="Minimum body divided by candle range.",
            dtype=ParameterDType.FLOAT,
            default=0.70,
            min_value=0.0,
            max_value=1.0,
            research_parameter=True,
        ),
        ParameterSpec(
            name="max_opposing_wick_ratio",
            description="Maximum opposing wick divided by candle range.",
            dtype=ParameterDType.FLOAT,
            default=0.20,
            min_value=0.0,
            max_value=1.0,
            research_parameter=True,
        ),
        ParameterSpec(
            name="baseline_lookback",
            description="Recent bars used to estimate normal candle body.",
            dtype=ParameterDType.INT,
            min_value=1.0,
            research_parameter=True,
        ),
    ],
    occurred_at_semantics="Timestamp of the displacement candle.",
    confirmed_at_semantics=(
        "Close of the displacement candle; FVG and follow-through are later evidence, "
        "not retroactive confirmation."
    ),
)


FVG_SPEC = ConceptSpec(
    id="imbalance.fvg",
    version="0.1.0",
    name="Fair Value Gap",
    category="imbalance",
    kind=ConceptKind.PRIMITIVE,
    formalization=FormalizationLevel.EXACT,
    timeframe_scope=TimeframeScope.BAR_BASED,
    supported_timeframes=ALL_BAR_TIMEFRAMES,
    description="Canonical wick-based three-candle fair value gap geometry.",
    criteria=[
        CriterionSpec(
            id="bullish_fvg",
            stage=CriterionStage.CONFIRMATION,
            description="Right candle low is strictly above left candle high.",
            expression="L[n+1] > H[n-1]",
            requires_future_data=True,
        ),
        CriterionSpec(
            id="bearish_fvg",
            stage=CriterionStage.CONFIRMATION,
            description="Right candle high is strictly below left candle low.",
            expression="H[n+1] < L[n-1]",
            requires_future_data=True,
        ),
    ],
    occurred_at_semantics="Timestamp of middle candle n.",
    confirmed_at_semantics="Close of candle n+1 when the geometry is observable.",
)


STRUCTURE_BREAK_SPEC = ConceptSpec(
    id="structure.break",
    version="0.1.0",
    name="Structure Break",
    category="market_structure",
    kind=ConceptKind.DERIVED,
    formalization=FormalizationLevel.EXACT,
    timeframe_scope=TimeframeScope.BAR_BASED,
    supported_timeframes=ALL_BAR_TIMEFRAMES,
    description=(
        "Close through a confirmed reference swing; prior direction classifies the "
        "candidate as BOS, CHoCH, or unclassified."
    ),
    dependencies=["structure.swing_point"],
    criteria=[
        CriterionSpec(
            id="bullish_break",
            stage=CriterionStage.CONFIRMATION,
            description="Close is strictly above the reference swing high.",
            expression="close[n] > reference_swing_high",
        ),
        CriterionSpec(
            id="bearish_break",
            stage=CriterionStage.CONFIRMATION,
            description="Close is strictly below the reference swing low.",
            expression="close[n] < reference_swing_low",
        ),
    ],
    occurred_at_semantics="Candle that closes beyond the confirmed reference swing.",
    confirmed_at_semantics="Close of the breaking candle.",
)


MSS_SPEC = ConceptSpec(
    id="structure.mss",
    version="0.1.0",
    name="Market Structure Shift",
    category="market_structure",
    kind=ConceptKind.COMPOSITE,
    formalization=FormalizationLevel.PARAMETRIC,
    timeframe_scope=TimeframeScope.BAR_BASED,
    supported_timeframes=[
        Timeframe.H4,
        Timeframe.H1,
        Timeframe.M15,
        Timeframe.M5,
        Timeframe.M1,
    ],
    description="Reversal-context CHoCH with matching displacement and linked FVG.",
    dependencies=[
        "structure.break",
        "delivery.displacement",
        "imbalance.fvg",
    ],
    criteria=[
        CriterionSpec(
            id="choch_required",
            stage=CriterionStage.CONFIRMATION,
            description="The structure break is classified as CHoCH.",
            expression="break_type == CHOCH",
        ),
        CriterionSpec(
            id="displacement_required",
            stage=CriterionStage.CONFIRMATION,
            description="A directionally matching displacement candidate is linked.",
            expression="matching_displacement == true",
        ),
        CriterionSpec(
            id="fvg_required",
            stage=CriterionStage.CONFIRMATION,
            description="A causally linked, directionally matching FVG is available.",
            expression="linked_fvg == true",
        ),
    ],
    occurred_at_semantics="Timestamp of the underlying CHoCH break.",
    confirmed_at_semantics=(
        "First timestamp when CHoCH, matching displacement, and linked FVG are all "
        "observable."
    ),
)


CORE_CONCEPT_SPECS = {
    spec.id: spec
    for spec in (
        SWING_POINT_SPEC,
        LIQUIDITY_POOL_SPEC,
        LIQUIDITY_SWEEP_SPEC,
        DISPLACEMENT_SPEC,
        FVG_SPEC,
        STRUCTURE_BREAK_SPEC,
        MSS_SPEC,
    )
}

