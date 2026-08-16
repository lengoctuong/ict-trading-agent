# Decision and open-question register

This file records current status explicitly. `OPEN` needs a policy, external
input, or research result; `RESOLVED` has an approved design; `IMPLEMENTED`
means that design is enforced by code and tests.

## Open / needs policy or calibration

1. **Concrete XAUUSD trading day — OPEN; production/backtest blocker.**
   `TradingDayPolicy` carries timezone and rollover configuration, but the
   project still needs the selected broker/data-source definition and helpers
   for trading-day ID, previous day, start, and end. Do not hard-code UTC,
   broker midnight, or New York 17:00 before that choice.

2. **Concrete market calendar — OPEN; real-data blocker.** The code now
   distinguishes wall-clock adjacency from market-sequence adjacency through
   an explicit closure calendar. Weekend and maintenance closures must still be
   supplied and versioned by the selected XAUUSD data source. An unexplained
   gap remains a missing-bar error.

3. **Session windows and overlap priority — OPEN.** Session names are frozen,
   but exact local windows and the primary session during overlaps are not.

4. **Multi-bar raid and MSS temporal matching — OPEN.** M2 supports the
   canonical same-bar breach/reclaim. Maximum reclaim span and allowed timing
   among raid, structural shift, displacement, and FVG remain research policy.

5. **Displacement calibration — OPEN, non-blocking research.** Directional
   candles now remain visible as permissive candidates. Baseline length and
   thresholds for body/range, body/baseline, wick, close location, ATR, and
   robust median features still need calibration by timeframe and session.

6. **Tick-boundary semantics — OPEN.** Strict tick-normalized comparisons mean
   a close exactly on a breached level is neither reclaim nor close-through.
   Equality/tolerance behavior must be selected for the actual price feed.

7. **TradingView transition snapshot — OPEN before M3.** The selected script is
   mutable and has no immutable commit. Every adopted RAID -> SHIFT ->
   ENTRY_ZONE transition must be frozen locally in contracts and fixtures.

8. **Candidate-window bounds — OPEN, operational calibration.** Structural
   relevance belongs to the semantic evaluator, but deterministic limits for
   age, distance, timeframe, and recent-candidate count are still needed before
   sending MarketState to an LLM.

9. **Close-acceptance calibration — OPEN, non-blocking research.** The v0
   default is implemented as one setup-timeframe close beyond the invalidation
   level with zero distance buffer. Alternatives such as two closes or an ATR
   buffer remain replay experiments, not runtime ambiguity.

## Resolved and implemented

- **Structural reference relevance — RESOLVED.** Machine code exposes valid
  confirmed swing-break candidates and keeps them `UNCLASSIFIED`; the semantic
  evaluator decides which reference is contextually significant.
- **Close acceptance v0 — IMPLEMENTED as a typed contract.** Setup timeframe,
  one consecutive close, zero buffer. Enforcement belongs to the M3 lifecycle.
- **Semantic decision identity — IMPLEMENTED.** `SetupSemanticDecision` has
  `decision_id` and `assessment_id`; `TradeDecision` references
  `semantic_decision_id`.
- **Reference-level lifecycle — IMPLEMENTED.** Default policy is append-only
  `ACTIVE -> TAKEN`, after which the level is historical and ineligible. Reuse
  requires an explicit `ReferenceLifecyclePolicy` override.
- **Displacement permissiveness — IMPLEMENTED.** Every directional candle can
  produce a repricing candidate with individual threshold results; thresholds
  do not erase evidence before semantic evaluation.
- **Replay/restart catch-up — IMPLEMENTED.** `process_range()` and `catch_up()`
  process every unseen closed bar through the same per-bar path as realtime.
- **Generic session targets — IMPLEMENTED.** `SESSION_HIGH` / `SESSION_LOW`
  carry a concrete Asia/London/NY AM/NY PM session.
- **LLM provenance — IMPLEMENTED.** Model, versions, prompt, temperature,
  input-state hash, creation time, and knowledge version are recorded.
- **Legacy rule scoring — REMOVED.** `scoring_feature` and rule `weight` are not
  active-path fields; semantic scoring belongs to the LLM.
