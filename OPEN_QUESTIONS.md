# Open questions from the source specification

These items are intentionally not resolved by implementation convenience.

1. **Trading-day boundary (blocking the final production preset).** The design
   requires a precise XAUUSD trading day, but does not choose broker day, UTC
   calendar day, or New York 17:00 rollover. `TradingDayPolicy` is required.

2. **Structural reference policy.** The design deliberately leaves open which
   STH/ITH/LTH is relevant for a structure-break candidate and delegates
   contextual relevance partly to the semantic evaluator. No hard-coded
   reference selector is included.

3. **Close acceptance.** Hard invalidation mentions "close acceptance beyond
   the sweep extreme" but does not define bar count, timeframe, or distance.
   `HardInvalidationRule.parameters` must carry the chosen policy later.

4. **Semantic decision identity.** `TradeDecision` references a
   `semantic_assessment_id`; `SemanticAssessment` has that ID, but
   `SetupSemanticDecision` has no independent ID. The implementation does not
   invent an additional reference field.

5. **Session windows and overlap policy.** Session names are frozen, but exact
   local start/end times and which session is primary during overlaps are not.
   `SessionSchedule` therefore requires configured IANA-timezone windows and
   an explicit priority when overlaps occur.

6. **Multi-bar raid and MSS timing.** The canonical same-bar breach/reclaim is
   defined, but the allowed multi-bar raid/reclaim span and the temporal link
   between raid, structure shift, displacement, and FVG remain research
   parameters.

7. **Displacement calibration.** The causal feature set is frozen, but baseline
   length and body/range, body/baseline, opposing-wick, and directional-close
   thresholds have not been calibrated per XAUUSD timeframe/session. Current
   defaults are explicit research parameters, not claims of universal validity.

8. **Reference-level lifecycle.** The source material does not define a single
   mechanical policy for when a swept/taken swing, session level, or previous-
   day level stops being eligible for later candidates. M2 leaves source facts
   immutable and does not silently deactivate or reuse them by preference.

9. **Tick-boundary close semantics.** M2 uses strict tick-normalized comparisons:
   a close exactly on a breached level is neither a reclaim nor a close-through
   break. Whether equality or a tolerance should count requires an explicit
   instrument/data-source policy.

10. **Mutable TradingView reference.** The selected TradingView publication has
    no immutable commit and its live feature set has expanded beyond the original
    Sweep -> MSS -> FVG flow. M3 must freeze every adopted transition in local
    contracts/tests rather than depend on the current upstream script behavior.

## Resolved by the updated planner transcript

- Session targets are generic `SESSION_HIGH` / `SESSION_LOW` records carrying
  a concrete `session`, so NY PM needs no dedicated target enum.
- Both semantic output schemas carry model/prompt provenance, input-state hash,
  creation time, and optional model/temperature/knowledge versions.
- `ConceptUsageSpec.scoring_feature` and `SetupRuleSpec.weight` were removed;
  semantic scoring belongs to the LLM and deterministic code retains only
  measurable rules and safety constraints.
