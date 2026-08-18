from __future__ import annotations

import json
from pathlib import Path

from ict_trading_agent.setup_review import load_setup_review, render_setup_review


def test_setup_review_renders_ict_geometry(tmp_path: Path) -> None:
    setup_id = "setup-example"
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "setup_candidate_id": setup_id, "direction": "bullish", "timeframe": "M15",
        "as_of": "2026-08-10T05:50:00+00:00",
        "evidence_payload": {"setup": {"evidence_candidate_ids": ["raid", "shift", "break", "fvg"], "evidence_fact_ids": ["fact-a"]}},
        "window_bars": [
            {"symbol":"XAUUSDm", "open_time":"2026-08-10T05:30:00+00:00", "close_time":"2026-08-10T05:35:00+00:00", "open":100, "high":104, "low":98, "close":103},
            {"symbol":"XAUUSDm", "open_time":"2026-08-10T05:35:00+00:00", "close_time":"2026-08-10T05:40:00+00:00", "open":103, "high":106, "low":102, "close":105},
        ],
    }) + "\n", encoding="utf-8")
    events = tmp_path / "events.jsonl"
    values = [
        {"kind":"candidate", "record_id":"raid", "category":"liquidity_event", "available_at":"2026-08-10T05:35:00+00:00", "payload":{"raw_features":{"reference_price":99,"extreme":98}}},
        {"kind":"candidate", "record_id":"shift", "category":"shift", "available_at":"2026-08-10T05:40:00+00:00", "payload":{}},
        {"kind":"candidate", "record_id":"break", "category":"structure_break", "payload":{"raw_features":{"reference_price":104}}},
        {"kind":"candidate", "record_id":"fvg", "category":"fvg", "payload":{"raw_features":{"low":101,"high":102,"ce":101.5}}},
        {"kind":"transition", "category":"forming", "setup_candidate_id":setup_id, "payload":{"hard_invalidation_price":98}},
    ]
    events.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")

    review = load_setup_review(queue, events, setup_id)
    page = render_setup_review(review)

    assert review.liquidity_level == 99
    assert review.shift_level == 104
    assert review.fvg_ce == 101.5
    assert "Liquidity / swing" in page
    assert "SHIFT confirmed" in page
    assert "FVG CE" in page
    assert "Invalidation" in page
    assert "not prompt text for LLM" in page
