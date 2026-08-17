from __future__ import annotations

import csv
import os
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from .base import NonEmptyStr, SchemaModel
from .enums import Timeframe
from .m4 import ExnessCsvLoader, ExnessDataset
from .m4_support import M4SymbolMetadata
from .market import TIMEFRAME_DURATIONS, ExplicitClosureCalendar

MT5_TIMEFRAME_ATTRIBUTES: dict[Timeframe, str] = {
    Timeframe.M1: "TIMEFRAME_M1",
    Timeframe.M5: "TIMEFRAME_M5",
    Timeframe.M15: "TIMEFRAME_M15",
    Timeframe.H1: "TIMEFRAME_H1",
    Timeframe.H4: "TIMEFRAME_H4",
    Timeframe.D1: "TIMEFRAME_D1",
    Timeframe.W1: "TIMEFRAME_W1",
}


def load_env_file(path: str | Path, *, override: bool = False) -> tuple[str, ...]:
    """Load a simple local KEY=VALUE file without returning or logging secrets."""

    loaded: list[str] = []
    for raw_line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or (key in os.environ and not override):
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
        loaded.append(key)
    return tuple(loaded)


class MT5ConnectionConfig(SchemaModel):
    login: int = Field(gt=0)
    password: SecretStr
    server: NonEmptyStr
    terminal_path: NonEmptyStr
    symbol: NonEmptyStr
    timeout_ms: int = Field(default=60_000, ge=1_000)

    @classmethod
    def from_environment(cls) -> MT5ConnectionConfig:
        required = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"missing MT5 environment variables: {', '.join(missing)}")
        suffix = os.getenv("MT5_SYMBOL_SUFFIX", "")
        return cls(
            login=int(os.environ["MT5_LOGIN"]),
            password=os.environ["MT5_PASSWORD"],
            server=os.environ["MT5_SERVER"],
            terminal_path=os.environ["MT5_PATH"],
            symbol="XAUUSD" + suffix,
        )


class MT5HistoryRequest(SchemaModel):
    timeframe: Timeframe
    start_at: AwareDatetime
    end_at: AwareDatetime
    chunk_days: int = Field(default=14, ge=1, le=366)

    @model_validator(mode="after")
    def validate_range(self) -> MT5HistoryRequest:
        if self.end_at <= self.start_at:
            raise ValueError("MT5 history end must follow start")
        return self


class MT5HistoryClient:
    """Small injectable adapter around the optional Windows MetaTrader5 SDK."""

    version = "0.1.0"

    def __init__(
        self,
        config: MT5ConnectionConfig,
        *,
        mt5_module: ModuleType | Any | None = None,
    ) -> None:
        self.config = config
        self.mt5 = mt5_module
        self.connected = False

    def connect(self) -> None:
        if self.mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as exc:  # pragma: no cover - platform dependency
                raise RuntimeError(
                    "MetaTrader5 package is required for acquisition"
                ) from exc
            self.mt5 = mt5
        ok = self.mt5.initialize(
            path=self.config.terminal_path,
            login=self.config.login,
            password=self.config.password.get_secret_value(),
            server=self.config.server,
            timeout=self.config.timeout_ms,
        )
        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {self.mt5.last_error()}")
        if not self.mt5.symbol_select(self.config.symbol, True):
            error = self.mt5.last_error()
            self.mt5.shutdown()
            raise RuntimeError(f"MT5 symbol_select failed: {error}")
        self.connected = True

    def close(self) -> None:
        if self.mt5 is not None and self.connected:
            self.mt5.shutdown()
        self.connected = False

    def symbol_metadata(self, *, captured_at: AwareDatetime) -> M4SymbolMetadata:
        self._require_connection()
        info = self.mt5.symbol_info(self.config.symbol)
        if info is None:
            raise RuntimeError(f"MT5 symbol_info unavailable: {self.mt5.last_error()}")
        return M4SymbolMetadata.from_mt5_symbol_info(info, captured_at=captured_at)

    def fetch_dataset(
        self,
        request: MT5HistoryRequest,
        *,
        closure_calendar: ExplicitClosureCalendar,
        raw_output_path: str | Path | None = None,
        strict: bool = True,
    ) -> ExnessDataset:
        text = self.fetch_tsv(request)
        if raw_output_path is not None:
            destination = Path(raw_output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        return ExnessCsvLoader(
            symbol=self.config.symbol,
            timeframe=request.timeframe,
            strict=strict,
            closure_calendar=closure_calendar,
        ).loads(
            text,
            source_name=(
                str(raw_output_path)
                if raw_output_path is not None
                else f"MT5:{self.config.symbol}:{request.timeframe.value}"
            ),
        )

    def fetch_tsv(self, request: MT5HistoryRequest) -> str:
        self._require_connection()
        timeframe_code = getattr(self.mt5, MT5_TIMEFRAME_ATTRIBUTES[request.timeframe])
        rows: dict[int, Any] = {}
        cursor = request.start_at.astimezone(UTC)
        end_at = request.end_at.astimezone(UTC)
        while cursor < end_at:
            chunk_end = min(cursor + timedelta(days=request.chunk_days), end_at)
            rates = self.mt5.copy_rates_range(
                self.config.symbol, timeframe_code, cursor, chunk_end
            )
            if rates is None:
                raise RuntimeError(
                    f"MT5 copy_rates_range failed: {self.mt5.last_error()}"
                )
            for row in rates:
                opened_at = datetime.fromtimestamp(int(row["time"]), UTC)
                close_at = opened_at + TIMEFRAME_DURATIONS[request.timeframe]
                if request.start_at <= opened_at and close_at <= request.end_at:
                    rows[int(row["time"])] = row
            cursor = chunk_end
        if not rows:
            raise ValueError("MT5 returned no completed bars for the requested range")

        output = StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "tick_volume",
                "volume",
                "spread",
            ]
        )
        for timestamp in sorted(rows):
            row = rows[timestamp]
            writer.writerow(
                [
                    datetime.fromtimestamp(timestamp, UTC).isoformat(),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["tick_volume"],
                    row["real_volume"],
                    row["spread"],
                ]
            )
        return output.getvalue()

    def _require_connection(self) -> None:
        if not self.connected or self.mt5 is None:
            raise RuntimeError("MT5 client is not connected")
