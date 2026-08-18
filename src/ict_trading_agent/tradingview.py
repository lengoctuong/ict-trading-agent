from __future__ import annotations

import csv
import json
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TradingViewReadyMarker:
    """A causally available setup marker rendered by a static Pine overlay."""

    available_at: datetime
    price: float
    direction: str
    timeframe: str
    setup_candidate_id: str


def load_ready_markers(
    audit_events_path: Path,
    m5_tsv_path: Path,
) -> tuple[TradingViewReadyMarker, ...]:
    """Load every distinct analysis READY payload and its preceding M5 close."""

    close_by_open = _m5_closes(m5_tsv_path)
    markers: list[TradingViewReadyMarker] = []
    seen_setup_ids: set[str] = set()
    with audit_events_path.open(encoding="utf-8") as source:
        for line in source:
            event = json.loads(line)
            if (
                event.get("kind") != "ready_payload"
                or event.get("included_in_analysis") is not True
            ):
                continue
            setup_id = str(event["setup_candidate_id"])
            if setup_id in seen_setup_ids:
                continue
            seen_setup_ids.add(setup_id)
            available_at = _parse_timestamp(str(event["available_at"]))
            price = _close_at_or_before(close_by_open, available_at)
            markers.append(
                TradingViewReadyMarker(
                    available_at=available_at,
                    price=price,
                    direction=str(event["direction"]),
                    timeframe=str(event["timeframe"]),
                    setup_candidate_id=setup_id,
                )
            )
    return _ordered_markers(markers)


def load_chart_review_markers(
    chart_queue_path: Path,
) -> tuple[TradingViewReadyMarker, ...]:
    """Load chart-review samples using their embedded M5 window close."""

    markers: list[TradingViewReadyMarker] = []
    seen_setup_ids: set[str] = set()
    with chart_queue_path.open(encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            setup_id = str(item["setup_candidate_id"])
            if setup_id in seen_setup_ids:
                continue
            seen_setup_ids.add(setup_id)
            available_at = _parse_timestamp(str(item["as_of"]))
            window_bars = item.get("window_bars", [])
            closes = [
                (bar["close_time"], float(bar["close"]))
                for bar in window_bars
                if _parse_timestamp(str(bar["close_time"])) <= available_at
            ]
            if not closes:
                raise ValueError(f"chart review item has no bar before {available_at}")
            price = max(closes, key=lambda item: item[0])[1]
            markers.append(
                TradingViewReadyMarker(
                    available_at=available_at,
                    price=price,
                    direction=str(item["direction"]),
                    timeframe=str(item["timeframe"]),
                    setup_candidate_id=setup_id,
                )
            )
    return _ordered_markers(markers)


def render_ready_overlay(
    markers: Iterable[TradingViewReadyMarker],
    *,
    title: str,
    source_description: str,
) -> str:
    """Return a Pine v6 overlay that draws static READY markers by bar time."""

    ordered = _ordered_markers(markers)
    if not ordered:
        raise ValueError("at least one READY marker is required")
    marker_lines = "\n".join(
        f"// {item.available_at.isoformat()} {item.direction} "
        f"{item.timeframe} {item.setup_candidate_id}"
        for item in ordered
    )
    return f'''//@version=6
indicator("{title}", overlay = true, max_labels_count = 500)

// Generated from {source_description}. These labels are research/LLM-review
// candidates, not entries, fills, or PnL. Use an M5 chart and a feed close to
// the Exness source; different providers can have different OHLC values.
//
// Marker time is when the setup became READY_FOR_LLM. Marker price is the
// completed Exness M5 close available at that time.
{marker_lines}

var int[] ready_times = array.from({_pine_values((item.available_at.timestamp() * 1000 for item in ordered), integer=True)})
var float[] ready_prices = array.from({_pine_values((item.price for item in ordered), integer=False)})
var int[] ready_directions = array.from({_pine_values((1 if item.direction == "bullish" else -1 for item in ordered), integer=True)})
var string[] ready_timeframes = array.from({_pine_strings(item.timeframe for item in ordered)})
var string[] ready_ids = array.from({_pine_strings(item.setup_candidate_id for item in ordered)})
var bool rendered = false

if barstate.islast and not rendered
    for index = 0 to array.size(ready_times) - 1
        direction = array.get(ready_directions, index)
        is_bullish = direction == 1
        marker_text = "READY " + (is_bullish ? "LONG" : "SHORT") + "\\n" + array.get(ready_timeframes, index) + "\\n" + array.get(ready_ids, index)
        label.new(
             x = array.get(ready_times, index),
             y = array.get(ready_prices, index),
             text = marker_text,
             xloc = xloc.bar_time,
             yloc = yloc.price,
             style = is_bullish ? label.style_label_up : label.style_label_down,
             color = is_bullish ? color.new(color.lime, 0) : color.new(color.red, 0),
             textcolor = color.white,
             size = size.tiny)
    rendered := true
'''


def _m5_closes(source_path: Path) -> list[tuple[datetime, float]]:
    with source_path.open(encoding="utf-8", newline="") as source:
        rows = csv.DictReader(source, delimiter="\t")
        return [
            (_parse_timestamp(row["datetime"]), float(row["close"])) for row in rows
        ]


def _close_at_or_before(
    close_by_open: list[tuple[datetime, float]],
    available_at: datetime,
) -> float:
    opens = [item[0] for item in close_by_open]
    # A row is an M5 *open* timestamp. At READY time, only bars that opened
    # strictly before it have completed; choosing an equal timestamp would
    # leak the close of the still-forming M5 bar.
    index = bisect_left(opens, available_at) - 1
    if index < 0:
        raise ValueError(f"no M5 close available at {available_at}")
    return close_by_open[index][1]


def _ordered_markers(
    markers: Iterable[TradingViewReadyMarker],
) -> tuple[TradingViewReadyMarker, ...]:
    return tuple(
        sorted(
            markers,
            key=lambda item: (item.available_at, item.setup_candidate_id),
        )
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _pine_values(values: Iterable[float], *, integer: bool) -> str:
    if integer:
        return ", ".join(str(round(value)) for value in values)
    return ", ".join(f"{value:.6f}" for value in values)


def _pine_strings(values: Iterable[str]) -> str:
    return ", ".join(json.dumps(value) for value in values)
