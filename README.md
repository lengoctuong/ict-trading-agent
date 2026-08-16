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

The frozen design contract is in `docs/spec_v0.md`; implementation milestones
are in `docs/implementation_plan.md`, and pinned research provenance is in
`docs/source_registry.md`. The complete 74-message source snapshot is retained
in `chat_web/ICT-LLM-Trading-conversation.md`.

## Intentionally unresolved policies

The source design did not freeze these market semantics, so the package does
not silently invent defaults for them:

- the exact XAUUSD trading-day boundary/rollover;
- the concrete broker/data-source market calendar;
- session windows and overlap priority;
- multi-bar raid/MSS timing and semantic candidate-window bounds.

The v0 close-acceptance default is now frozen as one setup-timeframe close with
zero buffer; its alternative calibrations remain research rather than runtime
ambiguity. See `OPEN_QUESTIONS.md` for the complete status register.

`TradingDayPolicy` is therefore required by the XAUUSD v0 profile factory.
Structural relevance is assigned by the semantic evaluator rather than a
hard-coded swing rank.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m pytest
```
