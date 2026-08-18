from __future__ import annotations

import json
from pathlib import Path

from ict_trading_agent.tradingview import (
    load_chart_review_markers,
    load_ready_markers,
    render_ready_overlay,
)


def test_ready_overlay_uses_causal_m5_close_and_unique_setups(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit_events.jsonl"
    audit_path.write_text(
        "\n".join(
            json.dumps(
                {
                    "kind": "ready_payload",
                    "included_in_analysis": True,
                    "setup_candidate_id": "setup-bull",
                    "available_at": "2026-08-10T10:05:00Z",
                    "direction": "bullish",
                    "timeframe": "M15",
                }
            )
            for _ in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    m5_path = tmp_path / "XAUUSDm_M5.tsv"
    m5_path.write_text(
        "datetime\topen\thigh\tlow\tclose\n"
        "2026-08-10T09:55:00+00:00\t100\t101\t99\t100.5\n"
        "2026-08-10T10:00:00+00:00\t100.5\t102\t100\t101.5\n"
        "2026-08-10T10:05:00+00:00\t101.5\t103\t101\t102.5\n",
        encoding="utf-8",
    )

    markers = load_ready_markers(audit_path, m5_path)

    assert len(markers) == 1
    assert markers[0].price == 101.5
    pine = render_ready_overlay(
        markers,
        title="Test overlay",
        source_description="test artifact",
    )
    assert "//@version=6" in pine
    assert "ready_times" in pine
    assert "setup-bull" in pine


def test_chart_review_overlay_uses_embedded_close_at_ready_time(tmp_path: Path) -> None:
    queue_path = tmp_path / "chart_review_queue.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "setup_candidate_id": "setup-bear",
                "as_of": "2026-08-10T10:05:00Z",
                "direction": "bearish",
                "timeframe": "H1",
                "window_bars": [
                    {"close_time": "2026-08-10T10:00:00Z", "close": 100.0},
                    {"close_time": "2026-08-10T10:05:00Z", "close": 99.5},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    markers = load_chart_review_markers(queue_path)

    assert len(markers) == 1
    assert markers[0].price == 99.5
