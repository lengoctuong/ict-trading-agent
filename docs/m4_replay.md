# M4.1 Exness ingestion and point-in-time replay

M4.1 builds a research audit path around the production M2 and M3 detectors. It
does not optimize PnL, tune detector parameters, classify ICT relevance, or
simulate execution.

## Source contract

`ExnessCsvLoader` accepts native MT5 CSV, semicolon-delimited, or tab-delimited
rate exports. Headers are normalized, including the standard MT5 form:

```text
<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>
```

The source clock is explicitly UTC. Naive MT5 timestamps are interpreted as
UTC; a non-zero timezone offset is rejected. The loader preserves tick volume,
real volume, spread points, source row identity, and any additional bid/ask or
vendor columns in `source_metrics`.

Strict mode rejects invalid OHLC, duplicate bars, out-of-order rows, and gaps
not covered by an `ExplicitClosureCalendar`. Known weekend or maintenance gaps
remain visible in the quality report. An abnormal-spread limit is deliberately
caller-configured because its correct value depends on symbol digits and the
specific Exness account/feed.

```python
from ict_trading_agent import ExnessCsvLoader, Timeframe

m5 = ExnessCsvLoader(
    symbol="XAUUSD",
    timeframe=Timeframe.M5,
    abnormal_spread_threshold_points=100,
).load("XAUUSD_M5.csv")
```

Use native M1/M5/M15/H1/H4 exports rather than retroactively exposing an
unfinished aggregate candle. M4.1 does not silently resample or repair source
data.

## Replay ordering and causality

`M4ReplayEngine` begins with an empty `ClosedBarFeed`. It groups source bars by
close time, appends only that group, then runs the existing production M2 and
M3 objects. When several timeframes close simultaneously, the deterministic
order is M1, M5, M15, H1, H4, D1, W1. This lets a lower-timeframe first take be
visible to a setup-timeframe observation at the same close without exposing any
future bar.

H4/D1/W1 can contribute M2 facts. M3 only processes timeframes enabled by its
versioned policy. Reference facts such as completed PDH/PDL or session levels
must be built point-in-time and supplied through `initial_facts`; replay never
backfills them from future data.

```python
from ict_trading_agent import M4ReplayEngine

engine = M4ReplayEngine(
    symbol="XAUUSD",
    tick_size=0.01,
    initial_facts=reference_facts,
    target_candidates=targets,
    context={"data_source": "Exness", "candle_timezone": "UTC"},
)
result = engine.run([m5, m15, h1])
paths = result.export_jsonl("artifacts/m4-run-001")
```

An engine instance is single-run so append-only stores and cursors cannot leak
between experiments.

## Outputs

The export contains:

- `audit_events.jsonl`: closed bars and every emitted fact, candidate, raid
  episode/update, setup, transition, and READY payload with the raw model JSON;
- `near_misses.jsonl`: late reclaim/shift/FVG/reaction research observations
  and expired setups, including timing distance, threshold, and excess where a
  versioned threshold exists;
- `replay_steps.jsonl`: exact close-time schedule and event IDs revealed at
  each step;
- `summary.json`: basic detection and terminal-status counts;
- `data_quality.json`: source validation reports and all explained/unexplained
  gaps.

The audit payloads retain timeframe provenance, effective swing rank, raid and
shift lag, FVG penetration/lifecycle metrics, session/context fields when
supplied, and reason codes. M4.2 can therefore calculate breakdowns and
parameter sensitivity without changing the M3 concept definition.

## M4.1 boundary

M4.1 is code-complete once the ingestion/replay/audit contracts pass synthetic
causality and near-miss regressions. It is not empirical validation: real
Exness XAUUSD exports still have to be ingested and reviewed before M4.2 begins
parameter sweeps, chart samples, future MFE/MAE labels, DOL outcomes, or frozen
real-market regression cases.
