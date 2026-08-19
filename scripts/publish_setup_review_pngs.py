"""Render public, planner-readable PNGs from generated setup-review HTML."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render setup-review HTML files to committed PNGs and gallery"
    )
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--output-directory", type=Path, default=Path("docs/setup_reviews")
    )
    parser.add_argument("--chrome", type=Path, default=_default_chrome())
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Render at most this many missing images; useful for bounded runs.",
    )
    return parser.parse_args()


def _default_chrome() -> Path:
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    return next((path for path in candidates if path.exists()), candidates[0])


def main() -> None:
    args = arguments()
    source = args.artifact / "setup_reviews"
    reviews = sorted(source.glob("setup-*.html"))
    if not reviews:
        raise ValueError(f"no setup HTML files found in {source}")
    if not args.chrome.exists():
        raise FileNotFoundError(f"Chrome/Edge executable does not exist: {args.chrome}")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    missing = [
        review
        for review in reviews
        if not (args.output_directory / f"{review.stem}.png").exists()
    ]
    existing_count = len(reviews) - len(missing)
    if args.limit is not None:
        missing = missing[: args.limit]
    for index, review in enumerate(missing, start=1):
        output = args.output_directory / f"{review.stem}.png"
        subprocess.run(
            [
                str(args.chrome),
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                "--window-size=1400,900",
                f"--screenshot={output.resolve()}",
                review.resolve().as_uri(),
            ],
            check=True,
            timeout=30,
        )
        print(f"[{index}/{len(missing)}] wrote {output}")
    if missing:
        print(
            f"rendered {len(missing)} image(s); "
            f"{existing_count + len(missing)} total ready"
        )
    (args.output_directory / "README.md").write_text(
        _gallery_markdown([review.stem for review in reviews]), encoding="utf-8"
    )


def _gallery_markdown(names: list[str]) -> str:
    rows = "\n".join(
        f"- [{name}]({name}.png)" for name in names
    )
    return (
        "# Public setup-review images\n\n"
        "Static PNGs rendered from exact Exness M5 review HTML. Times are UTC; "
        "these are research/LLM-review candidates, not entries or PnL.\n\n"
        f"{rows}\n"
    )


if __name__ == "__main__":
    main()
