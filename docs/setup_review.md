# Setup review chart

Create an exact-source, standalone HTML chart for a chart-review setup. The
default context is 72 M5 bars before READY and 96 M5 bars after it:

```powershell
.\.venv\Scripts\python.exe scripts\export_setup_review_html.py `
  --artifact artifacts\m42-bench-metrics3-2026-08-10_2026-08-17 `
  --setup-id setup-1ee93c61e3caaf42d5efebe4
```

Render the whole deterministic 50-setup chart-review queue plus an index:

```powershell
.\.venv\Scripts\python.exe scripts\export_setup_review_html.py `
  --artifact artifacts\m42-bench-metrics3-2026-08-10_2026-08-17 `
  --all
```

The output is `setup_reviews/<setup-id>.html` in that artifact. It uses the
embedded Exness M5 window, rather than a TradingView provider's OHLC values,
and draws the detector's liquidity/swing level, marks its actual swing candle,
marks the swing candle broken by SHIFT, then adds the raid extreme,
structure-shift level, FVG/CE, frozen invalidation, and lifecycle timestamps.

This is a review artifact, not an order ticket. Candidate/fact IDs are shown
only as audit references. They must not be copied wholesale into an LLM prompt;
the future LLM handoff needs a compact market-state summary derived from these
facts.
