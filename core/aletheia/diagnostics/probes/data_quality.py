"""Data-quality probes for doctor reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = [
    "_probe_coinalyze",
    "_probe_collector_health",
    "_probe_data_quality",
    "_probe_macro_signals",
    "_probe_market_data",
    "_probe_market_data_venue_health",
    "_probe_ohlcv_coverage",
    "_probe_orderbook",
    "_probe_perp_structure",
    "_probe_prediction_markets",
    "_probe_serenity_pipeline",
    "_probe_universe_market_coverage",
    "_probe_volume_ranking",
]


def _probe_market_data(report: Report) -> None:
    try:
        from aletheia.storage.repositories import list_market_snapshots
        rows = list_market_snapshots(limit=1)
    except Exception as e:
        report.add("data.market", "info", f"could not read snapshots: {e}")
        return
    if not rows:
        report.add("data.market", "info", "no market snapshots yet (feed never started)")
        return
    age = _age_minutes(rows[0].get("snapshot_at"))
    detail = f"newest snapshot {_fmt_age(age)}"
    if age is not None and age > 30:
        report.add("data.market", "warn", detail + " — feed appears stale",
                   "check gateway is running and scheduler not paused")
    else:
        report.add("data.market", "ok", detail)


def _probe_market_data_venue_health(report: Report) -> None:
    """Report in-process venue cooldowns instead of hiding an active outage."""
    from aletheia.data.venue_health import get_venue_health_snapshot

    cooling = [item for item in get_venue_health_snapshot() if item["cooling_down"]]
    if not cooling:
        return
    detail = ", ".join(
        f"{item['venue']} ({item['cooldown_remaining_sec']:.0f}s remaining)"
        for item in cooling
    )
    report.add(
        "data.market_venue_health",
        "warn",
        f"market-data venue cooldown active: {detail}",
        "wait for the cooldown or resolve the venue's rate-limit/IP-ban response",
    )


def _probe_collector_health(report: Report) -> None:
    """Surface whether scheduled domains ran, timed out, or lost the shared budget."""
    try:
        from aletheia.data.collector_health import get_collector_health_report

        state = get_collector_health_report()
    except Exception as e:
        report.add("data.collectors", "info", f"collector status unavailable: {e}")
        return

    domains = state["domains"]
    status = state["status"]
    if status == "initializing":
        report.add(
            "data.collectors",
            "info",
            "collector status initializing; no domain has completed a scheduled tick yet",
        )
        return

    problem_rows = [
        row for row in domains if row.get("degraded") or row.get("last_status") in {
            "failed", "timed_out"
        }
    ]
    attention_rows = [
        row for row in domains if row.get("degraded") or row.get("last_status") in {
            "failed", "timed_out", "partial", "empty"
        }
    ]
    inactive = [
        row["domain"] for row in domains
        if row.get("last_status") in {"disabled", "not_configured"}
    ]
    detail = (
        f"{state['recorded_count']}/{state['domain_count']} domains reporting; "
        f"{state['completed_count']} completed on latest due tick"
    )
    if inactive:
        detail += f"; inactive={','.join(inactive)}"

    if status == "degraded":
        def _fmt_problem(row: dict) -> str:
            s = f"{row['domain']}:{row['last_status']}"
            extras: list[str] = []
            if row.get("last_error_code"):
                extras.append(row["last_error_code"])
            cons = row.get("consecutive_problems") or 0
            if cons > 1:
                extras.append(f"{cons}x")
            if row.get("overdue"):
                extras.append("overdue")
            if row.get("starved"):
                extras.append("starved")
            if extras:
                s += f"[{','.join(extras)}]"
            return s

        problems = ", ".join(_fmt_problem(row) for row in problem_rows)
        report.add(
            "data.collectors",
            "warn",
            f"{detail}; degraded={problems}",
            "inspect GET /api/market/collector-health and gateway errors; keep the 40s budget bounded",
        )
    elif status == "attention":
        attention = ", ".join(
            f"{row['domain']}:{row['last_status']}" for row in attention_rows
        )
        report.add(
            "data.collectors",
            "info",
            f"{detail}; attention={attention}",
        )
    else:
        report.add("data.collectors", "ok", detail)


def _probe_universe_market_coverage(report: Report) -> None:
    """Verify each enabled instrument market reaches the recent feature layer."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.sqlite import get_db

        cfg = load_config()
        enabled = cfg.exchange.enabled_markets or {}
        expected: list[str] = []
        if enabled.get("spot"):
            expected.append("spot")
        if enabled.get("usdt_futures"):
            expected.append("perp")
        if not expected:
            report.add(
                "data.universe_market_coverage", "info",
                "no spot/perp markets enabled for Universe feature collection",
            )
            return

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        rows = db.execute(
            "SELECT ur.market_type, COUNT(*) AS n "
            "FROM universe_feature_snapshots ufs "
            "JOIN universe_registry ur ON ur.id = ufs.asset_id "
            "WHERE datetime(ufs.ts) >= datetime(?) "
            "GROUP BY ur.market_type",
            (cutoff,),
        ).fetchall()
        feature_counts = {str(row["market_type"]): int(row["n"]) for row in rows}

        active_rows = db.execute(
            "SELECT um.market_type, COUNT(*) AS n FROM universe_members um "
            "WHERE um.snapshot_id = ("
            "  SELECT id FROM universe_snapshots WHERE status = 'active' "
            "  ORDER BY datetime(created_at) DESC LIMIT 1"
            ") GROUP BY um.market_type"
        ).fetchall()
        member_counts = {str(row["market_type"]): int(row["n"]) for row in active_rows}
    except Exception as e:
        report.add("data.universe_market_coverage", "info", f"probe error: {e}")
        return

    detail = (
        "recent features "
        f"spot={feature_counts.get('spot', 0)} perp={feature_counts.get('perp', 0)}; "
        "active members "
        f"spot={member_counts.get('spot', 0)} perp={member_counts.get('perp', 0)}"
    )
    if sum(feature_counts.values()) == 0:
        report.add(
            "data.universe_market_coverage", "info", detail,
            "wait for Universe refresh before judging per-market coverage",
        )
        return

    missing = [market for market in expected if feature_counts.get(market, 0) == 0]
    if missing:
        report.add(
            "data.universe_market_coverage", "warn", detail,
            "enabled market type(s) missing from recent features: " + ", ".join(missing),
        )
        return
    report.add("data.universe_market_coverage", "ok", detail)


def _probe_orderbook(report: Report) -> None:
    """Expose whether L2 orderbook snapshots are actually reaching storage."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT exchange, symbol, market_type, observed_at, spread_bps, total_depth_10bps_usd "
            "FROM orderbook_snapshots "
            "ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        report.add("data.orderbook", "info", f"could not read orderbook snapshots: {e}")
        return

    if not row:
        report.add(
            "data.orderbook",
            "warn",
            "no orderbook rows yet",
            "check microstructure collector logs and exchange orderbook support",
        )
        return

    age = _age_minutes(row["observed_at"])
    sev = "warn" if age is not None and age > 15 else "ok"
    spread = row["spread_bps"]
    depth = row["total_depth_10bps_usd"]
    detail = (
        f"newest orderbook {row['exchange']}:{row['symbol']}:{row['market_type']} "
        f"({_fmt_age(age)}), "
        f"spread={spread:.2f}bps, depth10=${depth:,.0f}"
    )
    report.add(
        "data.orderbook",
        sev,
        detail,
        "check data_collectors microstructure job if stale > 15m"
        if sev == "warn" else None,
    )


def _probe_perp_structure(report: Report) -> None:
    """Expose whether observation-only perp structure rows are fresh."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT symbol, source, venue, as_of_time, observed_at, data_quality "
            "FROM perp_structure_snapshots "
            "ORDER BY as_of_time DESC, observed_at DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        if "no such table: perp_structure_snapshots" in str(e):
            report.add(
                "data.perp_structure",
                "info",
                "not configured: no trusted automatic perp structure source "
                "(observation-only)",
                "run migrations, then use the owner aggregate import only when "
                "a trustworthy export is available",
            )
            return
        report.add("data.perp_structure", "info", f"could not read perp structure: {e}")
        return

    if not row:
        try:
            from extensions.data.collectors.vergex_structure import structure_source_status
        except ModuleNotFoundError as exc:
            if exc.name not in {"extensions", "extensions.data", "extensions.data.collectors",
                                "extensions.data.collectors.vergex_structure", "extensions.data.position_x"}:
                raise
            report.add("data.perp_structure", "info", "unavailable: optional structure producer not installed")
            return
        source_status = structure_source_status()
        report.add(
            "data.perp_structure",
            "info",
            "not configured: no trusted automatic perp structure source; "
            f"mode={source_status['mode']} (observation-only)",
            "import an owner-authorized aggregate with `aletheia data "
            "ingest-perp-structure PATH` when a trustworthy export is available",
        )
        return

    age = _age_minutes(row["as_of_time"] or row["observed_at"])
    sev = "warn" if age is None or age > 60 else "ok"
    detail = (
        f"newest perp structure {row['symbol']} "
        f"{row['source']}/{row['venue']} quality={row['data_quality']} ({_fmt_age(age)})"
    )
    report.add(
        "data.perp_structure",
        sev,
        detail,
        "perp structure stale > 60m; check the offline importer/feed job"
        if sev == "warn" else None,
    )


def _probe_macro_signals(report: Report) -> None:
    """Round 2 macro signal freshness probes — LSR / F&G / CoinGecko top.

    Per plan §9.4: doctor must expose the age of the last LSR fill, the latest
    F&G value + age, and the CoinGecko top table row count + newest observed_at.
    All three signals enter the LLM enriched_context, so a stale signal silently
    poisons the prompt — surfacing the age is the operator's only signal.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()

        # --- LSR: most recent row where long_short_ratio IS NOT NULL ---
        try:
            row = db.execute(
                "SELECT symbol, long_short_ratio, observed_at "
                "FROM open_interest_snapshots "
                "WHERE long_short_ratio IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                age = _age_minutes(row["observed_at"])
                detail = (
                    f"newest LSR {row['symbol']}={row['long_short_ratio']:.2f} "
                    f"({_fmt_age(age)})"
                )
                sev = "warn" if age is not None and age > 30 else "ok"
                report.add("data.lsr", sev, detail,
                           "check Binance fapi reachability if stale > 30m"
                           if sev == "warn" else None)
            else:
                report.add("data.lsr", "info",
                           "no LSR rows yet — enable in Settings → Market Data Sources")
        except Exception as e:
            report.add("data.lsr", "info", f"could not read LSR: {e}")

        # --- F&G: latest macro snapshot's fng_value + age ---
        try:
            row = db.execute(
                "SELECT fng_value, fng_classification, observed_at "
                "FROM macro_snapshots "
                "WHERE fng_value IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                age = _age_minutes(row["observed_at"])
                cls = f" ({row['fng_classification']})" if row["fng_classification"] else ""
                # F&G updates ~daily, so 6h staleness is normal; warn at 30h.
                sev = "warn" if age is not None and age > 30 * 60 else "ok"
                report.add("data.fear_greed", sev,
                           f"F&G {row['fng_value']}{cls} ({_fmt_age(age)})",
                           "Alt.me may be down if stale > 30h"
                           if sev == "warn" else None)
            else:
                report.add("data.fear_greed", "info",
                           "no F&G rows yet — enable in Settings → Macro & Sentiment Signals")
        except Exception as e:
            report.add("data.fear_greed", "info", f"could not read F&G: {e}")

        # --- CoinGecko top: count + newest observed_at ---
        try:
            cnt_row = db.execute(
                "SELECT COUNT(*) AS n, MAX(observed_at) AS newest "
                "FROM coingecko_top_snapshots"
            ).fetchone()
            n = cnt_row["n"] if cnt_row else 0
            newest = cnt_row["newest"] if cnt_row else None
            if n == 0:
                report.add("data.coingecko_top", "info",
                           "no CoinGecko top rows — enable in Settings → Macro & Sentiment Signals")
            else:
                age = _age_minutes(newest)
                # Phase B is 10min cadence; >30m stale = collector probably failing.
                sev = "warn" if age is not None and age > 30 else "ok"
                report.add("data.coingecko_top", sev,
                           f"{n:,} rows total; newest batch {_fmt_age(age)}",
                           "check CoinGecko reachability (429? rate limit?)"
                           if sev == "warn" else None)
        except Exception as e:
            report.add("data.coingecko_top", "info", f"could not read CoinGecko top: {e}")

        # --- Hyperliquid: newest cross-venue OI row (exchange='hyperliquid') ---
        try:
            row = db.execute(
                "SELECT symbol, observed_at FROM open_interest_snapshots "
                "WHERE exchange = 'hyperliquid' "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                age = _age_minutes(row["observed_at"])
                sev = "warn" if age is not None and age > 30 else "ok"
                report.add("data.hyperliquid", sev,
                           f"newest HL cross-venue OI {row['symbol']} ({_fmt_age(age)})",
                           "check api.hyperliquid.xyz reachability if stale > 30m"
                           if sev == "warn" else None)
            else:
                report.add("data.hyperliquid", "info",
                           "no Hyperliquid rows yet — enable in Settings → Macro & Sentiment Signals")
        except Exception as e:
            report.add("data.hyperliquid", "info", f"could not read Hyperliquid: {e}")

        # --- DefiLlama on-chain: row count + newest observed_at ---
        try:
            cnt_row = db.execute(
                "SELECT COUNT(*) AS n, MAX(observed_at) AS newest FROM onchain_snapshots"
            ).fetchone()
            n = cnt_row["n"] if cnt_row else 0
            newest = cnt_row["newest"] if cnt_row else None
            if n == 0:
                report.add("data.onchain", "info",
                           "no on-chain rows yet — enable DefiLlama in Settings → Macro & Sentiment Signals")
            else:
                age = _age_minutes(newest)
                # Phase B ~10min cadence; >45m stale = collector probably failing.
                sev = "warn" if age is not None and age > 45 else "ok"
                report.add("data.onchain", sev,
                           f"{n:,} on-chain rows; newest {_fmt_age(age)}",
                           "check DefiLlama (api.llama.fi) reachability if stale > 45m"
                           if sev == "warn" else None)
        except Exception as e:
            report.add("data.onchain", "info", f"could not read on-chain: {e}")

    except Exception as e:
        report.add("data.macro_signals", "info", f"macro signals probe failed: {e}")


def _probe_coinalyze(report: Report) -> None:
    """Expose whether the optional Coinalyze OI/liquidation feed is actually live."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        enabled = bool(getattr(cfg.market_data, "enable_coinalyze", False))
        if not enabled:
            report.add("data.coinalyze", "info", "Coinalyze disabled in settings")
            report.add("data.liquidations", "info", "Coinalyze liquidation feed disabled in settings")
            return

        from extensions.data.collectors.coinalyze import is_configured
        if not is_configured():
            report.add(
                "data.coinalyze",
                "info",
                "Coinalyze optional feed is enabled but not configured",
                "add a Coinalyze API key only if this optional cross-venue feed is needed",
            )
            report.add(
                "data.liquidations",
                "info",
                "Coinalyze liquidation feed unavailable while Coinalyze is not configured",
            )
            return

        from aletheia.storage.sqlite import get_db
        db = get_db()
        oi_row = db.execute(
            "SELECT symbol, observed_at FROM open_interest_snapshots "
            "WHERE exchange = 'coinalyze' "
            "ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        liq_row = db.execute(
            "SELECT symbol, observed_at FROM liquidation_events "
            "WHERE exchange = 'coinalyze' "
            "ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()

        if oi_row:
            age = _age_minutes(oi_row["observed_at"])
            sev = "warn" if age is not None and age > 60 else "ok"
            report.add(
                "data.coinalyze",
                sev,
                f"newest Coinalyze OI {oi_row['symbol']} ({_fmt_age(age)})",
                "check Coinalyze key/quota or scheduler if stale > 60m"
                if sev == "warn" else None,
            )
        else:
            report.add(
                "data.coinalyze",
                "warn",
                "Coinalyze configured but no OI rows yet",
                "check Coinalyze key/quota and the data_collectors job",
            )

        if liq_row:
            age = _age_minutes(liq_row["observed_at"])
            report.add(
                "data.liquidations",
                "info",
                f"newest Coinalyze liquidation {liq_row['symbol']} ({_fmt_age(age)})",
            )
        else:
            report.add(
                "data.liquidations",
                "info",
                "no liquidation events observed yet",
                "event-driven feed is idle until a real liquidation occurs",
            )
    except Exception as e:
        report.add("data.coinalyze", "info", f"could not read Coinalyze state: {e}")


def _probe_prediction_markets(report: Report) -> None:
    """Expose whether prediction-market data is enabled and fresh enough to use."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.sqlite import get_db

        cfg = load_config()
        enabled = bool(getattr(cfg.market_data, "prediction_enabled", False))
        db = get_db()
        market_row = db.execute(
            "SELECT question, updated_at FROM prediction_markets "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        claim_row = db.execute(
            "SELECT claim_text, created_at FROM prediction_market_claims "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        report.add("data.prediction_markets", "info", f"could not read prediction market state: {e}")
        return

    market_age = _age_minutes(market_row["updated_at"]) if market_row else None
    claim_age = _age_minutes(claim_row["created_at"]) if claim_row else None
    newest_age = min(age for age in (market_age, claim_age) if age is not None) if (
        market_age is not None or claim_age is not None
    ) else None
    newest_detail = _fmt_age(newest_age)

    if not enabled:
        report.add(
            "data.prediction_markets",
            "info",
            f"prediction markets disabled in settings; newest stored signal {newest_detail}",
        )
        return

    if not market_row and not claim_row:
        report.add(
            "data.prediction_markets",
            "warn",
            "prediction markets enabled but no rows yet",
            "wait for prediction_observer or trigger a manual prediction refresh",
        )
        return

    sev = "warn" if newest_age is None or newest_age > 90 else "ok"
    detail = f"prediction markets enabled; newest stored signal {newest_detail}"
    if market_row:
        detail += f"; latest market={market_row['question'][:80]}"
    if claim_row:
        detail += f"; latest claim={claim_row['claim_text'][:80]}"
    report.add(
        "data.prediction_markets",
        sev,
        detail,
        "prediction_observer runs every 30m; stale data is ignored by agent context"
        if sev == "warn" else None,
    )


def _probe_data_quality(report: Report) -> None:
    """Check data provenance: are prices coming from fallback sources?"""
    try:
        from aletheia.storage.repositories import list_market_snapshots
        rows = list_market_snapshots(limit=50)
    except Exception:
        return
    if not rows:
        return

    recent = [r for r in rows if _age_minutes(r.get("snapshot_at") or "") is not None
              and _age_minutes(r.get("snapshot_at") or "") < 30]  # type: ignore[operator]
    if not recent:
        return

    fallback_providers = {"coingecko", "binance_public"}
    fallback_count = sum(1 for r in recent if (r.get("provider") or "").lower() in fallback_providers)
    total = len(recent)
    ratio = fallback_count / total if total else 0

    if ratio > 0.2:
        report.add(
            "data.quality", "warn",
            f"{fallback_count}/{total} recent snapshots from fallback sources "
            f"({ratio:.0%}) — CCXT primary may be degraded",
            "check network/exchange API status; consider disabling cross_source_check temporarily",
        )
    else:
        report.add("data.quality", "ok",
                   f"data sources healthy ({fallback_count}/{total} fallback)")

    # Price cache freshness
    try:
        from aletheia.data.price_feed import cache_stats
        stats = cache_stats()
        if stats.get("oldest_sec") and stats["oldest_sec"] > 80:
            report.add("data.cache_freshness", "info",
                       f"price cache oldest entry {stats['oldest_sec']:.0f}s (threshold 90s)")
    except Exception:
        logger.warning("doctor: price-cache probe unavailable", exc_info=True)


def _probe_ohlcv_coverage(report: Report) -> None:
    """Check OHLCV historical data coverage for active universe members."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.repositories import get_active_universe_members
        from aletheia.storage.sqlite import get_db

        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0]
        if total == 0:
            report.add("data.ohlcv", "warn",
                       "market_ohlcv is empty — OHLCV-dependent features cannot run",
                       "run: uv run aletheia data backfill")
            return

        cfg = load_config()
        min_bars = cfg.historical_data.min_bars_for_backtest
        members = get_active_universe_members()
        if not members:
            report.add("data.ohlcv", "info",
                       f"market_ohlcv: {total} total bars (no active members to verify coverage)")
            return

        gaps: list[str] = []
        gap_identities: list[tuple[str, str, str]] = []
        for m in members:
            sym = m.get("symbol", "")
            if not sym:
                continue
            from aletheia.storage.repositories import get_universe_asset
            asset = get_universe_asset(m.get("asset_id")) or {}
            venue = asset.get("venue", "")
            market_type = m.get("market_type") or asset.get("market_type", "")
            bars = db.execute(
                "SELECT COUNT(*) FROM market_ohlcv "
                "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe='1h'",
                (venue, sym, market_type),
            ).fetchone()[0]
            if bars < min_bars:
                gaps.append(f"{venue}:{sym}:{market_type}({bars})")
                gap_identities.append((venue, sym, market_type))

        symbols_covered = db.execute(
            "SELECT COUNT(DISTINCT exchange || ':' || symbol || ':' || market_type) "
            "FROM market_ohlcv WHERE timeframe='1h'"
        ).fetchone()[0]

        if gaps:
            catchup_counts: dict[str, int] = {}
            persistent_failures = 0
            for venue, sym, market_type in gap_identities:
                state = db.execute(
                    "SELECT status,consecutive_failures FROM ohlcv_backfill_state "
                    "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe='1h'",
                    (venue, sym, market_type),
                ).fetchone()
                status = state["status"] if state else "awaiting"
                catchup_counts[status] = catchup_counts.get(status, 0) + 1
                if state and int(state["consecutive_failures"] or 0) >= 3:
                    persistent_failures += 1
            auto_enabled = bool(
                cfg.historical_data.enabled
                and cfg.historical_data.active_universe_backfill_enabled
            )
            detail = (
                f"{len(gaps)}/{len(members)} active members below {min_bars} bars: "
                + ", ".join(gaps[:5])
                + ("…" if len(gaps) > 5 else "")
                + f"; catch-up={catchup_counts}"
            )
            if persistent_failures:
                detail += f", persistent_failures={persistent_failures}"
            action = (
                "automatic active-Universe catch-up is enabled; check gateway logs if "
                "persistent_failures remains non-zero"
                if auto_enabled
                else "enable Active Universe catch-up in Settings or run: "
                "uv run aletheia data backfill"
            )
            report.add(
                "data.ohlcv", "warn",
                detail,
                action,
            )
        else:
            report.add(
                "data.ohlcv", "ok",
                f"OHLCV: {total} total bars, {symbols_covered} symbols, "
                f"all {len(members)} active members ≥{min_bars} bars",
            )
    except Exception:
        logger.warning("doctor: ohlcv-coverage probe unavailable", exc_info=True)


def _probe_serenity_pipeline(report: Report) -> None:
    """Serenity freshness is based on successful completion, not mere attempts."""
    try:
        from aletheia.config.loader import load_config as _lcfg
        if not getattr(_lcfg().serenity, "enabled", True):
            report.add("data.serenity_pipeline", "info", "Serenity research pipeline disabled in settings")
            return
    except Exception:
        logger.warning("doctor: serenity settings load failed; the pipeline probe continues", exc_info=True)
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        successful = db.execute(
            "SELECT status, COALESCE(finished_at, created_at) newest "
            "FROM serenity_research_runs "
            "WHERE status IN ('completed', 'completed_empty') "
            "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 1"
        ).fetchone()
        attempted = db.execute(
            "SELECT status, error_type, COALESCE(finished_at, created_at) attempted_at "
            "FROM serenity_research_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        active = db.execute(
            "SELECT COUNT(*) FROM serenity_opportunities WHERE status = 'active'"
        ).fetchone()[0]
    except Exception as e:
        report.add("data.serenity_pipeline", "info", f"probe error: {e}")
        return
    if not successful:
        attempt_detail = ""
        if attempted:
            attempt_detail = (
                f"; latest attempt={attempted['status']}"
                + (f" ({attempted['error_type']})" if attempted["error_type"] else "")
            )
        report.add("data.serenity_pipeline", "warn",
                   f"no successful Serenity research run{attempt_detail}",
                   "check dreaming_daily and serenity_startup_catchup scheduler jobs")
        return
    age = _age_minutes(successful["newest"])
    detail = f"last successful run {_fmt_age(age)}, {active} active opportunities"
    if attempted and attempted["status"] not in ("completed", "completed_empty"):
        detail += f"; latest attempt={attempted['status']}"
        if attempted["error_type"]:
            detail += f" ({attempted['error_type']})"
    if age is not None and age < -5:
        report.add(
            "data.serenity_pipeline",
            "warn",
            f"Serenity latest success timestamp is in the future: {detail}",
            "inspect runtime clock and persisted Serenity run timestamps",
        )
    elif age is not None and age > 1560:  # 26h — serenity runs once per 24h; 12h fired daily false-positives
        report.add("data.serenity_pipeline", "warn",
                   f"Serenity research stale: {detail}",
                   "dreaming_daily missed 02:00 CST and startup catch-up has not completed")
    else:
        report.add("data.serenity_pipeline", "ok", detail)


def _probe_volume_ranking(report: Report) -> None:
    """Volume ranking snapshot freshness for universe ordering."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT MAX(observed_at) newest FROM volume_ranking_snapshots"
        ).fetchone()
    except Exception as e:
        report.add("data.volume_ranking", "info", f"probe error: {e}")
        return
    if not row["newest"]:
        report.add("data.volume_ranking", "warn",
                   "no volume ranking snapshots recorded",
                   "universe ordering falls back to static list; check volume_scanner scheduler job")
        return
    age = _age_minutes(row["newest"])
    detail = f"volume ranking snapshot {_fmt_age(age)}"
    if age is not None and age > 60:
        report.add("data.volume_ranking", "warn",
                   f"volume ranking stale: {detail}",
                   "volume_scanner job may have failed — universe ordering falls back to static list")
    else:
        report.add("data.volume_ranking", "ok", detail)


# Define exports before importing doctor so either module can be imported first.
from aletheia.diagnostics.doctor import (
    Check,
    Report,
    _age_minutes,
    _fmt_age,
    logger,
)
