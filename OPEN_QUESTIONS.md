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

## Resolved by the updated planner transcript

- Session targets are generic `SESSION_HIGH` / `SESSION_LOW` records carrying
  a concrete `session`, so NY PM needs no dedicated target enum.
- Both semantic output schemas carry model/prompt provenance, input-state hash,
  creation time, and optional model/temperature/knowledge versions.
- `ConceptUsageSpec.scoring_feature` and `SetupRuleSpec.weight` were removed;
  semantic scoring belongs to the LLM and deterministic code retains only
  measurable rules and safety constraints.
