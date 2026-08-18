from __future__ import annotations

import argparse
from pathlib import Path

from ict_trading_agent.setup_review import load_setup_review, render_setup_review


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render one exact-source ICT setup review as standalone HTML"
    )
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--setup-id", required=True)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    output = args.output or args.artifact / "setup_reviews" / f"{args.setup_id}.html"
    review = load_setup_review(
        args.artifact / "research" / "chart_review_queue.jsonl",
        args.artifact / "replay" / "audit_events.jsonl",
        args.setup_id,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_setup_review(review), encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
