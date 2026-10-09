"""Read-only storage and trust probes for doctor reports."""

from __future__ import annotations

__all__ = [
    "_classify_db_capacity",
    "_probe_audit_chain",
    "_probe_fts_coverage",
    "_probe_funding_audit_mirror",
    "_probe_infra_db_size",
    "_probe_infra_schema_current",
    "_probe_runtime_session_provenance",
]


def _probe_runtime_session_provenance(report: Report) -> None:
    """Runtime Session provenance integrity — four distinct states surfaced explicitly.

    1. active durable session  — session row verified in DB; child rows carry a
       traceable runtime_session_id.
    2. active non-durable (degraded) — session exists in-memory but DB row absent or
       unverifiable; child rows stamp NULL to avoid orphan references.
    3. no active session       — process has not called start_runtime_session() yet;
       normal for CLI / test contexts.
    4. stale open sessions     — sessions with ended_at IS NULL from prior process
       crashes that were never cleanly ended.

    Also reports orphan reference counts (child rows referencing a session_id with no
    parent row) so analytics can know attribution coverage rather than silently
    treating orphan ids as valid.
    """
    try:
        from aletheia.agent_runtime.runtime_session import (
            current_external_gateway_session,
            current_session,
            current_session_durable,
        )
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db

        sess = current_session()
        db = get_db()
        open_sessions = [
            dict(row)
            for row in db.execute(
                "SELECT session_id, runtime_id, boot_commit, behavior_fingerprint, "
                "started_at FROM runtime_sessions WHERE ended_at IS NULL"
            ).fetchall()
        ]
        external_id = None
        if sess is None:
            external_session = current_external_gateway_session(open_sessions)
            external_id = (external_session or {}).get("session_id")
        else:
            external_session = None

        # --- 1/2/3: current-process session state ---
        if external_session is not None:
            report.add(
                "trust.runtime_session.current",
                "ok",
                f"active durable external Gateway session "
                f"{external_session['session_id'][:16]}… "
                f"(commit={(external_session.get('boot_commit') or '-')[:12]}, "
                f"fingerprint={(external_session.get('behavior_fingerprint') or '-')[:12]}…)",
            )
        elif sess is None:
            report.add(
                "trust.runtime_session.current",
                "info",
                "no active runtime session (process not started or CLI context)",
            )
        elif current_session_durable():
            report.add(
                "trust.runtime_session.current",
                "ok",
                f"active durable session {sess['session_id'][:16]}… "
                f"(commit={( sess.get('boot_commit') or '-')[:12]}, "
                f"fingerprint={( sess.get('behavior_fingerprint') or '-')[:12]}…)",
            )
        else:
            report.add(
                "trust.runtime_session.current",
                "warn",
                f"active session {sess['session_id'][:16]}… is NON-DURABLE — "
                "DB row absent or fingerprint mismatch; child rows stamped with NULL "
                "runtime_session_id (provenance degraded, trading continues)",
                fix_hint="Call retry_session_durability() or restart the gateway to "
                         "attempt re-registration.",
            )

        # --- 4: stale open sessions from prior crashes ---
        try:
            current_id = sess["session_id"] if sess else external_id
            stale = [
                s for s in open_sessions
                if s["session_id"] != current_id
            ]
            if stale:
                ids = ", ".join(s["session_id"][:16] + "…" for s in stale[:3])
                report.add(
                    "trust.runtime_session.stale_open",
                    "warn",
                    f"{len(stale)} stale open session(s) from prior crash(es): {ids}",
                    fix_hint="These sessions were never cleanly ended — likely a crash. "
                             "They do not affect current trading but inflate the open-session "
                             "count. end_runtime_session() was not called on gateway shutdown.",
                )
            else:
                report.add(
                    "trust.runtime_session.stale_open",
                    "ok",
                    "no stale open sessions from prior crashes",
                )
        except Exception as e:
            report.add_probe_error("trust.runtime_session.stale_open", e, "stale-session probe error")

        # --- Orphan scan: child rows with no parent session ---
        try:
            scan = repo.scan_orphan_session_ids()
            if "error" in scan:
                report.add("trust.runtime_session.orphans", "info",
                           f"orphan scan failed: {scan['error']}")
            elif scan["total_orphan_refs"] == 0:
                report.add("trust.runtime_session.orphans", "ok",
                           "no orphan runtime_session_id references found")
            else:
                table_summary = "; ".join(
                    f"{t}:{sum(v for v in cols.values())}"
                    for t, cols in scan["by_table"].items()
                )
                n_ids = len(scan["orphan_session_ids"])
                report.add(
                    "trust.runtime_session.orphans",
                    "warn",
                    f"{scan['total_orphan_refs']} orphan reference(s) across "
                    f"{n_ids} unknown session id(s): {table_summary}",
                    fix_hint="These child rows advertise a runtime_session_id that has no "
                             "parent in runtime_sessions. They are not fabricated — the "
                             "session row was simply never written. Use "
                             "scan_orphan_session_ids() for a full breakdown.",
                )
        except Exception as e:
            report.add_probe_error("trust.runtime_session.orphans", e, "orphan scan error")

    except Exception as e:
        report.add_probe_error("trust.runtime_session.current", e)


def _probe_audit_chain(report: Report) -> None:
    """Verify audit JSONL hash chain integrity."""
    from aletheia.files.jsonl import verify_audit_chain
    result = verify_audit_chain()
    records = result.get("records", 0)
    if records == 0:
        report.add("trust.audit_chain", "info", "no audit records yet")
        return
    reanchors = result.get("reanchors", 0)
    reanchor_extra = f", {reanchors} rotation re-anchors" if reanchors else ""
    if result["valid"]:
        report.add("trust.audit_chain", "ok", f"hash chain valid ({records} records{reanchor_extra})")
    else:
        errors = result.get("errors", [])
        error_timestamps = result.get("error_timestamps", [])
        # Split at the current effective write-path fix. The original file lock
        # serialized appends, but each gateway process still cached its own chain
        # tail; frontdoor/backend interleaving could therefore continue from a
        # stale _prev_hash. The writer now re-reads the chain tail while holding
        # the cross-process lock, so only breaks after this timestamp indicate
        # an active write-path or tamper problem.
        EFFECTIVE_CHAIN_FIX_TS = "2026-07-03T08:00:00+00:00"
        effective_ts = _parse_iso_datetime(EFFECTIVE_CHAIN_FIX_TS)
        ts_padded = list(error_timestamps) + [""] * len(errors)
        # #179 fix: undateable breaks (no parseable timestamp on the surrounding
        # audit event) used to be silently excluded from `post_fix`; when every
        # break was undateable the code declared "all historical, info" and
        # masked a real post-fix corruption that happened to also break the
        # timestamp. Now we split into three bins so an undateable break can't
        # hide behind the historical label.
        post_fix: list[str] = []
        undateable: list[str] = []
        pre_fix_count = 0
        for e, t in zip(errors, ts_padded, strict=False):
            parsed = _parse_iso_datetime(t)
            if parsed is None:
                undateable.append(e)
            elif effective_ts is not None and parsed > effective_ts:
                post_fix.append(e)
            else:
                pre_fix_count += 1

        if not post_fix and not undateable:
            report.add("trust.audit_chain", "info",
                       f"{len(errors)} historical chain break(s), all before current "
                       f"cross-process cache fix ({EFFECTIVE_CHAIN_FIX_TS}); "
                       f"zero post-lock breaks ({records} records{reanchor_extra})")
        elif post_fix:
            detail = f"chain BROKEN — {len(post_fix)} post-fix error(s) in {records} records: {post_fix[:3]}"
            report.add("trust.audit_chain", "warn", detail,
                       "audit log may have been tampered with or corrupted (post hash-cache fix)")
        else:
            # Only undateable breaks — can't confirm they're historical, so
            # surface them as warn (not info) rather than hiding them.
            detail = (
                f"{len(undateable)} chain break(s) with unparseable timestamp — "
                f"cannot confirm historical vs post-fix; treat as WARN: {undateable[:3]}"
            )
            report.add("trust.audit_chain", "warn", detail,
                       "inspect audit log manually; missing timestamps prevent automatic classification")


def _probe_funding_audit_mirror(report: Report) -> None:
    """Surface durable funding audits still missing their JSONL mirror."""
    from aletheia.portfolio.funding_audit import funding_audit_backlog

    backlog = funding_audit_backlog()
    if backlog["count"]:
        report.add(
            "trust.audit_mirror_backlog",
            "warn",
            f"{backlog['count']} funding audit mirror(s) pending; "
            f"oldest={backlog['oldest_at'] or 'unknown'}",
            "the funding_audit_reconciler will retry without replaying accounting",
        )
    else:
        report.add(
            "trust.audit_mirror_backlog",
            "ok",
            "no pending funding audit mirrors",
        )


def _probe_fts_coverage(report: Report) -> None:
    """Check FTS5 index coverage vs *rebuildable* eligible knowledge items.

    Coverage denominator must be items whose body is still recoverable — either
    the DB ``content`` column is populated, or the ``file_path`` still resolves
    to a readable Markdown file via ``retrieval._read_body`` (which handles the
    Windows/POSIX legacy-path migration). Rows whose body is unreachable are
    audit stubs, not learnable material, and are reported separately as
    ``knowledge.unindexable_high_confidence`` so users don't misread lost bodies
    as "the reindex job hasn't finished".
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
    ).fetchone()
    if not row:
        report.add("fts.coverage", "warn", "knowledge_fts table missing — experience loop disabled",
                   "run: uv run aletheia knowledge reindex")
        return
    try:
        from extensions.knowledge.retrieval import _read_body
    except Exception:
        _read_body = None  # type: ignore[assignment]

    active_rows = db.execute(
        "SELECT file_path, content FROM knowledge_items "
        "WHERE confidence='high_confidence' AND (status='active' OR status IS NULL)"
    ).fetchall()
    total_active = len(active_rows)
    rebuildable = 0
    unindexable = 0
    for r in active_rows:
        has_db_body = bool((r["content"] or "").strip())
        has_disk_body = False
        if not has_db_body and _read_body is not None:
            try:
                has_disk_body = bool((_read_body(r["file_path"]) or "").strip())
            except Exception as exc:
                logger.warning("doctor: FTS disk-body probe unavailable (%s)", type(exc).__name__)
                has_disk_body = False
        if has_db_body or has_disk_body:
            rebuildable += 1
        else:
            unindexable += 1

    indexed = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]

    # Separate signal for the unrecoverable stubs so recovery/downgrade work
    # is visible without contaminating coverage severity.
    if unindexable > 0:
        report.add(
            "knowledge.unindexable_high_confidence",
            "warn",
            f"{unindexable} of {total_active} high-confidence rows have no reachable body "
            f"(DB content empty and file_path unreadable)",
            "run: uv run aletheia knowledge scan-recovery-sources; "
            "then: uv run aletheia knowledge downgrade-unindexable-high-confidence",
        )
    else:
        report.add(
            "knowledge.unindexable_high_confidence",
            "ok",
            "all high-confidence bodies reachable",
        )

    if total_active == 0:
        # No high-confidence rows exist yet. Legacy behavior: green.
        report.add("fts.coverage", "ok", "no eligible items yet")
        return
    if rebuildable == 0:
        report.add(
            "fts.coverage",
            "warn",
            f"{indexed}/{rebuildable} rebuildable indexed (no reachable bodies)",
            "recover bodies before reindex — see knowledge.unindexable_high_confidence",
        )
        return

    pct = indexed / rebuildable
    detail = f"{indexed}/{rebuildable} rebuildable indexed ({pct:.0%})"
    if pct >= 0.8:
        report.add("fts.coverage", "ok", detail)
    elif pct >= 0.5:
        report.add("fts.coverage", "warn", detail, "restart gateway to trigger reindex")
    else:
        report.add(
            "fts.coverage", "blocker", detail,
            "experience loop severely degraded — restart gateway or run: uv run aletheia knowledge reindex",
        )


def _classify_db_capacity(
    *,
    db_mb: float,
    free_mb: float,
    reclaimable_mb: float,
    capacity_mb: float | None = None,
    auto_vacuum: int | None = None,
    wal_mb: float = 0.0,
) -> tuple[str, str, str | None]:
    """Classify storage pressure separately from normal retained database growth."""
    minimum_free_mb = max(500.0, db_mb * 1.2)
    if free_mb < minimum_free_mb:
        return (
            "warn",
            "low disk space",
            "free disk space before SQLite writes fail; database compaction also needs temporary free space",
        )

    if wal_mb > 64.0:
        return (
            "warn",
            "WAL above retained limit",
            "inspect `logs/database_capacity.jsonl` for repeated checkpoint backlog; "
            "a long-lived reader or external process may be pinning an old snapshot",
        )

    if capacity_mb and capacity_mb > 0 and db_mb >= capacity_mb:
        return (
            "warn",
            "capacity budget exceeded",
            "run `uv run aletheia data capacity --deep`; compact offline only when reusable space is material",
        )

    if auto_vacuum == 0 and reclaimable_mb >= 20:
        return (
            "info",
            "incremental reclaim disabled",
            "new databases enable incremental vacuum automatically; for this database plan `uv run aletheia data compact --apply` with the Gateway stopped",
        )

    reclaim_ratio = reclaimable_mb / db_mb if db_mb > 0 else 0.0
    if reclaimable_mb >= 100 and reclaim_ratio >= 0.20:
        return (
            "info",
            "maintenance candidate",
            f"{reclaim_ratio:.0%} of the database is reusable freelist space; plan offline compaction only if disk recovery is needed",
        )
    if db_mb > 500:
        return "info", "large but compact", None
    if db_mb > 200:
        return "info", "growing", None
    return "ok", "normal", None


def _probe_infra_db_size(report: Report) -> None:
    """Report actual disk pressure and freelist waste, not size alone."""
    try:
        import shutil

        from aletheia.storage.sqlite import db_path, get_db
        path = db_path()
        database_bytes = path.stat().st_size if path.exists() else 0
        wal_path = path.with_name(path.name + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        db_mb = (database_bytes + wal_bytes) / (1024 * 1024)
        du = shutil.disk_usage(str(path.parent))
        free_mb = du.free / (1024 * 1024)
        db = get_db()
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        freelist_count = int(db.execute("PRAGMA freelist_count").fetchone()[0])
        auto_vacuum = int(db.execute("PRAGMA auto_vacuum").fetchone()[0])
        reclaimable_mb = page_size * freelist_count / (1024 * 1024)
        wal_mb = wal_bytes / (1024 * 1024)
        from aletheia.config.loader import load_config

        capacity_mb = float(
            load_config(persist_migrations=False).retention.app_db_capacity_mb
        )
    except Exception as e:
        report.add_probe_error("infra.db_size", e)
        return
    severity, label, hint = _classify_db_capacity(
        db_mb=db_mb,
        free_mb=free_mb,
        reclaimable_mb=reclaimable_mb,
        capacity_mb=capacity_mb,
        auto_vacuum=auto_vacuum,
        wal_mb=wal_mb,
    )
    detail = (
        f"{label}: app.db + WAL {db_mb:.0f} MB, reusable {reclaimable_mb:.1f} MB, "
        f"disk free {free_mb:.0f} MB"
    )
    report.add("infra.db_size", severity, detail, hint)


def _probe_infra_schema_current(report: Report) -> None:
    """A database behind the code's migrations makes reads fail on missing columns."""
    try:
        from aletheia.storage.migrations import migration_status
        status = migration_status()
    except Exception as e:
        report.add_probe_error("infra.schema", e)
        return
    pending = status.get("pending") or []
    if not pending:
        report.add("infra.schema", "ok", f"schema current at {status.get('schema_version')}")
        return
    report.add(
        "infra.schema",
        "warn",
        f"{len(pending)} pending migration(s); code expects {status.get('schema_version')} "
        f"— oldest pending {pending[0]}",
        "queries can fail on missing columns; run `uv run aletheia data migrate`",
    )


# Define exports before importing doctor so either module can be imported first.
from aletheia.diagnostics.doctor import (
    Check,
    Report,
    _age_minutes,
    _fmt_age,
    _parse_iso_datetime,
    logger,
)
