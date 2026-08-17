from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from ict_trading_agent.enums import Timeframe
from ict_trading_agent.m4_support import ExnessXauCalendarPreset
from ict_trading_agent.mt5_history import (
    MT5ConnectionConfig,
    MT5HistoryClient,
    MT5HistoryRequest,
    load_env_file,
)


class FakeMT5:
    TIMEFRAME_M5 = 5

    def __init__(self) -> None:
        self.shutdown_called = False

    def initialize(self, **kwargs: object) -> bool:
        return bool(kwargs["login"] and kwargs["password"])

    def symbol_select(self, symbol: str, enabled: bool) -> bool:
        return symbol == "XAUUSDm" and enabled

    def symbol_info(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=symbol,
            digits=3,
            point=0.001,
            trade_tick_size=0.001,
        )

    def copy_rates_range(
        self, symbol: str, timeframe: int, start: datetime, end: datetime
    ) -> list[dict[str, float | int]]:
        assert symbol == "XAUUSDm"
        assert timeframe == self.TIMEFRAME_M5
        rows = []
        timestamp = int(start.timestamp())
        while timestamp < int(end.timestamp()):
            rows.append(
                {
                    "time": timestamp,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "tick_volume": 10,
                    "real_volume": 0,
                    "spread": 25,
                }
            )
            timestamp += 300
        return rows

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_local_env_loader_does_not_echo_or_override_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    env = tmp_path / ".env.local"
    env.write_text("MT5_LOGIN=123\nMT5_PASSWORD=secret\n", encoding="utf-8")
    monkeypatch.setenv("MT5_LOGIN", "999")

    loaded = load_env_file(env)

    assert loaded == ("MT5_PASSWORD",)


def test_mt5_adapter_snapshots_metadata_and_validates_completed_history() -> None:
    fake = FakeMT5()
    config = MT5ConnectionConfig(
        login=123,
        password="secret",
        server="Exness-Test",
        terminal_path="terminal64.exe",
        symbol="XAUUSDm",
    )
    start = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    end = datetime(2026, 8, 17, 9, 20, tzinfo=UTC)
    client = MT5HistoryClient(config, mt5_module=fake)
    client.connect()

    metadata = client.symbol_metadata(captured_at=start)
    dataset = client.fetch_dataset(
        MT5HistoryRequest(
            timeframe=Timeframe.M5,
            start_at=start,
            end_at=end,
            chunk_days=1,
        ),
        closure_calendar=ExnessXauCalendarPreset().build(
            start_date=start.date(), end_date=end.date()
        ),
    )
    client.close()

    assert metadata.symbol == "XAUUSDm"
    assert metadata.trade_tick_size == 0.001
    assert len(dataset.records) == 4
    assert dataset.quality.has_errors is False
    assert fake.shutdown_called is True
