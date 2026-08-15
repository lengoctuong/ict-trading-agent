# ICT Trading Agent

Pydantic v0 contracts derived from the `ICT-Trading-Agent` design transcript.

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

