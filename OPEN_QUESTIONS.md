# Decision and open-question register

This file records current status explicitly. `OPEN` needs a policy, external
input, or research result; `RESOLVED` has an approved design; `IMPLEMENTED`
means that design is enforced by code and tests.

## Open / needs policy or calibration

1. **Concrete market calendar — OPEN; real-data blocker.** The code now
   distinguishes wall-clock adjacency from market-sequence adjacency through
   an explicit closure calendar. Weekend and maintenance closures must still be
   supplied and versioned by the selected XAUUSD data source. An unexplained
   gap remains a missing-bar error. Exness timestamps are UTC and completed
   source D1 candles define PDH/PDL; the season-dependent XAUUSD closure around
   21:00/22:00 UTC still belongs in this versioned source calendar, not in a
   fabricated UTC-midnight trading day.

2. **Session windows and overlap priority — OPEN.** Session names are frozen,
   but exact local windows and the primary session during overlaps are not.

3. **M3 window calibration — OPEN, non-blocking research.** Runtime v0 is
   frozen and implemented as reclaim <=3 bars; raid-to-shift M5/M15/H1 =
   12/8/4 bars; and FVG expiry = 24/16/6 bars. Replay may calibrate alternatives
   but must version any later change.

4. **Displacement calibration — OPEN, non-blocking research.** Directional
   candles now remain visible as permissive candidates. Baseline length and
   thresholds for body/range, body/baseline, wick, close location, ATR, and
   robust median features still need calibration by timeframe and session.

5. **Tick-boundary semantics — OPEN.** Strict tick-normalized comparisons mean
   a close exactly on a breached level is neither reclaim nor close-through.
   Equality/tolerance behavior must be selected for the actual price feed.

6. **Semantic candidate-window bounds — OPEN, operational calibration.** Structural
   relevance belongs to the semantic evaluator, but deterministic limits for
   age, distance, timeframe, and recent-candidate count are still needed before
   sending MarketState to an LLM.

7. **Close-acceptance calibration — OPEN, non-blocking research.** The v0
   default is implemented as one setup-timeframe close beyond the invalidation
   level with zero distance buffer. Alternatives such as two closes or an ATR
   buffer remain replay experiments, not runtime ambiguity.

## Resolved and implemented

- **M3 clock/data policy — IMPLEMENTED.** Exness source timestamps use UTC;
  D1/H4 and PDH/PDL follow completed source candles rather than a synthesized
  UTC-midnight day. New York remains the
  independent clock for sessions, killzones, and the 00:00 NY True Day Open.
  `build_exness_xauusd_intraday_v0()` records UTC raw time plus feed-defined D1.
- **M3.2 lifecycle — IMPLEMENTED.** One global RaidEpisode starts at first
  breach, tracks per-TF `BREACHED -> RECLAIMED` observations and continuously
  updates its extreme. Independent H1/M15 setup paths accept usable M5 FVGs
  formed inside the shift candle or after its confirmation. Stateful FVG zones
  and favorable reaction closes lead to `READY_FOR_LLM`; trading terminals
  remain terminal while research observation continues for calibration.
- **Multi-bar reclaim and timing — IMPLEMENTED as versioned research policy.**
  Same-bar reclaim is canonical; reclaim within three bars is permissive; late
  reclaim remains raw evidence. Shift and FVG clocks use tradable bar counts.
- **Liquidity versus structure lifecycle — IMPLEMENTED.** Liquidity is
  single-use globally per reference. A wick `TAKEN` observation does
  not erase structural value; structural references independently become
  `BROKEN` or explicitly `SUPERSEDED`.
- **Cross-timeframe provenance — IMPLEMENTED.** Every level interaction records
  detection and reference timeframe. Cross-TF close-through remains raw
  evidence; only same-TF close-through is eligible for a shift.
- **Swing hierarchy — IMPLEMENTED.** STH/STL observations are preserved and
  ITH/ITL then LTH/LTL promotions are appended without rewriting lower ranks;
  PRICE_BREAK and SHIFT evidence resolve the effective rank as-of break.
- **Local M3 transition snapshot — IMPLEMENTED.** The adopted rules from the
  mutable TradingView source are restated in `chat_web/M3-plan.md`, contracts,
  reason codes, and causal fixtures.
- **Structural reference relevance — RESOLVED.** Machine code exposes valid
  confirmed swing-break candidates and keeps them `UNCLASSIFIED`; the semantic
  evaluator decides which reference is contextually significant.
- **Close acceptance v0 — IMPLEMENTED and enforced.** One setup-timeframe close
  beyond the raid extreme with zero buffer invalidates the setup.
- **Semantic decision identity — IMPLEMENTED.** `SetupSemanticDecision` has
  `decision_id` and `assessment_id`; `TradeDecision` references
  `semantic_decision_id`.
- **Reference-level lifecycle — IMPLEMENTED.** Default liquidity policy is
  append-only global `ACTIVE -> TAKEN` per reference. Reuse requires an
  explicit `ReferenceLifecyclePolicy` override.
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
