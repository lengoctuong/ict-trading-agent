# Implementation plan

## M0 — Contracts and frozen spec

- Pydantic schemas, enums, invariants, lifecycle, tests.
- `docs/spec_v0.md` as the single implementation source.
- Contract patches: auditable LLM provenance, generic session targets, and no
  active-path legacy rule-score fields.

## M1 — Core infrastructure

- OHLC bar model with explicit open/close timestamps.
- Multi-timeframe feed that emits closed bars only.
- Append-only in-memory FactStore contract.
- Point-in-time MarketStateReducer.
- Timeframe/session utilities with configurable schedules and IANA timezone.

## M2 — Primitive pipeline

Status: implemented and covered by causal synthetic fixtures.

- ThreeBarSwingDetector.
- Session and previous-day reference facts.
- FVGGeometryDetector.
- Candle/displacement raw features.
- Broad structure-break and liquidity-raid candidates.
- Pinned source registry and explicit adaptation decisions.
- Closed-bar pipeline that performs duplicate preflight before append-only batch
  writes.

## M2.1 — Real-data and replay hardening

Status: implemented; concrete broker closure calendar remains open.

- Market-calendar-aware bar adjacency with explicit versionable closures.
- Mean/median body and range plus ATR raw baselines.
- Permissive directional-repricing candidates with per-criterion results.
- Append-only reference lifecycle; taken levels are single-use by default.
- Shared per-bar path for realtime, range replay, and restart catch-up.
- Explicit semantic-decision identity and setup-timeframe close-acceptance v0.

## M3 — Setup state machine

Status: M3.2 implemented with causal single- and multi-timeframe fixtures;
research windows remain versioned calibration parameters for M4.

```text
IDLE -> RAID -> SHIFT -> ENTRY_ZONE -> READY_FOR_LLM
```

- Global first-take liquidity lifecycle and append-only cross-TF RaidEpisode.
- Per-TF raid `BREACHED -> RECLAIMED` state from first breach, with continuous
  global-extreme updates and no same-bar re-breach requirement.
- Independent H1/M15 setup paths, M5 entry evidence, and setup-TF invalidation.
- Inside-shift M5 repricing/FVG linkage with usability checked at shift close.
- Stateful FVG touch/reaction/failure/expiry facts with multi-bar confirmation.
- Research-only multi-touch penetration, CE, full-fill, time-in-zone, and MAE
  path aggregates.
- Deterministic invalidation/expiry policies.
- Structured READY_FOR_LLM JSON output.
- Independent liquidity and structural-reference lifecycle.
- Detection/reference timeframe provenance and same-TF shift eligibility.
- Append-only STH/STL -> ITH/ITL -> LTH/LTL promotions.
- Effective swing rank resolved into PRICE_BREAK and SHIFT evidence as-of break.
- Canonical same-bar and permissive <=3-bar reclaim episodes.
- Raid-to-shift windows M5/M15/H1 = 12/8/4 tradable bars.
- Causal shift/repricing/FVG linkage and FVG expiry 24/16/6 bars.
- Append-only setup transitions and post-terminal 32/64-bar research observer.

## M4 — Replay/backtest harness

- Historical XAUUSD M5/M15/H1/H4 input.
- Exact replay at `as_of` without look-ahead.
- Deterministic snapshots and regression fixtures.

## M5 — Semantic evaluator

- Structured ACCEPT/REJECT output.
- Candidate classifications, context score, DOL, reason codes.
- Complete provenance and reproducibility logging.

## M6 — Risk and paper execution

- Deterministic gates, sizing, paper orders, and audit logs.
- No direct LLM-to-execution path.
