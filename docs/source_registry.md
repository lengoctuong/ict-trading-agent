# Source registry

Status: pinned for M2 research and implementation provenance.

Reviewed: 2026-08-16.

The M2.1 corrections and decision statuses are preserved in
`chat_web/M2-feedback.md`; it is a project review input, not an additional ICT
definition or detector authority.

No single external source is authoritative for the complete system. Project
contracts and approved decisions remain in `docs/spec_v0.md`; external sources
have bounded roles and must pass point-in-time replay tests before adoption.

## Pinned sources

### ICT Knowledge Library

- URL: <https://github.com/SrsBlack/ict-knowledge-library>
- Commit: `31e13d70132fde1e3a29d6afe0bf97bfbff8094c`
- Role: ontology, definitions, concept relationships, and upstream citation IDs.
- Not authoritative for: detector algorithms, backtests, or profitability.
- M2 files audited:
  - `concepts/02-liquidity/liquidity-sweep.md`
  - `concepts/01-market-structure/mss.md`
  - `concepts/09-displacement/displacement-definition.md`
  - `concepts/09-displacement/displacement-strength-criteria.md`
  - `concepts/31-models/ict-2022-model.md`

### smart-money-concepts

- URL: <https://github.com/joshyattridge/smart-money-concepts>
- Commit: `1b62fd6c41e1f508e7ed76831a039fa4c82d42f6`
- Package version at that commit: `0.0.27`.
- Role: primitive implementation and test reference.
- M2 code audited: `smartmoneyconcepts/smc.py` functions `fvg`,
  `swing_highs_lows`, `bos_choch`, `liquidity`, and `previous_high_low`.
- Required adaptations:
  - centered swing windows are only visible after their right-hand bars close;
  - future `MitigatedIndex`, `Swept`, and follow-through results cannot be
    backfilled into historical state;
  - previous-period levels use the project's explicit `TradingDayPolicy` rather
    than an implicit pandas calendar boundary;
  - the library's global-range liquidity grouping is a reference algorithm,
    not the v0 liquidity-pool ontology.

### TradingView ICT Algo

- URL:
  <https://www.tradingview.com/script/SD8VyvVg-ICT-Algo-Sweep-MSS-High-Prob-FVG-IFVG/>
- Publisher: DivergentTrades.
- Role: setup sequencing and lifecycle reference only.
- Adopted sequence: HTF/reference liquidity sweep -> close-through-pivot shift
  -> qualified FVG -> retrace/mitigation opportunity.
- Not adopted in M2: KNN/AI Pivot Hunter, IFVG, SMT, killzone hard filters,
  EMA/trend filters, automatic targets, or performance claims.
- Reproducibility warning: this is a mutable TradingView publication without a
  Git commit. Every rule taken from it must be restated in project tests and
  contracts; no live upstream behavior is imported at runtime.
- The locally frozen M3 adaptation and research windows are retained in
  `chat_web/M3-plan.md`, captured from the planner share at
  <https://chatgpt.com/share/6a81f8a0-39e0-83ec-9a96-e752c1d84802>.
- The M3.1 architecture review and mandatory regression cases are retained in
  `chat_web/M3-feedback.md`.

## M2 adoption decisions

| Concern | Decision |
|---|---|
| Candle features | Store raw causal measurements at bar close. |
| Displacement | Emit every directional repricing candidate with per-threshold results; thresholds are research configuration, not a hard gate. |
| Follow-through | Later append-only evidence, never backfilled into the original candle fact. |
| Level breach | Strict tick-normalized excursion beyond a known reference. |
| Canonical sweep | Same-bar breach plus close reclaim; wick fraction is a feature, not a v0 hard gate. |
| Multi-bar sweep | Deferred pending an explicit maximum span and reclaim policy. |
| Reference lifecycle | First breach appends `TAKEN`; default policy prevents later reuse while preserving history. |
| Structure break | Close through an already-confirmed swing; initially unclassified. |
| BOS/CHoCH/MSS | Do not hard-classify until structural-reference and temporal-link policies are frozen. |
