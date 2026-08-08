"""Repeatable micro-benchmark for the reference and incremental TA engines."""

from __future__ import annotations

import json
import math
from time import perf_counter

from aletheia.trading.technical_analysis import BatchTechnicalAnalyzer, IncrementalTechnicalAnalyzer, TechnicalBar


def _bars(count: int) -> list[TechnicalBar]:
    rows = []
    previous = 100.0
    for index in range(count):
        close = previous + math.sin(index / 11.0) * 0.2 + 0.01
        rows.append(TechnicalBar(
            ts=1_700_000_000_000 + index * 60_000,
            open=previous,
            high=max(previous, close) + 0.1,
            low=min(previous, close) - 0.1,
            close=close,
            volume=1_000 + index % 100,
        ))
        previous = close
    return rows


def main() -> None:
    seed = _bars(256)
    updates = _bars(10_000)[256:]
    batch = BatchTechnicalAnalyzer()
    started = perf_counter()
    for bar in updates:
        batch.compute(
            [*seed[1:], bar], exchange="benchmark", symbol="BTC/USDT",
            market_type="perp", timeframe="1m",
        )
        seed = [*seed[1:], bar]
    batch_seconds = perf_counter() - started

    incremental = IncrementalTechnicalAnalyzer(
        exchange="benchmark", symbol="BTC/USDT", market_type="perp",
        timeframe="1m", max_history=256,
    )
    incremental.extend(_bars(256))
    started = perf_counter()
    for bar in updates:
        incremental.update(bar)
    incremental_seconds = perf_counter() - started
    count = len(updates)
    print(json.dumps({
        "updates": count,
        "batch_seconds": round(batch_seconds, 6),
        "incremental_seconds": round(incremental_seconds, 6),
        "incremental_us_per_bar": round(incremental_seconds / count * 1_000_000, 3),
        "speedup": round(batch_seconds / incremental_seconds, 2),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
