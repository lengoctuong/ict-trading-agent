# TradingView READY overlay

This exporter renders static Pine Script v6 overlays from causal
`READY_FOR_LLM` artifacts. It is for visual review only: a marker is neither an
LLM decision nor an MT5 order, fill, or PnL result.

Generate both overlays from a completed M4 artifact:

```powershell
.\.venv\Scripts\python.exe scripts\export_tradingview_ready_overlay.py `
  --artifact artifacts\m42-bench-metrics3-2026-08-10_2026-08-17
```

The command writes:

- `tradingview/ready_chart_review.pine`: the 50 deterministic review samples;
- `tradingview/ready_all.pine`: every distinct analysis `READY_FOR_LLM` payload
  (190 for the current week artifact).

Open the desired `.pine` file, paste it into TradingView's Pine Editor, save,
then select **Add to chart**. Review on an M5 XAU chart. The source is Exness
`XAUUSDm`; a TradingView provider with a different candle feed can differ in
price or OHLC, so timestamps/provenance remain the authoritative replay record.

The overlay is deliberately static. Pine does not read the local JSONL replay
artifact, so regenerate and paste the script after each new replay.
