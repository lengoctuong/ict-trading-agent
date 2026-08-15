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

The frozen design contract is in `docs/spec_v0.md`; implementation milestones
are in `docs/implementation_plan.md`. The complete 74-message source snapshot
is retained in `chat_web/ICT-LLM-Trading-conversation.md`.

## Intentionally unresolved policies

The source design did not freeze these market semantics, so the package does
not silently invent defaults for them:

- the exact XAUUSD trading-day boundary/rollover;
- the policy for selecting a structurally relevant STH/ITH/LTH reference;
- the formula for close acceptance beyond a hard invalidation level.

`TradingDayPolicy` is therefore required by the XAUUSD v0 profile factory,
while structural-reference and close-acceptance policies remain downstream
configuration contracts.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m pytest
```
