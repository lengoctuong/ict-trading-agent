"""Standalone, exact-source HTML chart reviews for READY_FOR_LLM setups.

This is deliberately separate from the TradingView overlay.  The review uses
the M5 candles embedded in the M4.2 artifact, so price geometry is exact to
the Exness feed that produced the detector evidence.
"""

from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SetupReview:
    setup_id: str
    symbol: str
    direction: str
    setup_timeframe: str
    ready_at: datetime
    bars: tuple[dict[str, Any], ...]
    liquidity_level: float | None
    raid_extreme: float | None
    shift_level: float | None
    fvg_low: float | None
    fvg_high: float | None
    fvg_ce: float | None
    invalidation: float | None
    liquidity_swing_at: datetime | None
    shift_swing_at: datetime | None
    raid_at: datetime | None
    shift_at: datetime | None
    fvg_at: datetime | None
    evidence_ids: tuple[str, ...]


def load_setup_review(
    chart_queue_path: Path,
    audit_events_path: Path,
    setup_id: str,
    *,
    m5_tsv_path: Path | None = None,
    bars_before: int = 72,
    bars_after: int = 96,
) -> SetupReview:
    """Load one visual review with optional wider raw-M5 context."""

    reviews = load_setup_reviews(
        chart_queue_path,
        audit_events_path,
        setup_ids=(setup_id,),
        m5_tsv_path=m5_tsv_path,
        bars_before=bars_before,
        bars_after=bars_after,
    )
    if not reviews:
        raise ValueError(f"setup {setup_id} is not in chart review queue")
    return reviews[0]


def load_setup_reviews(
    chart_queue_path: Path,
    audit_events_path: Path,
    *,
    setup_ids: tuple[str, ...] | None = None,
    m5_tsv_path: Path | None = None,
    bars_before: int = 72,
    bars_after: int = 96,
) -> tuple[SetupReview, ...]:
    """Load a batch efficiently: audit/raw sources are each parsed once."""

    wanted = set(setup_ids) if setup_ids else None
    items = [
        item
        for item in _queue_items(chart_queue_path)
        if wanted is None or item.get("setup_candidate_id") in wanted
    ]
    all_events = _events(audit_events_path)
    events_by_id = {event.get("record_id"): event for event in all_events}
    events_by_setup: dict[str, list[dict[str, Any]]] = {}
    for event in all_events:
        if setup_key := event.get("setup_candidate_id"):
            events_by_setup.setdefault(str(setup_key), []).append(event)
    raw_bars = _m5_bars(m5_tsv_path) if m5_tsv_path else ()
    return tuple(
        _review_from_item(
            item,
            events_by_setup.get(str(item["setup_candidate_id"]), []),
            events_by_id,
            _context_bars(raw_bars, _timestamp(str(item["as_of"])), bars_before, bars_after)
            if raw_bars
            else tuple(item["window_bars"]),
        )
        for item in items
    )


def _review_from_item(
    item: dict[str, Any],
    events: list[dict[str, Any]],
    events_by_id: dict[str | None, dict[str, Any]],
    bars: tuple[dict[str, Any], ...],
) -> SetupReview:
    """Materialize one review from its queue item and causal audit evidence."""

    setup_id = str(item["setup_candidate_id"])
    # Candidate facts normally predate and therefore do not carry the later
    # setup ID. The READY evidence list is the causal join, not that field.
    candidates = {
        str(record_id): event
        for record_id, event in events_by_id.items()
        if record_id is not None and event.get("kind") == "candidate"
    }
    evidence = item["evidence_payload"]["setup"]
    candidate_ids = tuple(evidence.get("evidence_candidate_ids", ()))
    linked_candidates = tuple(
        candidates[candidate_id]
        for candidate_id in candidate_ids
        if candidate_id in candidates
    )

    def candidate(category: str) -> dict[str, Any] | None:
        return next(
            (
                event
                for event in linked_candidates
                if event.get("category") == category
            ),
            None,
        )

    # Candidate order is causal. In particular, a later M15 observation of an
    # M5 raid must not overwrite the physical M5 sweep for chart geometry.
    raid = candidate("liquidity_event")
    shift = candidate("shift")
    structure = candidate("structure_break")
    fvg = candidate("fvg")
    raid_features = _features(raid)
    structure_features = _features(structure)
    fvg_features = _features(fvg)
    liquidity_swing = events_by_id.get(raid_features.get("reference_fact_id"))
    shift_swing = events_by_id.get(structure_features.get("reference_fact_id"))
    forming = next(
        (
            event
            for event in events
            if event.get("kind") == "transition"
            and event.get("category") == "forming"
        ),
        None,
    )
    return SetupReview(
        setup_id=setup_id,
        symbol=str(item["window_bars"][0]["symbol"]),
        direction=str(item["direction"]),
        setup_timeframe=str(item["timeframe"]),
        ready_at=_timestamp(str(item["as_of"])),
        bars=bars,
        liquidity_level=_number(raid_features.get("reference_price")),
        raid_extreme=_number(raid_features.get("extreme")),
        shift_level=_number(structure_features.get("reference_price")),
        fvg_low=_number(fvg_features.get("low")),
        fvg_high=_number(fvg_features.get("high")),
        fvg_ce=_number(fvg_features.get("ce")),
        invalidation=_number(
            (forming or {}).get("payload", {}).get("hard_invalidation_price")
        ),
        liquidity_swing_at=_timestamp_or_none(
            (liquidity_swing or {}).get("occurred_at")
        ),
        shift_swing_at=_timestamp_or_none((shift_swing or {}).get("occurred_at")),
        raid_at=_timestamp_or_none((raid or {}).get("available_at")),
        shift_at=_timestamp_or_none((shift or {}).get("available_at")),
        fvg_at=_timestamp_or_none(fvg_features.get("fvg_available_at")),
        evidence_ids=(*candidate_ids, *evidence.get("evidence_fact_ids", ())),
    )


def render_setup_review(review: SetupReview) -> str:
    """Render a self-contained SVG/HTML page; no external CDN is required."""

    width, height = 1280, 720
    left, top, right, bottom = 74, 52, 230, 98
    plot_width, plot_height = width - left - right, height - top - bottom
    overlays = [
        value
        for value in (
            review.liquidity_level,
            review.raid_extreme,
            review.shift_level,
            review.fvg_low,
            review.fvg_high,
            review.invalidation,
        )
        if value is not None
    ]
    prices = [
        float(bar[key]) for bar in review.bars for key in ("high", "low")
    ] + overlays
    low, high = min(prices), max(prices)
    padding = max((high - low) * 0.08, 0.1)
    low, high = low - padding, high + padding
    count = len(review.bars)

    def x(index: int) -> float:
        return left + (index + 0.5) * plot_width / count

    def y(price: float) -> float:
        return top + (high - price) * plot_height / (high - low)

    def index_at(moment: datetime | None) -> int | None:
        if moment is None:
            return None
        for index, bar in enumerate(review.bars):
            if _timestamp(str(bar["close_time"])) >= moment:
                return index
        return None

    def index_by_open(moment: datetime | None) -> int | None:
        if moment is None:
            return None
        for index, bar in enumerate(review.bars):
            if _timestamp(str(bar["open_time"])) == moment:
                return index
        return None

    grid = []
    for row in range(6):
        price = low + (high - low) * row / 5
        py = y(price)
        grid.append(
            f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_width}" y2="{py:.1f}" class="grid"/>'
            f'<text x="8" y="{py + 4:.1f}" class="axis">{price:.3f}</text>'
        )
    candle_width = max(2.0, plot_width / count * 0.62)
    candles = []
    for index, bar in enumerate(review.bars):
        px = x(index)
        opening, closing = float(bar["open"]), float(bar["close"])
        color = "#22c55e" if closing >= opening else "#ef4444"
        body_top, body_bottom = min(y(opening), y(closing)), max(y(opening), y(closing))
        candles.append(
            f'<line x1="{px:.1f}" y1="{y(float(bar["high"])):.1f}" x2="{px:.1f}" y2="{y(float(bar["low"])):.1f}" stroke="{color}"/>'
            f'<rect x="{px - candle_width / 2:.1f}" y="{body_top:.1f}" width="{candle_width:.1f}" height="{max(1.0, body_bottom - body_top):.1f}" fill="{color}"/>'
        )
    lines = []
    def level(value: float | None, label: str, color: str, dash: str = "6 4") -> None:
        if value is None:
            return
        py = y(value)
        lines.append(
            f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_width}" y2="{py:.1f}" stroke="{color}" stroke-dasharray="{dash}" stroke-width="1.5"/>'
            f'<text x="{left + plot_width + 8}" y="{py + 4:.1f}" fill="{color}" class="label">{html.escape(label)} {value:.3f}</text>'
        )
    if review.fvg_low is not None and review.fvg_high is not None:
        start = index_at(review.fvg_at) or 0
        fvg_y, fvg_height = y(review.fvg_high), y(review.fvg_low) - y(review.fvg_high)
        fvg_x = x(start) - candle_width
        lines.append(f'<rect x="{fvg_x:.1f}" y="{fvg_y:.1f}" width="{left + plot_width - fvg_x:.1f}" height="{fvg_height:.1f}" fill="#38bdf8" fill-opacity=".18" stroke="#38bdf8"/>')
        lines.append(f'<text x="{fvg_x + 5:.1f}" y="{fvg_y + 16:.1f}" fill="#7dd3fc" class="label">FVG {review.fvg_low:.3f}–{review.fvg_high:.3f}</text>')
    level(review.liquidity_level, "Liquidity / swing", "#f59e0b")
    level(review.shift_level, "Shift break", "#a78bfa")
    level(review.fvg_ce, "FVG CE", "#38bdf8")
    level(review.invalidation, "Invalidation", "#fb7185", "2 3")
    markers = []
    swing_points = []
    for marker_number, (moment, label, color) in enumerate((
        (review.raid_at, "RAID confirmed", "#f59e0b"),
        (review.shift_at, "SHIFT confirmed", "#a78bfa"),
        (review.ready_at, "READY for LLM", "#22c55e"),
    )):
        index = index_at(moment)
        if index is None:
            continue
        px = x(index)
        markers.append(f'<line x1="{px:.1f}" y1="{top}" x2="{px:.1f}" y2="{top + plot_height}" stroke="{color}" stroke-dasharray="3 4"/>')
        marker_y = top + 16 + (marker_number % 3) * 16
        markers.append(f'<text x="{px + 4:.1f}" y="{marker_y}" fill="{color}" class="label">{label}</text>')
    for moment, price, label, color in (
        (review.liquidity_swing_at, review.liquidity_level, "swing swept", "#f59e0b"),
        (review.shift_swing_at, review.shift_level, "swing broken", "#a78bfa"),
    ):
        index = index_by_open(moment)
        if index is None or price is None:
            continue
        px, py = x(index), y(price)
        swing_points.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="#0b1220" stroke="{color}" stroke-width="2"/>')
        swing_points.append(f'<text x="{px + 7:.1f}" y="{py - 7:.1f}" fill="{color}" class="label">{label}</text>')
    time_labels = []
    step = max(1, count // 8)
    for index in range(0, count, step):
        stamp = _timestamp(str(review.bars[index]["open_time"])).strftime("%H:%M")
        time_labels.append(f'<text x="{x(index):.1f}" y="{top + plot_height + 22}" text-anchor="middle" class="axis">{stamp}</text>')
    narrative = (
        f"{review.direction.upper()} | {review.setup_timeframe} setup | READY {review.ready_at.strftime('%Y-%m-%d %H:%M UTC')}<br>"
        "Yellow: swept swing liquidity. Purple: confirmed close-through / SHIFT. "
        "Blue band: FVG, dashed blue: CE. Pink: frozen invalidation."
    )
    levels = "<br>".join(
        item
        for item in (
            _level_text("Liquidity swing", review.liquidity_level, review.liquidity_swing_at),
            _level_text("Raid extreme", review.raid_extreme, review.raid_at),
            _level_text("Swing broken by SHIFT", review.shift_level, review.shift_swing_at),
            _fvg_text(review),
            _level_text("Frozen invalidation", review.invalidation, review.shift_at),
        )
        if item
    )
    ids = html.escape(", ".join(review.evidence_ids))
    return f'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>ICT review {html.escape(review.setup_id)}</title>
<style>body{{margin:0;background:#0b1220;color:#e5e7eb;font:14px system-ui,sans-serif}}main{{max-width:1280px;margin:auto;padding:22px}}h1{{font-size:18px;margin:0 0 5px}}.muted,.axis{{fill:#94a3b8;color:#94a3b8}}.grid{{stroke:#1e293b}}.label{{font-size:12px;font-weight:600}}.panel{{background:#111827;border:1px solid #243244;border-radius:8px;padding:12px;margin-top:12px;line-height:1.55}}code{{font-size:11px;word-break:break-all}}</style></head>
<body><main><h1>ICT chart review — {html.escape(review.setup_id)}</h1><div class="muted">{html.escape(review.symbol)} • exact Exness M5 candles • all times UTC</div>
<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="ICT setup chart">{''.join(grid)}{''.join(lines)}{''.join(markers)}{''.join(candles)}{''.join(swing_points)}{''.join(time_labels)}</svg>
<div class="panel"><strong>Machine narrative</strong><br>{narrative}</div>
<div class="panel"><strong>Exact machine levels</strong><br>{levels}</div>
<div class="panel"><strong>Audit references (not prompt text for LLM)</strong><br><code>{ids}</code></div>
</main></body></html>'''


def _queue_items(path: Path) -> tuple[dict[str, Any], ...]:
    with path.open(encoding="utf-8") as source:
        return tuple(json.loads(line) for line in source)


def _events(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source]


def _m5_bars(path: Path) -> tuple[dict[str, Any], ...]:
    with path.open(encoding="utf-8", newline="") as source:
        rows = csv.DictReader(source, delimiter="\t")
        return tuple(
            {
                "symbol": "source-symbol",
                "open_time": row["datetime"],
                "close_time": (
                    _timestamp(row["datetime"]) + timedelta(minutes=5)
                ).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for row in rows
        )


def _context_bars(
    bars: tuple[dict[str, Any], ...],
    as_of: datetime,
    bars_before: int,
    bars_after: int,
) -> tuple[dict[str, Any], ...]:
    if bars_before < 0 or bars_after < 0:
        raise ValueError("context bar counts cannot be negative")
    anchor = max(
        index
        for index, bar in enumerate(bars)
        if _timestamp(str(bar["close_time"])) <= as_of
    )
    start = max(0, anchor - bars_before)
    return bars[start : anchor + bars_after + 1]


def _features(event: dict[str, Any] | None) -> dict[str, Any]:
    return (event or {}).get("payload", {}).get("raw_features", {})


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _timestamp_or_none(value: str | None) -> datetime | None:
    return _timestamp(value) if value else None


def _level_text(label: str, price: float | None, moment: datetime | None) -> str:
    if price is None:
        return ""
    at = f" at {moment.strftime('%H:%M UTC')}" if moment else ""
    return f"{html.escape(label)}: <strong>{price:.3f}</strong>{at}"


def _fvg_text(review: SetupReview) -> str:
    if review.fvg_low is None or review.fvg_high is None:
        return ""
    at = f" at {review.fvg_at.strftime('%H:%M UTC')}" if review.fvg_at else ""
    ce = f"; CE {review.fvg_ce:.3f}" if review.fvg_ce is not None else ""
    return f"FVG: <strong>{review.fvg_low:.3f}–{review.fvg_high:.3f}</strong>{ce}{at}"
