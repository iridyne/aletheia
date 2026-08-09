from pathlib import Path


FRONTEND = Path("frontend/src")


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def test_backend_connection_guard_uses_lightweight_ready_probe():
    src = _read("components/BackendConnectionGuard.tsx")

    assert "queryFn: api.getReady" in src
    assert "queryFn: api.getStatus" not in src
    assert "const POLL_MS = 10000" in src
    assert "refetchIntervalInBackground: true" not in src
    assert "const FAILURES_BEFORE_ALERT = 3" in src
    assert "failureCount >= FAILURES_BEFORE_ALERT" in src


def test_fetch_progress_ignores_background_polling_queries():
    src = _read("components/FetchProgress.tsx")

    assert "query.meta?.background === true" in src
    assert "refetchInterval?: unknown" in src


def test_market_ticker_marks_symbol_queries_as_background():
    src = _read("components/MarketTicker.tsx")

    assert "meta: { background: true }" in src


def test_realtime_updates_pause_while_tab_is_hidden():
    src = _read("lib/useRealtimeUpdates.ts")

    assert "document.visibilityState === 'hidden'" in src
    assert "document.addEventListener('visibilitychange', handleVisibilityChange)" in src
    assert "document.removeEventListener('visibilitychange', handleVisibilityChange)" in src


def test_realtime_updates_expose_degraded_state_and_bounded_diagnostics():
    src = _read("lib/useRealtimeUpdates.ts")

    assert "export interface RealtimeStatus" in src
    assert "state: 'polling_only'" in src
    assert "DIAGNOSTIC_COUNT_MAX = 999" in src
    assert "malformedFrameCount: boundedAdd" in src
    assert "unknownCategoryCount: boundedAdd" in src


def test_chart_lifecycle_tracks_market_identity_and_container_size():
    price = _read("charts/PriceChart.tsx")
    pnl = _read("charts/PnLChart.tsx")
    account = _read("charts/AccountEquityChart.tsx")
    theme = _read("charts/theme.ts")

    assert "}, [symbol, timeframe, marketType])" in price
    assert "markerClickRef.current = onMarkerClick" in price
    assert "chart.unsubscribeClick(handleClick)" in price
    assert "resizeObserver.disconnect()" in price
    assert "resizeObserver.disconnect()" in pnl
    assert "resizeObserver.observe(containerRef.current)" in account
    assert "window.addEventListener('resize'" not in account
    assert "chartTheme.accountEquityAdjusted" in account
    assert "accountEquityAdjusted" in theme


def test_routes_recover_inside_shell_and_redirect_legacy_paths():
    src = _read("App.tsx")

    assert "const Overview = lazy(() => import('./pages/Overview'))" in src
    assert '<Suspense fallback=' in src
    assert '<RouteErrorBoundary key={location.pathname}' in src
    assert '<Route path="/agent-runs" element={<Navigate to="/agent-activity" replace />} />' in src
    assert '<Route path="/positions" element={<Navigate to="/trades" replace />} />' in src
    assert '<Route path="*" element={<NotFound />} />' in src


def test_shared_page_header_and_table_expose_accessible_semantics():
    header = _read("components/PageHeader.tsx")
    table = _read("components/DataTable.tsx")

    assert 'aria-describedby={role ? roleId : undefined}' in header
    assert 'className="page-role">{role}' in header
    assert 'aria-label="Filter table rows"' in table
    assert "aria-sort={canSort ?" in table
    assert 'className="dt-sort-button"' in table


def test_source_install_stops_packaged_update_poll_after_mode_detection():
    src = _read("components/PackagedUpdateBanner.tsx")

    assert "packagedRuntime = false" in src
    assert "packagedRuntime !== false" in src
    assert "activeRef.current ? 1000 : 30000" in src
