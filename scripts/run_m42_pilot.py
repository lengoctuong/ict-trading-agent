from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, time
from pathlib import Path

from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import Timeframe
from ict_trading_agent.m4 import M4ReplayEngine
from ict_trading_agent.m4_support import (
    CausalReferenceBuilder,
    CausalReferencePolicy,
    ExnessXauCalendarPreset,
    M4StudyWindow,
)
from ict_trading_agent.m42 import M42ResearchAnalyzer
from ict_trading_agent.market import MarketClosure, MarketSequenceAdjacencyPolicy
from ict_trading_agent.mt5_history import (
    MT5ConnectionConfig,
    MT5HistoryClient,
    MT5HistoryRequest,
    load_env_file,
)

DEFAULT_REPLAY_START = datetime(2026, 4, 6, tzinfo=UTC)
DEFAULT_ANALYSIS_START = datetime(2026, 6, 1, tzinfo=UTC)
DEFAULT_ANALYSIS_END = datetime(2026, 8, 17, tzinfo=UTC)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the first M4.2 Exness XAU pilot")
    parser.add_argument("--env-file", default=".env.mt5.local")
    parser.add_argument("--output", default="artifacts/m42-pilot-2026-06-01_2026-08-16")
    parser.add_argument(
        "--replay-start", type=datetime.fromisoformat, default=DEFAULT_REPLAY_START
    )
    parser.add_argument(
        "--analysis-start", type=datetime.fromisoformat, default=DEFAULT_ANALYSIS_START
    )
    parser.add_argument(
        "--analysis-end", type=datetime.fromisoformat, default=DEFAULT_ANALYSIS_END
    )
    return parser.parse_args()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _exceptional_closures() -> tuple[MarketClosure, ...]:
    return (
        MarketClosure(
            start_at=datetime(2026, 5, 25, 18, 30, tzinfo=UTC),
            end_at=datetime(2026, 5, 25, 22, 2, tzinfo=UTC),
            reason=(
                "2026 Memorial Day XAU early close; exact interval observed on "
                "Exness MT5 XAUUSDm and retained as an exceptional source interval"
            ),
        ),
        MarketClosure(
            start_at=datetime(2026, 6, 19, 17, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 21, 22, 5, tzinfo=UTC),
            reason=(
                "2026 Juneteenth XAU early close; exact interval observed on "
                "Exness MT5 XAUUSDm and retained as an exceptional source interval"
            ),
        ),
        MarketClosure(
            start_at=datetime(2026, 7, 3, 17, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 5, 22, 5, tzinfo=UTC),
            reason=(
                "2026 Independence Day XAU close/open from the Exness holiday "
                "schedule (UTC+0)"
            ),
        ),
    )


def main() -> None:
    args = _arguments()
    replay_start = _aware_utc(args.replay_start)
    analysis_start = _aware_utc(args.analysis_start)
    analysis_end = _aware_utc(args.analysis_end)
    output = Path(args.output)
    load_env_file(args.env_file)
    config = MT5ConnectionConfig.from_environment()
    calendar = ExnessXauCalendarPreset().build(
        start_date=replay_start.date(),
        end_date=analysis_end.date(),
        exceptional_closures=_exceptional_closures(),
    )
    calendar.metadata.update(
        {
            "pilot_id": "m42-pilot-2026-06-01_2026-08-16",
            "holiday_schedule_url": (
                "https://get.exness.help/hc/en-us/articles/"
                "17923046759836-Holiday-trading-hours"
            ),
            "observed_exception_policy": (
                "manually enumerated named 2026 holidays; never auto-whitelist gaps"
            ),
        }
    )
    client = MT5HistoryClient(config)
    client.connect()
    try:
        captured_at = datetime.now(UTC)
        metadata = client.symbol_metadata(captured_at=captured_at)
        datasets = []
        raw = output / "raw"
        for timeframe in (
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        ):
            print(f"fetching {timeframe.value}", flush=True)
            dataset = client.fetch_dataset(
                MT5HistoryRequest(
                    timeframe=timeframe,
                    start_at=replay_start,
                    end_at=analysis_end,
                ),
                closure_calendar=calendar,
                raw_output_path=raw / f"{config.symbol}_{timeframe.value}.tsv",
            )
            datasets.append(dataset)
            print(
                f"validated {timeframe.value}: bars={len(dataset.records)} "
                f"gaps={len(dataset.quality.gaps)} "
                f"unexplained={dataset.quality.unexplained_gap_count}",
                flush=True,
            )
    finally:
        client.close()

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    reference_builder = CausalReferenceBuilder(
        CausalReferencePolicy(
            previous_day_from_native_d1=True,
            true_day_open_source_timeframe=Timeframe.M5,
            true_day_open_timezone="America/New_York",
            true_day_open_local=time(0, 0),
        )
    )
    engine = M4ReplayEngine(
        symbol=config.symbol,
        symbol_metadata=metadata,
        git_commit_sha=revision,
        candle_config=CandleFeatureConfig(baseline_period=20),
        adjacency_policy=MarketSequenceAdjacencyPolicy(calendar),
        reference_builder=reference_builder,
        context={
            "data_source": "Exness MT5",
            "candle_timezone": "UTC",
            "session_policy": "pending_review_no_session_labels",
        },
    )
    window = M4StudyWindow(
        replay_start=replay_start,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    print("running causal M4 replay", flush=True)
    next_progress = 2_500

    def report_progress(processed: int, total: int, as_of: datetime) -> None:
        nonlocal next_progress
        if processed < next_progress and processed != total:
            return
        print(
            f"replay progress: {processed}/{total} "
            f"({processed / total:.1%}) as_of={as_of.isoformat()}",
            flush=True,
        )
        while next_progress <= processed:
            next_progress += 2_500

    replay = engine.run(
        datasets,
        study_window=window,
        progress_callback=report_progress,
    )
    replay_paths = replay.export_jsonl(output / "replay")
    all_bars = [record.bar for dataset in datasets for record in dataset.records]
    bundle = M42ResearchAnalyzer(
        tick_size=metadata.trade_tick_size,
        entry_timeframe=Timeframe.M5,
    ).analyze(replay, all_bars, generated_at=datetime.now(UTC))
    research_paths = bundle.export(output / "research")
    summary = {
        "run_id": replay.run_id,
        "symbol": replay.symbol,
        "study_window": window.model_dump(mode="json"),
        "symbol_metadata": metadata.model_dump(mode="json"),
        "summary": replay.summary.model_dump(mode="json"),
        "data_quality": [
            {
                "source": item.source_name,
                "rows": item.rows_accepted,
                "gaps": len(item.gaps),
                "unexplained_gaps": item.unexplained_gap_count,
            }
            for item in replay.data_quality
        ],
        "m42_report": bundle.report.model_dump(mode="json"),
        "replay_outputs": {key: str(value) for key, value in replay_paths.items()},
        "research_outputs": {key: str(value) for key, value in research_paths.items()},
    }
    (output / "pilot_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
