from __future__ import annotations

import argparse
from pathlib import Path

from ict_trading_agent.tradingview import (
    load_chart_review_markers,
    load_ready_markers,
    render_ready_overlay,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate TradingView Pine overlays for READY_FOR_LLM review markers"
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        required=True,
        help="M4 pilot artifact directory containing replay/, research/, and raw/",
    )
    parser.add_argument("--symbol", default="XAUUSDm")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=None,
        help="Defaults to <artifact>/tradingview",
    )
    return parser.parse_args()


def main() -> None:
    args = arguments()
    artifact = args.artifact
    output = args.output_directory or artifact / "tradingview"
    output.mkdir(parents=True, exist_ok=True)
    all_markers = load_ready_markers(
        artifact / "replay" / "audit_events.jsonl",
        artifact / "raw" / f"{args.symbol}_M5.tsv",
    )
    review_markers = load_chart_review_markers(
        artifact / "research" / "chart_review_queue.jsonl"
    )
    all_path = output / "ready_all.pine"
    review_path = output / "ready_chart_review.pine"
    all_path.write_text(
        render_ready_overlay(
            all_markers,
            title="ICT READY_FOR_LLM — all setups",
            source_description="M4 audit READY payloads",
        ),
        encoding="utf-8",
    )
    review_path.write_text(
        render_ready_overlay(
            review_markers,
            title="ICT READY_FOR_LLM — chart review",
            source_description="M4 deterministic chart-review queue",
        ),
        encoding="utf-8",
    )
    print(f"wrote {all_path} ({len(all_markers)} markers)")
    print(f"wrote {review_path} ({len(review_markers)} markers)")


if __name__ == "__main__":
    main()
