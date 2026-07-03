from pathlib import Path


FRONTEND = Path("frontend/src")


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def test_backend_connection_guard_uses_lightweight_ready_probe():
    src = _read("components/BackendConnectionGuard.tsx")

    assert "queryFn: api.getReady" in src
    assert "queryFn: api.getStatus" not in src
    assert "const POLL_MS = 10000" in src
    assert "const FAILURES_BEFORE_ALERT = 3" in src
    assert "failureCount >= FAILURES_BEFORE_ALERT" in src


def test_fetch_progress_ignores_background_polling_queries():
    src = _read("components/FetchProgress.tsx")

    assert "query.meta?.background === true" in src
    assert "refetchInterval?: unknown" in src


def test_market_ticker_marks_symbol_queries_as_background():
    src = _read("components/MarketTicker.tsx")

    assert "meta: { background: true }" in src
