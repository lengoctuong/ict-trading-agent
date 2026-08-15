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

4. **NY PM target mismatch.** Earlier liquidity-pool scope includes NY PM
   high/low, while the final `TargetCandidate.target_type` literal omits them.
   The implementation follows the final literal and does not add NY PM targets.

5. **LLM provenance mismatch.** The design requires logging model version,
   temperature, input-state hash, and timestamp, but the final v0 schemas only
   define `model` and `prompt_version`. The implementation follows the explicit
   final fields pending a provenance decision.

6. **SetupSpec scoring mismatch.** The earlier `SetupSpec` has weighted soft
   rules, while the final architecture limits rules to invariants/measurable
   constraints and moves semantic scoring to the LLM. The earlier config model
   is retained for compatibility, but no scoring engine is implemented.

7. **Semantic decision identity.** `TradeDecision` references a
   `semantic_assessment_id`; `SemanticAssessment` has that ID, but
   `SetupSemanticDecision` has no independent ID. The implementation does not
   invent an additional reference field.

