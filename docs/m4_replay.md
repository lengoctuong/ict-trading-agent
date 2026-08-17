# M4.1.1 Exness ingestion and point-in-time replay

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

`ExnessXauCalendarPreset` generates the official regular XAU daily/weekend
breaks in UTC and changes them on the US DST boundary. Holiday, maintenance,
and exceptional changes are never inferred from a holiday name: pass their
exact published intervals as `MarketClosure` objects. Any gap outside the
resulting explicit intervals remains a strict error.

```python
from datetime import date
from ict_trading_agent import ExnessCsvLoader, ExnessXauCalendarPreset, Timeframe

calendar = ExnessXauCalendarPreset().build(
    start_date=date(2026, 1, 1),
    end_date=date(2026, 12, 31),
    exceptional_closures=verified_exness_exceptions,
)
m5 = ExnessCsvLoader(
    symbol="XAUUSD",
    timeframe=Timeframe.M5,
    closure_calendar=calendar,
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
versioned policy. `CausalReferenceBuilder` emits PDH/PDL only when a native
Exness D1 candle closes. It can also emit completed session ranges and TDO from
explicit, reviewed session/TDO policies; developing session extremes are not
published as completed levels.

```python
from ict_trading_agent import M4ReplayEngine, M4StudyWindow, M4SymbolMetadata

engine = M4ReplayEngine(
    symbol="XAUUSD",
    symbol_metadata=M4SymbolMetadata.from_mt5_symbol_info(
        mt5.symbol_info("XAUUSD"), captured_at=metadata_time
    ),
    git_commit_sha=git_sha,
    initial_facts=reference_facts,
    target_candidates=targets,
    context={"data_source": "Exness", "candle_timezone": "UTC"},
)
result = engine.run(
    [m5, m15, h1],
    study_window=M4StudyWindow(
        replay_start=warmup_start,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    ),
)
paths = result.export_jsonl("artifacts/m4-run-001")
```

An engine instance is single-run so append-only stores and cursors cannot leak
between experiments.

Warmup bars run through the same production M2/M3 path. Their audit events are
retained with `study_phase="warmup"`, but setups originating before
`analysis_start` and their later transitions are excluded from the main
summary. Pilot runs should supply roughly 40--60 completed D1 bars before the
analysis boundary as an initialization choice, not an ICT rule.

`SessionContextProvider` converts every event timestamp to
`America/New_York` with IANA DST rules and annotates it from an explicitly
configured `SessionSchedule`. No built-in Asia/London/NY windows are guessed,
and session classification never filters setups.

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
- `manifest.json`: raw-data SHA-256 hashes, code revision, M2/M3/M4 versions,
  MT5 digits/point/trade tick size, detector configs, study window, calendar,
  reference/context policy, and target policy.

The audit payloads retain timeframe provenance, effective swing rank, raid and
shift lag, FVG penetration/lifecycle metrics, session/context fields when
supplied, and reason codes. M4.2 can therefore calculate breakdowns and
parameter sensitivity without changing the M3 concept definition.

The `run_id` is the stable hash of that complete manifest. A config, calendar,
source file, metadata, policy, or code-revision change therefore creates a new
experiment identity.

## M4.1.1 boundary

M4.1.1 is code-complete once the ingestion/replay/audit contracts pass synthetic
causality, warmup, manifest, calendar, reference, context, and near-miss
regressions. It is not empirical validation: real
Exness XAUUSD exports still have to be ingested and reviewed before M4.2 begins
parameter sweeps, chart samples, future MFE/MAE labels, DOL outcomes, or frozen
real-market regression cases.
