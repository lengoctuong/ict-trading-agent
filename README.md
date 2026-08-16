# ICT Trading Agent

Point-in-time-safe Pydantic contracts and deterministic market infrastructure
derived from the `ICT-Trading-Agent` planner transcript.

The package freezes the agreed boundary:

```text
Market Data
-> ObservableFact
-> ConceptCandidate
-> MarketState
-> SetupCandidate
-> SemanticAssessment (LLM)
-> Safety/Risk
-> TradeDecision
```

Facts and safety constraints are deterministic. The LLM may assess relevance,
quality, multi-timeframe coherence, and DOL selection, but it cannot rewrite
facts, timestamps, geometry, position sizing, or risk checks.

## Current implementation

- Strict Pydantic contracts for facts, candidates, market state, semantic
  outputs, safety, and trade decisions.
- Append-only fact/candidate stores and a point-in-time `MarketStateReducer`.
- Closed-bar multi-timeframe feed and configurable IANA-timezone sessions.
- Strict three-bar swing and FVG geometry detectors with causal availability.
- Completed-session and previous-day reference-level fact builders.
- Causal candle features and configurable displacement candidates.
- Strict level breach/reclaim facts, same-bar liquidity-raid candidates, and
  unclassified close-through-swing structure candidates.
- An M2 closed-bar pipeline with stable IDs and append-only batch preflight.
- Market-calendar-aware adjacency for explicit weekend/maintenance closures.
- Single-use reference lifecycle plus range replay and restart catch-up APIs.
- Traceable semantic decisions and a typed v0 close-acceptance contract.
- Global append-only raid episodes with independent H1/M15 setup paths, M5
  entry evidence, and setup-timeframe invalidation.
- Stateful FVG entry zones (`FRESH -> TOUCHED -> REACTED/FAILED/EXPIRED`) and
  post-terminal near-miss observation for replay calibration.
- Per-timeframe raid observation state (`BREACHED -> RECLAIMED`) with continuous
  global-extreme updates, including reclaim bars that do not re-breach.
- M5 displacement/FVG evidence contained inside a later-confirmed M15/H1 shift
  candle, promoted only if the zone is still usable at shift confirmation.
- Independent liquidity/structure lifecycle, cross-timeframe provenance, and
  append-only swing hierarchy promotions.
- Traceable `ReadyForLLMPayload` snapshots containing raw evidence, targets,
  and supplied context.

The frozen design contract is in `docs/spec_v0.md`; implementation milestones
are in `docs/implementation_plan.md`, and pinned research provenance is in
`docs/source_registry.md`. The complete 74-message source snapshot is retained
in `chat_web/ICT-LLM-Trading-conversation.md`; the frozen M3 directive is in
`chat_web/M3-plan.md`.

## Intentionally unresolved policies

The source design did not freeze these market semantics, so the package does
not silently invent defaults for them:

- the concrete broker/data-source market calendar;
- session windows and overlap priority;
- semantic candidate-window bounds and replay calibration of M3 windows.

The v0 close-acceptance default is now frozen as one setup-timeframe close with
zero buffer; its alternative calibrations remain research rather than runtime
ambiguity. See `OPEN_QUESTIONS.md` for the complete status register.

`build_exness_xauusd_intraday_v0()` interprets Exness timestamps as UTC and
uses completed source D1 candles for PDH/PDL; it does not invent a UTC-midnight
or New-York trading day. New York/DST-aware session clocks remain separate.
Structural relevance is assigned by the semantic evaluator, while each break
records the effective STH/ITH/LTH rank known at break time.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m pytest
```
