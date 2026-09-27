"""Test-only repository helpers moved out of production storage code."""

from aletheia.storage.repositories import (
    AgentRun,
    ExperimentStateReadError,
    LEGACY_DERIVATIVES_WHERE,
    Opportunity,
    Sequence,
    StrategyRecord,
    UniverseAsset,
    UniversePolicy,
    _AUTO_EXPERIMENT_FLOOR,
    _OPEN_TRADE_RETRY_SQL,
    _OPEN_TRADE_UNRANKED_SQL,
    _active_observation_claim_refs,
    _assert_twin_outcomes_match,
    _benchmark_payload,
    _canonical_lab_authority,
    _effective_cutoff,
    _forward_outcome_insert_payload,
    _insert,
    _project_candidate_admission,
    _protected_universe_outcome_ids,
    _resolve_experiment_floor,
    _retention_metrics,
    _retention_metrics_lock,
    _select_all,
    _select_one,
    _step_timestamp,
    _universe_twin_shadow_capture_due_from_latest,
    _update,
    datetime,
    decode_strategy_parameters,
    execute_write,
    get_db,
    insert_filter_candidate_outcomes_batch,
    insert_opportunity,
    iso_instant_us,
    json,
    register_instant_us,
    sqlite3,
    timedelta,
    timezone,
    transaction,
)

def retention_metrics_snapshot() -> dict[str, dict[str, float | int]]:
    with _retention_metrics_lock:
        return {table: dict(values) for table, values in _retention_metrics.items()}

def insert_agent_run(run: AgentRun) -> None:
    """Insert historical/test evidence; active Runtime admission uses claim_agent_run."""

    _insert("agent_runs", run.model_dump())

def rebind_agent_run_pi_decision_artifacts(pending_id: str, final_id: str) -> int:
    """Atomically replace transient PI carrier links with their final decision."""
    if not pending_id or not final_id or pending_id == final_id:
        return 0
    execute_write(
        "INSERT OR IGNORE INTO agent_run_artifacts "
        "(run_id, artifact_type, artifact_id, created_at, reference_version) "
        "SELECT run_id, artifact_type, ?, created_at, reference_version "
        "FROM agent_run_artifacts WHERE artifact_type='pi_decision' AND artifact_id=?",
        (final_id, pending_id),
    )
    return execute_write(
        "DELETE FROM agent_run_artifacts WHERE artifact_type='pi_decision' AND artifact_id=?",
        (pending_id,),
    ).rowcount

def list_open_trade_opportunities(limit: int = 50) -> list[dict]:
    """Return one bounded open trade workset, newest arrivals last.

    Legacy rows with a NULL/blank asset class predate the discriminator and are
    crypto opportunities by construction. Prediction observations remain
    research context and never enter this trade workset. Half of each normal
    page is reserved for never-evaluated rows and half for the oldest retryable
    rows; either side may consume unused capacity from the other.

    The retryable half only fills for rows carrying ``last_ranked_at``, and no
    code writes that column any more: the ranker that advanced the cursor was
    retired with the signal emitter. On a fresh database this returns the
    oldest-first unranked stream and nothing else, so the two-stream split no
    longer delivers the anti-starvation property it was built for. Restoring it
    needs a cursor producer, not a change here.
    """
    page_size = max(0, int(limit))
    if page_size == 0:
        return []
    db = get_db()
    unranked_quota = (page_size + 1) // 2
    retry_quota = page_size // 2

    def fetch(sql: str, count: int, *, offset: int = 0) -> list[dict]:
        if count <= 0:
            return []
        return [dict(row) for row in db.execute(sql, (int(count), int(offset))).fetchall()]

    unranked = fetch(_OPEN_TRADE_UNRANKED_SQL, unranked_quota)
    retryable = fetch(_OPEN_TRADE_RETRY_SQL, retry_quota)
    remaining = page_size - len(unranked) - len(retryable)
    if remaining > 0 and len(unranked) < unranked_quota:
        retryable.extend(
            fetch(
                _OPEN_TRADE_RETRY_SQL,
                remaining,
                offset=len(retryable),
            )
        )
        remaining = page_size - len(unranked) - len(retryable)
    if remaining > 0 and len(retryable) < retry_quota:
        unranked.extend(
            fetch(
                _OPEN_TRADE_UNRANKED_SQL,
                remaining,
                offset=len(unranked),
            )
        )
        remaining = page_size - len(unranked) - len(retryable)
    # When both initial quotas were short, consume any final capacity without
    # duplicating the rows already returned from either indexed stream.
    if remaining > 0:
        retryable.extend(
            fetch(
                _OPEN_TRADE_RETRY_SQL,
                remaining,
                offset=len(retryable),
            )
        )
        remaining = page_size - len(unranked) - len(retryable)
    if remaining > 0:
        unranked.extend(
            fetch(
                _OPEN_TRADE_UNRANKED_SQL,
                remaining,
                offset=len(unranked),
            )
        )
    return unranked + retryable

def update_opportunity(oid: str, **kwargs) -> None:
    _update("opportunities", oid, kwargs)

def update_open_opportunity(oid: str, **kwargs) -> bool:
    """CAS non-lifecycle enrichment onto a still-open Opportunity only."""
    if not kwargs:
        return False
    if "status" in kwargs:
        raise ValueError("Opportunity lifecycle status requires a transition helper")
    sets = ", ".join(f"{key} = ?" for key in kwargs)
    cursor = execute_write(
        f"UPDATE opportunities SET {sets} WHERE id = ? AND status = 'open'",
        [*kwargs.values(), oid],
    )
    return cursor.rowcount == 1

def transition_open_opportunity_to_queued(oid: str) -> bool:
    """Claim one still-open Opportunity for queue insertion without state regression."""
    cur = execute_write(
        "UPDATE opportunities SET status='queued' WHERE id=? AND status='open'",
        (oid,),
    )
    return cur.rowcount == 1

def transition_opportunity_to_reviewed(oid: str) -> bool:
    """Advance a produced/evaluated Opportunity to its terminal reviewed state."""
    cur = execute_write(
        "UPDATE opportunities SET status='reviewed' WHERE id=? AND status IN ('open', 'queued')",
        (oid,),
    )
    return cur.rowcount == 1

def find_open_opportunity_by_symbol_snapshot(symbol: str, snapshot_id: str, market: str | None = None) -> dict | None:
    """Find an existing open opportunity for the same symbol in the same universe snapshot.

    When ``market`` is given, also require ``opportunities.market = ?`` so a
    same-symbol opp from a different market (e.g., spot vs perp) does not
    annotate / block the other.  A missing/None market keeps the prior
    behavior (any open opp for the symbol+snapshot is a hit), preserving
    backward compatibility for callers that predate the market dimension.
    """
    db = get_db()
    sql = "SELECT * FROM opportunities WHERE symbol = ? AND universe_snapshot_id = ? AND status = 'open'"
    params: list = [symbol, snapshot_id]
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None

def find_opportunity_by_symbol_snapshot(
    symbol: str,
    snapshot_id: str,
    market: str | None = None,
) -> dict | None:
    """Find the first opportunity ever created for one snapshot candidate.

    Unlike :func:`find_open_opportunity_by_symbol_snapshot`, this lifecycle
    identity lookup includes terminal rows.  A delayed producer must not create
    a second opportunity merely because the first one has already been reviewed.
    The earliest row is authoritative for legacy databases that already contain
    duplicates.
    """
    db = get_db()
    sql = "SELECT * FROM opportunities WHERE symbol = ? AND universe_snapshot_id = ?"
    params: list = [symbol, snapshot_id]
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " ORDER BY created_at ASC, rowid ASC LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None

def insert_opportunity_once_by_snapshot(
    opportunity: Opportunity,
) -> tuple[dict, bool]:
    """Atomically resolve one lifecycle owner for a snapshot instrument.

    The producer-side pre-check remains a useful fast path, but only an IMMEDIATE
    writer transaction can make the final check+insert indivisible across observer
    and universe worker threads. Historical duplicates remain readable; their
    earliest row keeps ownership without destructive cleanup.
    """
    snapshot_id = str(opportunity.universe_snapshot_id or "").strip()
    symbol = str(opportunity.symbol or "").strip()
    market = str(opportunity.market or "").strip()
    if not snapshot_id or not symbol or not market:
        raise ValueError("snapshot Opportunity identity requires symbol, market, snapshot")

    with transaction(immediate=True):
        existing = find_opportunity_by_symbol_snapshot(symbol, snapshot_id, market=market)
        if existing is not None:
            return existing, False
        insert_opportunity(opportunity)
        persisted = _select_one("opportunities", opportunity.id)
        if persisted is None:
            raise RuntimeError("snapshot Opportunity insert did not persist")
        return persisted, True

def latest_pi_decision(symbol: str, max_age_minutes: int = 30) -> dict | None:
    """Get the most recent PI decision for a symbol within max_age_minutes."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    db = get_db()
    row = db.execute(
        "SELECT * FROM pi_decisions WHERE symbol = ? AND created_at >= ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (symbol, cutoff),
    ).fetchone()
    return dict(row) if row else None

def list_pi_decisions(limit: int = 20) -> list[dict]:
    return _select_all("pi_decisions", order="created_at DESC, rowid DESC", limit=limit)

def list_capital_events(limit: int = 200) -> list[dict]:
    return _select_all("capital_events", order="detected_at DESC, id DESC", limit=limit)

def list_positions_for_path_observation_bucket(*, bucket_open: str, bucket_end: str) -> list[dict]:
    """Return open Positions plus Positions closed after the target bucket."""

    db = get_db()
    register_instant_us(db)
    rows = db.execute(
        "SELECT * FROM positions "
        "WHERE aletheia_instant_us(opened_at) <= ? "
        "AND (status='open' OR ("
        "status='closed' AND aletheia_instant_us(closed_at) >= ?)) "
        "ORDER BY rowid DESC",
        (iso_instant_us(bucket_open), iso_instant_us(bucket_end)),
    ).fetchall()
    return [dict(row) for row in rows]

def insert_strategy(s: StrategyRecord) -> None:
    data = s.model_dump()
    decode_strategy_parameters(data.get("parameters"))
    _insert("strategy_records", data)

def update_strategy(sid: str, **kwargs) -> None:
    if "parameters" in kwargs:
        decode_strategy_parameters(kwargs["parameters"])
    _update("strategy_records", sid, kwargs)

def list_outcome_hook_jobs(
    *,
    position_id: str | None = None,
    hook_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []
    if position_id is not None:
        conditions.append("position_id = ?")
        params.append(position_id)
    if hook_type is not None:
        conditions.append("hook_type = ?")
        params.append(hook_type)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    return _select_all(
        "outcome_hook_jobs",
        where=" AND ".join(conditions) if conditions else None,
        params=tuple(params),
        order="created_at ASC, id ASC",
        limit=limit,
    )

def finish_serenity_research_run(run_id: str, status: str, **kwargs) -> bool:
    """Commit a worker outcome only while that worker still owns running state."""
    if status not in {"completed", "completed_empty", "failed", "rate_limited"}:
        raise ValueError(f"invalid Serenity terminal status: {status}")
    allowed = {
        "stage",
        "finished_at",
        "updated_at",
        "artifact_counts",
        "error_type",
        "error_message",
        "error_stage",
        "next_retry_at",
    }
    unknown = set(kwargs) - allowed
    if unknown:
        raise ValueError(f"invalid Serenity terminal fields: {sorted(unknown)}")
    values = {"status": status, **kwargs}
    sets = ", ".join(f"{key} = ?" for key in values)
    cursor = execute_write(
        f"UPDATE serenity_research_runs SET {sets} WHERE id = ? AND status = 'running'",
        [*values.values(), run_id],
    )
    return cursor.rowcount == 1

def get_serenity_claim(claim_id: str) -> dict | None:
    return _select_one("serenity_claims", claim_id)

def insert_serenity_claims_bulk(claims: list) -> None:
    if not claims:
        return
    with transaction():
        for c in claims:
            data = c.model_dump()
            data["price_relevant"] = int(data.get("price_relevant", False))
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            execute_write(f"INSERT INTO serenity_claims ({cols}) VALUES ({placeholders})", list(data.values()))

def insert_serenity_theme_claim(theme_id: str, claim_id: str, relevance_score: float | None = None) -> None:
    with transaction(savepoint=True):
        execute_write(
            "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
            (theme_id, claim_id, relevance_score),
        )

def insert_tool_call(
    run_id: str,
    tool_name: str,
    tool_class: str,
    permitted: bool,
    input_data: str | None = None,
    output_data: str | None = None,
    inference_call_id: str | None = None,
    duration_ms: int | None = None,
) -> None:
    from aletheia.storage.models import new_id

    _insert(
        "agent_tool_calls",
        {
            "id": new_id(),
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_class": tool_class,
            "input_data": input_data,
            "output_data": output_data,
            "permitted": 1 if permitted else 0,
            "inference_call_id": inference_call_id,
            "duration_ms": duration_ms,
            "created_at": _step_timestamp(),
        },
    )

def insert_run_step(
    run_id: str, step_number: int, action: str, input_data: str | None = None, output_data: str | None = None
) -> None:
    """Record one reasoning/action step of an agent run (ordered by insertion time).

    `step_number` is preserved on the row for compatibility, but callers that read
    via `list_run_steps` see a step number reassigned by merged execution order
    (see `list_run_steps`).
    """
    from aletheia.storage.models import new_id

    _insert(
        "agent_run_steps",
        {
            "id": new_id(),
            "run_id": run_id,
            "step_number": step_number,
            "action": action,
            "input_data": input_data,
            "output_data": output_data,
            "created_at": _step_timestamp(),
        },
    )

def plan_market_ohlcv_tiering(
    *,
    exchange: str,
    symbol: str,
    market_type: str,
    timeframe: str,
    hot_bars: int = 2_048,
    cold_chunk_bars: int = 4_096,
):
    """Build a format-neutral hot/cold plan from one exact stored series.

    This is read-only.  No rows are removed until a future configured cold
    backend returns durable, read-back-verified receipts.
    """
    from aletheia.storage.market_ohlcv_tiering import (
        OHLCVSeriesIdentity,
        OHLCVTieringPolicy,
        OHLCVTierManager,
    )

    db = get_db()
    rows = db.execute(
        "SELECT ts,open,high,low,close,volume,provider FROM market_ohlcv "
        "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? ORDER BY ts",
        (exchange.lower(), symbol, market_type, timeframe),
    ).fetchall()
    manager = OHLCVTierManager(OHLCVTieringPolicy(hot_bars=hot_bars, cold_chunk_bars=cold_chunk_bars))
    return manager.plan(
        OHLCVSeriesIdentity(exchange.lower(), symbol, market_type, timeframe),
        rows,
    )

def insert_universe_asset(asset: UniverseAsset) -> None:
    _insert("universe_registry", asset.model_dump())

def universe_twin_shadow_capture_due(
    captured_at: str,
    *,
    min_interval_sec: int = 55 * 60,
) -> bool:
    """Return whether a compact diagnostic sample may be captured now."""

    row = (
        get_db()
        .execute("SELECT captured_at FROM universe_twin_shadow_batches ORDER BY captured_at DESC LIMIT 1")
        .fetchone()
    )
    latest = str(row["captured_at"]) if row else None
    return _universe_twin_shadow_capture_due_from_latest(
        captured_at,
        latest,
        min_interval_sec=min_interval_sec,
    )

def get_universe_twin_shadow_batch(scanner_run_id: str) -> dict | None:
    row = (
        get_db()
        .execute(
            "SELECT * FROM universe_twin_shadow_batches WHERE scanner_run_id=?",
            (scanner_run_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None

def insert_relative_rank_shadow_bundle(
    batch,
    *,
    outcomes: Sequence | None = None,
    min_interval_sec: int = 55 * 60,
) -> int:
    """Atomically persist one isolated relative-rank cluster and its outcomes."""

    batch_data = batch.model_dump()
    outcome_rows = [item.model_dump() for item in outcomes or ()]
    try:
        records = json.loads(batch.records_json)
    except (TypeError, ValueError):
        raise ValueError("relative_rank_shadow_records_invalid") from None
    if not isinstance(records, list) or len(records) != batch.candidate_count:
        raise ValueError("relative_rank_shadow_candidate_count_mismatch")
    if batch.assignment_unit_key != batch.scanner_run_id:
        raise ValueError("relative_rank_shadow_assignment_unit_mismatch")
    if batch.promotion_eligible:
        raise ValueError("relative_rank_shadow_promotion_not_allowed")
    selected_keys = {
        str(record.get("outcome_candidate_key")) for record in records if record.get("assigned_policy_selected")
    }
    if "None" in selected_keys or len(selected_keys) != len(outcome_rows):
        raise ValueError("relative_rank_shadow_outcome_count_mismatch")
    if selected_keys != {str(row.get("outcome_candidate_key")) for row in outcome_rows}:
        raise ValueError("relative_rank_shadow_outcome_identity_mismatch")

    db = get_db()
    with transaction(immediate=True):
        stored = db.execute(
            "SELECT batch_hash FROM universe_relative_rank_shadow_batches WHERE scanner_run_id=?",
            (batch.scanner_run_id,),
        ).fetchone()
        if stored is not None:
            if stored["batch_hash"] != batch.batch_hash:
                raise ValueError("relative_rank_shadow_batch_identity_conflict")
            _assert_twin_outcomes_match(outcome_rows)
            return 0
        latest = db.execute(
            "SELECT captured_at FROM universe_relative_rank_shadow_batches ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if not _universe_twin_shadow_capture_due_from_latest(
            batch.captured_at,
            str(latest["captured_at"]) if latest else None,
            min_interval_sec=min_interval_sec,
        ):
            return 0
        columns = list(batch_data)
        execute_write(
            "INSERT INTO universe_relative_rank_shadow_batches "
            f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [batch_data[column] for column in columns],
        )
        if outcome_rows:
            _assert_twin_outcomes_match(outcome_rows, allow_missing=True)
            insert_filter_candidate_outcomes_batch(list(outcomes or ()))
            _assert_twin_outcomes_match(outcome_rows)
    return 1

def list_relative_rank_shadow_batches(policy_epoch_id: str) -> list[dict]:
    rows = (
        get_db()
        .execute(
            "SELECT * FROM universe_relative_rank_shadow_batches WHERE policy_epoch_id=? ORDER BY captured_at",
            (policy_epoch_id,),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]

def list_active_candidate_attention(limit: int = 100) -> list[dict]:
    timestamp = datetime.now(timezone.utc).isoformat()
    return [
        _project_candidate_admission(dict(row))
        for row in get_db()
        .execute(
            "SELECT pg.*, um.rank, um.total_score, um.entry_price_usd, "
            "um.liquidity_score, um.spread_bps, um.inclusion_reason, um.risk_flags, "
            "um.execution_eligible, um.expires_at AS candidate_expires_at, "
            "um.candidate_selection_policy, um.candidate_selection_policy_epoch, "
            "um.candidate_selection_utility_pct, um.opportunity_mechanism, "
            "um.opportunity_mechanism_version, um.opportunity_source_component, "
            "um.opportunity_source_cutoff_at, um.opportunity_evidence_hash, "
            "um.candidate_opportunity_context_json, um.candidate_opportunity_context_hash "
            "FROM candidate_planning_generations pg "
            "LEFT JOIN universe_members um ON um.id=pg.last_candidate_id "
            "WHERE pg.is_current=1 AND pg.attention_status='active' "
            "AND pg.soft_expires_at>? AND pg.hard_expires_at>? "
            "ORDER BY MIN(pg.soft_expires_at, pg.hard_expires_at), pg.id LIMIT ?",
            (timestamp, timestamp, max(1, min(int(limit), 200))),
        )
        .fetchall()
    ]

def insert_universe_policy(policy: UniversePolicy) -> None:
    _insert("universe_policies", policy.model_dump())

def insert_forward_outcome(outcome) -> None:
    _insert("universe_forward_outcomes", _forward_outcome_insert_payload(outcome))

def insert_universe_discovery_benchmark(window) -> dict:
    """Insert one frozen read-only benchmark window, enforcing one active row."""
    data = _benchmark_payload(window)
    data["promotion_eligible"] = 0
    columns = (
        "id", "status", "contract_json", "contract_hash", "code_commit", "started_at",
        "capture_ends_at", "finalize_not_before", "created_at", "invalidated_at",
        "invalidation_reason", "aborted_at", "abort_reason", "finalized_at",
        "summary_json", "artifact_path", "artifact_sha256", "artifact_size",
        "capture_count", "last_capture_at", "promotion_eligible",
    )
    row = {column: data[column] for column in columns if column in data}
    try:
        with transaction(immediate=True):
            _insert("universe_discovery_benchmarks", row)
    except sqlite3.IntegrityError as exc:
        if "uq_universe_discovery_benchmark_active" in str(exc) or "UNIQUE constraint failed" in str(exc):
            raise ValueError("active_universe_discovery_benchmark_exists") from exc
        raise
    stored = _select_one("universe_discovery_benchmarks", str(data["id"]))
    if stored is None:
        raise RuntimeError("universe discovery benchmark insert did not persist")
    return stored

def insert_filter_shadow_experiment(experiment: dict) -> bool:
    """Insert an immutable prospective plan, returning False for a duplicate key."""
    if _canonical_lab_authority():
        raise RuntimeError("observation_lab_authority_required")
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(experiment.keys())
    placeholders = ", ".join(["?"] * len(experiment))
    cursor = execute_write(
        f"INSERT OR IGNORE INTO filter_shadow_experiments ({cols}) VALUES ({placeholders})",
        list(experiment.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cursor.rowcount)

def update_filter_shadow_experiment(experiment_id: str, **values) -> None:
    if _canonical_lab_authority():
        raise RuntimeError("observation_lab_authority_required")
    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{key} = ?" for key in values)
    execute_write(
        f"UPDATE filter_shadow_experiments SET {sets} WHERE id = ?",
        [*values.values(), experiment_id],
    )
    if not was_in_tx:
        db.commit()

def insert_filter_shadow_evaluation(evaluation: dict) -> None:
    if _canonical_lab_authority():
        raise RuntimeError("observation_lab_authority_required")
    db = get_db()
    was_in_tx = db.in_transaction
    _insert("filter_shadow_evaluations", evaluation)
    if not was_in_tx:
        db.commit()

def latest_perp_structure(symbol: str, source: str | None = None) -> dict | None:
    """Return the newest perp structure row for a symbol."""
    if source is not None:
        rows = _select_all(
            "perp_structure_snapshots",
            where="symbol = ? AND source = ?",
            params=(symbol, source),
            order="as_of_time DESC, observed_at DESC",
            limit=1,
        )
    else:
        rows = _select_all(
            "perp_structure_snapshots",
            where="symbol = ?",
            params=(symbol,),
            order="as_of_time DESC, observed_at DESC",
            limit=1,
        )
    return rows[0] if rows else None

def batch_funding_rates(symbols: list[str], exchange: str) -> dict[str, dict]:
    """Return {symbol: latest_row} for exact exchange (latest by observed_at DESC)."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    db = get_db()
    rows = db.execute(
        f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY observed_at DESC
            ) AS _rn
            FROM funding_rate_snapshots
            WHERE symbol IN ({placeholders}) AND exchange = ?
              AND {LEGACY_DERIVATIVES_WHERE}
        ) WHERE _rn = 1
        """,
        (*symbols, exchange),
    ).fetchall()
    from aletheia.data.derivatives_facts import project_runtime_snapshot
    return {row['symbol']: row for r in rows if (row := project_runtime_snapshot(dict(r), 'funding')) is not None}

def batch_open_interest(symbols: list[str], exchange: str) -> dict[str, dict]:
    """Return the newest exact-exchange OI row for each requested symbol."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    rows = (
        get_db()
        .execute(
            f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY symbol ORDER BY observed_at DESC
            ) AS _rn
            FROM open_interest_snapshots
            WHERE symbol IN ({placeholders}) AND exchange = ?
              AND {LEGACY_DERIVATIVES_WHERE}
        ) WHERE _rn = 1
        """,
            (*symbols, exchange),
        )
        .fetchall()
    )
    from aletheia.data.derivatives_facts import project_runtime_snapshot
    return {row['symbol']: row for r in rows if (row := project_runtime_snapshot(dict(r), 'open_interest')) is not None}

def batch_news(
    symbols: list[str],
    since_iso: str,
    limit_per_symbol: int = 3,
) -> dict[str, list[dict]]:
    """Return exact, analyzed, relevant news grouped by requested symbol.

    Historical ``symbols_mentioned`` values are hints only. Every row is
    revalidated against article text so old substring false positives cannot
    re-enter Observer context.
    """
    if not symbols:
        return {}
    from aletheia.data.news_entities import news_matches_symbol

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT id, title, source, summary, content, published_at, symbols_mentioned, "
        "relevance_score, analyzed_at FROM news_items "
        "WHERE status = 'analyzed' AND relevance_score >= 0.6 "
        "AND datetime(published_at) > datetime(?) "
        "AND datetime(published_at) <= datetime(?) "
        "ORDER BY published_at DESC LIMIT 500",
        (since_iso, now_iso),
    ).fetchall()
    result: dict[str, list] = {s: [] for s in symbols}
    for row in rows:
        item = dict(row)
        for sym in symbols:
            if len(result[sym]) >= limit_per_symbol:
                continue
            if news_matches_symbol(sym, item.get("title"), item.get("summary"), item.get("content")):
                result[sym].append(item)
    return result

def latest_options_structure(symbol: str) -> dict | None:
    rows = _select_all(
        "options_market_structure_snapshots",
        where="symbol = ?",
        params=(symbol,),
        order="observed_at DESC",
        limit=1,
    )
    return rows[0] if rows else None

def insert_onchain_snapshot(data: dict) -> None:
    """Insert one onchain_snapshots row (stablecoin_mcap | chain_tvl | dex_volume).

    Follows the was_in_tx pattern: sqlite.py opens connections with
    isolation_level=None (autocommit), so we must explicitly BEGIN/COMMIT
    only when NOT already inside a transaction started by the caller.
    """
    from aletheia.storage.models import new_id

    if "id" not in data:
        data["id"] = new_id()
    _insert("onchain_snapshots", data)

def prune_filter_shadow_evaluations(days: int = 90) -> int:
    """Prune filter_shadow_evaluations older than ``days`` (by created_at).

    Called first by ``prune_filter_shadow_experiments`` to cascade-delete
    children; can also be invoked independently.
    """
    try:
        if _canonical_lab_authority():
            return 0
    except ExperimentStateReadError:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM filter_shadow_evaluations WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0

def prune_universe_members(
    days: int = 30,
    min_experiment_started_at: str | None | object = _AUTO_EXPERIMENT_FLOOR,
) -> int:
    """Prune universe_members whose parent snapshot is older than ``days``.

    Exposed for the _PRUNABLE whitelist. ``prune_universe_snapshots`` calls
    this first inside a single transaction.
    """
    try:
        lab_authority = _canonical_lab_authority()
    except ExperimentStateReadError:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    protected_members: tuple[str, ...] = ()
    if lab_authority:
        try:
            refs = _active_observation_claim_refs("universe_forward_outcomes")
            protected_members, _ = _protected_universe_outcome_ids(get_db(), refs)
        except (ExperimentStateReadError, sqlite3.Error):
            return 0
        if protected_members in {
            ("__claim_set_too_large__",),
            ("__claim_source_unavailable__",),
        }:
            return 0
        outcome_cutoff = _effective_cutoff(days, None)
    else:
        min_experiment_started_at = _resolve_experiment_floor(min_experiment_started_at)
        outcome_cutoff = _effective_cutoff(days, min_experiment_started_at)
    db = get_db()
    was_in_tx = db.in_transaction
    protected_clause = ""
    protected_params: tuple[str, ...] = ()
    if protected_members:
        placeholders = ",".join("?" for _ in protected_members)
        protected_clause = f" AND id NOT IN ({placeholders})"
        protected_params = protected_members
    cur = execute_write(
        "DELETE FROM universe_members WHERE snapshot_id IN ("
        "  SELECT id FROM universe_snapshots WHERE created_at < ?"
        ") AND NOT EXISTS ("
        "  SELECT 1 FROM universe_forward_outcomes o "
        "  WHERE o.member_id=universe_members.id AND o.entry_ts>=?"
        ")" + protected_clause,
        (cutoff, outcome_cutoff, *protected_params),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0
