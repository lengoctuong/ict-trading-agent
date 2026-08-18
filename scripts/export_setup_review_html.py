from __future__ import annotations

import argparse
import html
from pathlib import Path

from ict_trading_agent.setup_review import load_setup_reviews, render_setup_review


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render exact-source ICT setup reviews as standalone HTML"
    )
    parser.add_argument("--artifact", type=Path, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--setup-id")
    choice.add_argument("--all", action="store_true", help="render all chart-review setups")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--output-directory", type=Path, default=None)
    parser.add_argument("--symbol", default="XAUUSDm")
    parser.add_argument("--bars-before", type=int, default=72)
    parser.add_argument("--bars-after", type=int, default=96)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    output_directory = args.output_directory or args.artifact / "setup_reviews"
    reviews = load_setup_reviews(
        args.artifact / "research" / "chart_review_queue.jsonl",
        args.artifact / "replay" / "audit_events.jsonl",
        setup_ids=(args.setup_id,) if args.setup_id else None,
        m5_tsv_path=args.artifact / "raw" / f"{args.symbol}_M5.tsv",
        bars_before=args.bars_before,
        bars_after=args.bars_after,
    )
    if args.setup_id:
        output = args.output or output_directory / f"{args.setup_id}.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_setup_review(reviews[0]), encoding="utf-8")
        print(f"wrote {output}")
        return
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for review in reviews:
        filename = f"{review.setup_id}.html"
        (output_directory / filename).write_text(
            render_setup_review(review), encoding="utf-8"
        )
        rows.append(
            f'<li><a href="{html.escape(filename)}">{html.escape(review.setup_id)}</a> '
            f'— {review.direction} {review.setup_timeframe}, READY '
            f'{review.ready_at.isoformat()}</li>'
        )
    index = "<!doctype html><meta charset=\"utf-8\"><title>ICT setup reviews</title>"
    index += "<h1>ICT setup reviews</h1><p>Exact Exness M5 context, all timestamps UTC.</p><ul>"
    index += "".join(rows) + "</ul>"
    (output_directory / "index.html").write_text(index, encoding="utf-8")
    print(f"wrote {len(reviews)} reviews and {output_directory / 'index.html'}")


if __name__ == "__main__":
    main()
