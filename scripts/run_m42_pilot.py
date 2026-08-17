from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from threading import Event, Thread
from time import perf_counter
from typing import Self

from ict_trading_agent.detectors import CandleFeatureConfig
from ict_trading_agent.enums import Timeframe
from ict_trading_agent.m4 import ExnessCsvLoader, M4ReplayEngine
from ict_trading_agent.m4_support import (
    CausalReferenceBuilder,
    CausalReferencePolicy,
    ExnessXauCalendarPreset,
    M4StudyWindow,
    M4SymbolMetadata,
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


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


class ProcessMetricsSampler:
    """Sample the actual Python process, avoiding Windows venv launcher ambiguity."""

    def __init__(self, *, sample_interval_seconds: float = 0.05) -> None:
        self.sample_interval_seconds = sample_interval_seconds
        self._started_at: float | None = None
        self._stop = Event()
        self._thread: Thread | None = None
        self._peak_working_set_bytes = 0
        self._peak_private_bytes = 0
        self._samples = 0

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def start(self) -> Self:
        if self._started_at is not None:
            raise RuntimeError("process metrics sampler is already running")
        self._started_at = perf_counter()
        self._sample()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._started_at is None:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._sample()

    def _run(self) -> None:
        while not self._stop.wait(self.sample_interval_seconds):
            self._sample()

    def _sample(self) -> None:
        self._samples += 1
        if sys.platform != "win32":
            return
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessMemoryCountersEx),
            ctypes.c_ulong,
        )
        get_process_memory_info.restype = ctypes.c_int
        process = get_current_process()
        success = get_process_memory_info(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        if not success:
            raise ctypes.WinError()
        self._peak_working_set_bytes = max(
            self._peak_working_set_bytes,
            int(counters.working_set_size),
        )
        self._peak_private_bytes = max(
            self._peak_private_bytes,
            int(counters.private_usage),
        )

    def snapshot(self) -> dict[str, int | float | str | None]:
        elapsed = (
            perf_counter() - self._started_at if self._started_at is not None else None
        )
        return {
            "measurement": "in_process",
            "platform": sys.platform,
            "sample_interval_ms": round(self.sample_interval_seconds * 1_000),
            "sample_count": self._samples,
            "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
            "peak_working_set_bytes": self._peak_working_set_bytes,
            "peak_private_bytes": self._peak_private_bytes,
        }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the first M4.2 Exness XAU pilot")
    parser.add_argument("--env-file", default=".env.mt5.local")
    parser.add_argument("--output", default="artifacts/m42-pilot-2026-06-01_2026-08-16")
    parser.add_argument("--symbol", default="XAUUSDm")
    parser.add_argument(
        "--reuse-raw",
        action="store_true",
        help="Validate and replay existing raw TSV files without opening MT5",
    )
    parser.add_argument(
        "--replay-start", type=datetime.fromisoformat, default=DEFAULT_REPLAY_START
    )
    parser.add_argument(
        "--analysis-start", type=datetime.fromisoformat, default=DEFAULT_ANALYSIS_START
    )
    parser.add_argument(
        "--analysis-end", type=datetime.fromisoformat, default=DEFAULT_ANALYSIS_END
    )
    parser.add_argument(
        "--debug-steps",
        action="store_true",
        help="Retain per-close replay steps; disabled by default for research runs",
    )
    parser.add_argument(
        "--no-process-metrics",
        action="store_true",
        help="Disable in-process metrics for cProfile or other external profilers",
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
    symbol = args.symbol
    calendar_end = (
        max(analysis_end.date(), DEFAULT_ANALYSIS_END.date())
        if args.reuse_raw
        else analysis_end.date()
    )
    calendar_start = (
        min(replay_start.date(), DEFAULT_REPLAY_START.date())
        if args.reuse_raw
        else replay_start.date()
    )
    calendar = ExnessXauCalendarPreset().build(
        start_date=calendar_start,
        end_date=calendar_end,
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
    metrics_sampler = (
        None if args.no_process_metrics else ProcessMetricsSampler().start()
    )
    datasets = []
    raw = output / "raw"
    timeframes = (
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    )
    metadata_path = raw / "symbol_metadata.json"
    if args.reuse_raw:
        if not metadata_path.exists():
            raise FileNotFoundError(
                f"cached replay requires metadata snapshot: {metadata_path}"
            )
        metadata = M4SymbolMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        if metadata.symbol != symbol:
            raise ValueError("cached metadata symbol does not match --symbol")
        for timeframe in timeframes:
            source = raw / f"{symbol}_{timeframe.value}.tsv"
            print(f"loading cached {timeframe.value}: {source}", flush=True)
            dataset = ExnessCsvLoader(
                symbol=symbol,
                timeframe=timeframe,
                strict=True,
                closure_calendar=calendar,
            ).load(source)
            print(
                f"validated {timeframe.value}: bars={len(dataset.records)} "
                f"gaps={len(dataset.quality.gaps)} "
                f"unexplained={dataset.quality.unexplained_gap_count}",
                flush=True,
            )
            datasets.append(
                dataset.window(start_at=replay_start, end_at=analysis_end)
            )
    else:
        load_env_file(args.env_file)
        config = MT5ConnectionConfig.from_environment()
        if config.symbol != symbol:
            raise ValueError("MT5 environment symbol does not match --symbol")
        client = MT5HistoryClient(config)
        client.connect()
        try:
            captured_at = datetime.now(UTC)
            metadata = client.symbol_metadata(captured_at=captured_at)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(
                metadata.model_dump_json(indent=2), encoding="utf-8"
            )
            for timeframe in timeframes:
                print(f"fetching {timeframe.value}", flush=True)
                dataset = client.fetch_dataset(
                    MT5HistoryRequest(
                        timeframe=timeframe,
                        start_at=replay_start,
                        end_at=analysis_end,
                    ),
                    closure_calendar=calendar,
                    raw_output_path=raw / f"{symbol}_{timeframe.value}.tsv",
                )
                print(
                    f"validated {timeframe.value}: bars={len(dataset.records)} "
                    f"gaps={len(dataset.quality.gaps)} "
                    f"unexplained={dataset.quality.unexplained_gap_count}",
                    flush=True,
                )
                datasets.append(
                    dataset.window(start_at=replay_start, end_at=analysis_end)
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
        symbol=symbol,
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
        retain_research_facts=False,
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
        retain_steps=args.debug_steps,
    )
    replay_paths = replay.export_jsonl(output / "replay")
    all_bars = [record.bar for dataset in datasets for record in dataset.records]
    bundle = M42ResearchAnalyzer(
        tick_size=metadata.trade_tick_size,
        entry_timeframe=Timeframe.M5,
    ).analyze(replay, all_bars, generated_at=datetime.now(UTC))
    research_paths = bundle.export(output / "research")
    if metrics_sampler is not None:
        metrics_sampler.stop()
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
        "runtime_metrics": (
            metrics_sampler.snapshot()
            if metrics_sampler is not None
            else {"measurement": "disabled_for_external_profiler"}
        ),
        "replay_outputs": {key: str(value) for key, value in replay_paths.items()},
        "research_outputs": {key: str(value) for key, value in research_paths.items()},
    }
    (output / "pilot_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
