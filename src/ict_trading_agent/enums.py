from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Timeframe(str, Enum):
    W1 = "W1"
    D1 = "D1"
    H4 = "H4"
    H1 = "H1"
    M15 = "M15"
    M5 = "M5"
    M1 = "M1"


class TimeframeRole(str, Enum):
    MACRO = "macro"
    BIAS = "bias"
    SETUP = "setup"
    ENTRY = "entry"
    REFINEMENT = "refinement"


class ConceptKind(str, Enum):
    PRIMITIVE = "primitive"
    COMPOSITE = "composite"
    DERIVED = "derived"


class FormalizationLevel(str, Enum):
    EXACT = "exact"
    PARAMETRIC = "parametric"
    SEMANTIC = "semantic"


class CriterionStage(str, Enum):
    OCCURRENCE = "occurrence"
    CONFIRMATION = "confirmation"
    INVALIDATION = "invalidation"


class TimeframeScope(str, Enum):
    BAR_BASED = "bar_based"
    SESSION_BASED = "session_based"
    REFERENCE_LEVEL = "reference_level"
    GLOBAL = "global"


class ParameterDType(str, Enum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STR = "str"
    ENUM = "enum"


class TradingStyle(str, Enum):
    INTRADAY = "intraday"
    SCALP = "scalp"


class HoldingHorizon(str, Enum):
    TRADING_DAY = "trading_day"


class Session(str, Enum):
    ASIA = "asia"
    LONDON = "london"
    NY_AM = "ny_am"
    NY_PM = "ny_pm"
    OFF_SESSION = "off_session"


class FactType(str, Enum):
    SWING_POINT = "swing_point"
    SWING_PROMOTION = "swing_promotion"
    PRICE_BREAK = "price_break"
    LEVEL_BREACH = "level_breach"
    LEVEL_RECLAIM = "level_reclaim"
    FVG_GEOMETRY = "fvg_geometry"
    CANDLE_FEATURES = "candle_features"
    SESSION_LEVEL = "session_level"
    PREVIOUS_DAY_LEVEL = "previous_day_level"
    REFERENCE_STATE = "reference_state"
    STRUCTURE_STATE = "structure_state"
    FVG_REACTION = "fvg_reaction"
    RESEARCH_OBSERVATION = "research_observation"


class CandidateType(str, Enum):
    LIQUIDITY_EVENT = "liquidity_event"
    DISPLACEMENT = "displacement"
    STRUCTURE_BREAK = "structure_break"
    SHIFT = "shift"
    MSS = "mss"
    FVG = "fvg"
    TARGET = "target"


class SemanticClass(str, Enum):
    VALID = "valid"
    WEAK = "weak"
    IRRELEVANT = "irrelevant"
    CONFLICTING = "conflicting"
    UNCERTAIN = "uncertain"


class SetupStatus(str, Enum):
    DETECTED = "detected"
    FORMING = "forming"
    READY_FOR_LLM = "ready_for_llm"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ENTERED = "entered"
    CLOSED = "closed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    RISK_REJECTED = "risk_rejected"


class SemanticAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"


class TargetScope(str, Enum):
    LOCAL = "local"
    SESSION = "session"
    INTRADAY = "intraday"
    EXTERNAL = "external"
    MACRO = "macro"


class TargetSide(str, Enum):
    UPSIDE = "upside"
    DOWNSIDE = "downside"


class TargetType(str, Enum):
    LOCAL_SWING = "local_swing"
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"
    PREVIOUS_DAY_HIGH = "previous_day_high"
    PREVIOUS_DAY_LOW = "previous_day_low"
    EXTERNAL_LIQUIDITY = "external_liquidity"


class TradeAction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no_trade"


class SetupDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class RuleSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class RuleOperator(str, Enum):
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CUSTOM = "custom"


class HardInvalidationRuleType(str, Enum):
    PRICE_CLOSE_BEYOND_LEVEL = "price_close_beyond_level"
    OPPOSITE_STRUCTURE = "opposite_structure"
    TIME_EXPIRY = "time_expiry"
    TRADING_DAY_END = "trading_day_end"


class InvalidationTimeframeBasis(str, Enum):
    SETUP_TIMEFRAME = "setup_timeframe"


class SwingSide(str, Enum):
    HIGH = "high"
    LOW = "low"


class SwingRank(str, Enum):
    SHORT_TERM = "short_term"
    INTERMEDIATE = "intermediate"
    LONG_TERM = "long_term"


class SwingRelation(str, Enum):
    HH = "HH"
    LH = "LH"
    HL = "HL"
    LL = "LL"
    EQUAL = "EQUAL"
    UNKNOWN = "UNKNOWN"


class StructureBreakType(str, Enum):
    BOS = "bos"
    CHOCH = "choch"
    UNCLASSIFIED = "unclassified"


class StructureScope(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class LiquiditySide(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class ReferenceStatus(str, Enum):
    ACTIVE = "active"
    TAKEN = "taken"


class StructureReferenceStatus(str, Enum):
    ACTIVE = "active"
    BROKEN = "broken"
    SUPERSEDED = "superseded"


class FvgLifecycle(str, Enum):
    FRESH = "fresh"
    TOUCHED = "touched"
    CE_REACHED = "ce_reached"
    FULLY_FILLED = "fully_filled"
    REACTED = "reacted"
