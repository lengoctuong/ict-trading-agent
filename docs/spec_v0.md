# ICT Trading Agent — Spec v0

Status: frozen core contracts; implementation policies remain versioned and testable.

Source of design decisions: the planner transcript in
`chat_web/ICT-LLM-Trading-conversation.md`. Later decisions supersede earlier
ones when the architecture changed from deterministic concept booleans and
rule scoring to facts, permissive candidates, LLM semantics, and deterministic
safety.

## 1. System boundary

```text
Market Data
-> ObservableFact
-> ConceptCandidate
-> MarketState
-> SetupCandidate
-> SemanticAssessment / SetupSemanticDecision [LLM]
-> SafetyAssessment [deterministic]
-> TradeDecision
```

Machine code owns observations, timestamps, geometry, closed-bar semantics,
candidate generation, lifecycle terminals, risk, sizing, and execution
constraints. The LLM owns contextual relevance, semantic quality,
multi-timeframe coherence, structural-reference significance, DOL selection,
and ACCEPT/REJECT. It cannot rewrite facts or override safety.

## 2. Trading profile v0

- Instrument: XAUUSD.
- Style: intraday; no overnight holding.
- Session-aware, not session-filtered.
- Enabled sessions: Asia, London, NY AM, NY PM.
- Session is a contextual feature, not a hard entry requirement.
- Timeframe roles:
  - W1: macro context.
  - D1/H4: directional bias.
  - H1/M15: setup.
  - M5: entry.
  - M1: optional refinement.
- Target universe: local swing, session high/low, previous-day high/low, and
  external liquidity.
- M3 data-clock freeze: Exness timestamps/candles are UTC; D1/H4 and PDH/PDL
  use Exness candle boundaries. New York time remains a separate contextual
  clock for sessions, killzones, and ICT True Day Open 00:00 NY.

## 3. Phase-0 concept universe

Runtime concepts remain logically separate from timeframe occurrence and
timeframe role.

1. SwingPoint
2. StructureScope
3. BOS
4. CHoCH
5. MSS
6. LiquidityPool
7. LiquiditySweep
8. ExternalRangeLiquidity
9. DrawOnLiquidity
10. ReferenceLiquidity
11. DealingRange
12. PremiumDiscount
13. Displacement
14. DisplacementStrength
15. FairValueGap
16. FVGLifecycle
17. SessionContext
18. AsianRange

The first vertical slice only implements the primitive chain needed to produce
a reproducible setup candidate. OB, IFVG, BPR, OTE, SMT, Silver Bullet, PO3,
Judas, macros, and other deferred concepts are out of scope.

## 4. Point-in-time invariants

1. Every datetime is timezone-aware.
2. `occurred_at <= confirmed_at <= available_at` when confirmation exists.
3. A state may only expose facts/candidates where `available_at <= as_of`.
4. Multi-timeframe detectors consume closed bars only.
5. Higher-timeframe developing candles are not silently treated as closed.
6. Swing/FVG occurrence may belong to an earlier candle, but visibility begins
   only after the confirming right-side candle closes.
7. Historical objects are append-only; future lifecycle observations do not
   mutate what a past replay could see.
8. Concept definitions are separate from detector implementations and
   provenance.
9. "Consecutive" means consecutive tradable bars under an explicit data-source
   calendar. Wall-clock equality is only the default when no closure intervenes;
   unexplained gaps remain invalid.

## 5. Primitive semantics

### SwingPoint

Canonical three-bar wick geometry after tick normalization:

```text
Swing high: H[n] > H[n-1] and H[n] > H[n+1]
Swing low:  L[n] < L[n-1] and L[n] < L[n+1]
```

`occurred_at` belongs to `n`; confirmation/availability begins at close of
`n+1`. STH -> ITH -> LTH promotions are later append-only observations.

### Liquidity

A confirmed swing/session/previous-day extreme can create a reference pool.
Pool taken is a breach; a canonical same-bar sweep additionally requires close
reclaim. The first breach appends a `TAKEN` lifecycle observation and makes the
reference ineligible for another liquidity event on that detection timeframe
under the default single-use policy. Reuse requires an explicit override.
Reclaim within three detection-timeframe bars creates a permissive raid; later
reclaim remains logged without setup promotion. Nearby evidence for one
reference is grouped into a single raid episode.

Liquidity lifecycle and structural lifecycle are independent. A wick breach
does not erase a swing's structural role. Same-timeframe close-through appends
`BROKEN`; a later reference-selection policy may explicitly append
`SUPERSEDED`. Cross-timeframe interaction records both timeframes but cannot
declare the higher-timeframe structure broken.

### Displacement

Store raw features before semantic classification: body/range,
body-vs-baseline, opposing wick/range, close location, ATR, and mean/median body
and range baselines. Every directional candle may remain visible as a permissive
repricing candidate with individual criterion results. Operational thresholds
are research parameters, not hard evidence gates or universal ICT truth.
Follow-through is later evidence and cannot be backfilled into the original
state.

### FVG

Canonical wick geometry:

```text
Bullish: L[n+1] > H[n-1]
Bearish: H[n+1] < L[n-1]
```

FVG geometry is deterministic and independent of displacement. The LLM may
assess relevance/quality, not existence. Lifecycle observations include touch,
penetration, CE reach, full fill, and favorable reaction close.

### Structure and MSS

A structure-break fact is close through a confirmed reference swing. BOS,
CHoCH, internal/external significance, and relevant-reference selection are
candidate/semantic concerns when ambiguous. MSS relates CHoCH, matching
displacement, and a causally linked FVG without double-counting them as four
independent score components.

STH/STL facts remain immutable. Three same-rank extrema can append an ITH/ITL
promotion; the same procedure can append LTH/LTL from intermediate swings.
Promotion never removes or rewrites the lower-rank observation.

## 6. Setup lifecycle

TradingView ICT-2022/Silver-Bullet implementations are reference state
machines, not authoritative truth. The permissive sequence is:

```text
IDLE
-> RAID_DETECTED
-> SHIFT_DETECTED
-> ENTRY_ZONE_AVAILABLE
-> READY_FOR_LLM
```

Mapping to active v0 statuses:

```text
possible liquidity raid                         -> DETECTED
raid + possible structural/delivery transition -> FORMING
shift + linked FVG + retrace/reaction evidence -> READY_FOR_LLM
LLM                                             -> ACCEPTED | REJECTED
safety                                          -> ENTERED | RISK_REJECTED
position completion                             -> CLOSED
```

Frozen M3 research windows use tradable-bar counts:

```text
multi-bar reclaim: <= 3 bars
raid -> shift:     M5=12, M15=8, H1=4
FVG expiry:        M5=24, M15=16, H1=6 from FVG availability
repricing candle:  shift bar or its next bar
```

A shift is eligible only when its setup-timeframe candle closes through a
confirmed swing on that same timeframe in the raid direction. It remains
`UNCLASSIFIED`; the semantic evaluator decides whether it is noise, internal
CHoCH, or a meaningful reversal. The linked FVG must be formed by the selected
repricing candle. Touch records `touched`, `penetration_fraction`, and
`favorable_close_outside`; only a favorable close outside the zone promotes the
setup to `READY_FOR_LLM`.

Every setup origin and transition is append-only. Failed links, late events,
touch-only reactions, thresholds, and reason codes remain available for M4
research. A READY payload carries the reconstructed setup, all referenced raw
facts/candidates, available untaken targets, and supplied context.

Reference implementations suggest invalidating/discarding when structure is
reclaimed, the target is hit before entry, the entry-zone/FVG opportunity
fails, or the setup times out. Close acceptance uses the frozen v0 default;
expiry remains configurable until its lifecycle policy is selected.

An FVG reaction candidate should expose at least:

```text
touched
penetration_fraction
favorable_close_outside
```

## 7. LLM audit contract

Every semantic output records model, optional model version, prompt version,
optional temperature, input-state hash, creation timestamp, and optional
knowledge version. Scores are ordinal/self-assessment values, never win
probabilities. `SetupSemanticDecision` has its own decision ID and references
the broader semantic-assessment ID; `TradeDecision` references the semantic
decision ID.

## 8. Safety contract

Deterministic checks own data freshness, spread, entry/stop validity, RR, daily
loss, exposure, position limits, trading-day validity, sizing, and execution.
No LLM output can override a failed safety check.

The v0 close-acceptance contract is enforced as one close on the setup
timeframe beyond the raid extreme with zero distance buffer. Alternative
levels, counts, or buffers are research configurations and must not change the
recorded v0 default silently.

## 9. Reference-source roles

- ICT Knowledge Library: ontology and definitions.
- smart-money-concepts: primitive/reference detector implementations; outputs
  must be re-timestamped for point-in-time availability.
- TradingView Sweep -> MSS -> FVG implementations: setup sequencing,
  lifecycle, invalidation, reference selection, and FVG reaction semantics.

`smc_quant`, BAKOME, and other previously discussed repositories are not active
v0 sources. Adding another source later requires an explicit source-registry
decision and a bounded role.

## 10. Remaining open policies

- Concrete broker/data-source market calendar and closure versioning.
- Semantic candidate-window age, distance, timeframe, and count bounds.
- Exact session windows and overlap policy.
- Replay calibration of the versioned M3 reclaim, shift, and FVG-expiry windows.
- Per-timeframe/session displacement baseline and threshold calibration.
- Equality/tolerance semantics for closes exactly on a reference level.
