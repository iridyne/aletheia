"""One-shot data-retention backfill that reclaims dead writes accumulated before
Phase 3 retention windows existed. Runs with the gateway stopped; every DELETE
goes through the module-level ``prune_*`` functions so the same code path is
exercised by the recurring nightly cleanup afterwards.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# --- Audit-chain + learning invariants -------------------------------------
#
# Row counts for these tables MUST be identical before and after the backfill
# runs. Any deviation aborts the apply pass and demands a manual restore from
# the pre-migration backup. Mirrors AGENTS.md "trust-bearing tables are never
# pruned" and the spec's "audit-chain 12 + learning 7" whitelist.
_AUDIT_CHAIN_TABLES: tuple[str, ...] = (
    "audit_events",
    "fills",
    "capital_events",
    "orders",
    "positions",
    "proposals",
    "risk_decisions",
    "approvals",
    "knowledge_items",
    "trade_proposals",
    "ps_orders",
    "ps_fills",
    "ps_positions",
)

_LEARNING_TABLES: tuple[str, ...] = (
    "learning_experiments",
    "learning_exposures",
    "learning_hypotheses",
    "learning_policy_epochs",
    "learning_contribution_verdicts",
    "learning_lifecycle_events",
    "learning_job_health",
)


@dataclass
class RetentionTarget:
    """One prune-table entry in the backfill plan.

    Every field is deliberately explicit so the dry-run report reads like a
    manifest a human can grep for. ``prune_fn`` is looked up on the repositories
    module at run time — a missing function means the corresponding prune agent
    has not landed yet, and the row is skipped with a WARNING (not silently).
    """

    table: str
    prune_fn: str
    kwargs_factory: Callable[[argparse.Namespace, sqlite3.Connection], dict[str, Any]]
    predicate_sql: str  # For dry-run row/byte estimation only.
    predicate_params_factory: Callable[[argparse.Namespace, sqlite3.Connection], tuple[Any, ...]]
    archive: bool = False
    byte_col: str | None = None  # Optional column used to estimate archived bytes.
    notes: str = ""


@dataclass
class BackfillPlan:
    """Top-level backfill plan; every step is idempotent."""

    targets: list[RetentionTarget] = field(default_factory=list)
    fs_prunes: list[Callable[[argparse.Namespace], dict[str, Any]]] = field(default_factory=list)


# --- Small helpers ---------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff_iso(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _cutoff_ms(days: int) -> int:
    """UTC epoch cutoff in milliseconds — matches ``market_ohlcv.ts``."""
    return int((datetime.now(timezone.utc).timestamp() - days * 86400) * 1000)


def _select_min_open_experiment_started_at(db: sqlite3.Connection) -> str | None:
    """Return the earliest ``started_at`` of an open learning_experiments row.

    The filter-family prune functions must respect this: the clean_window_cutoff
    guard keeps every candidate/decision row a still-open experiment could still
    look at. Returns ``None`` when no open experiment exists (i.e. the guard is
    a no-op and the plain window applies).
    """
    try:
        row = db.execute(
            "SELECT MIN(started_at) FROM learning_experiments WHERE ended_at IS NULL"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    value = row[0]
    return str(value) if value else None


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _row_counts(db: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    """Return ``{table: rowcount}`` for the subset of tables that actually exist."""
    counts: dict[str, int] = {}
    for table in tables:
        if not _table_exists(db, table):
            continue
        cur = db.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = int(cur.fetchone()[0])
    return counts


def _db_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return path.stat().st_size / (1024 * 1024)


def _iso_from_migration_completed(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("completed_at") if isinstance(payload, dict) else None
    return str(value) if value else None


# --- Retention plan construction ------------------------------------------

def _build_plan(args: argparse.Namespace) -> BackfillPlan:
    """Assemble the full retention target list from the spec.

    Windows are held as ints on the args namespace so the CLI can override any
    of them (``--override table_key=days`` is intentionally out of scope for the
    one-shot script; edit args here to change them). ``kwargs_factory`` returns
    the kwargs passed to the prune fn — allows the filter family to inject
    ``min_experiment_started_at`` at call time.
    """
    plan = BackfillPlan()

    def _basic(days_attr: str) -> Callable[[argparse.Namespace, sqlite3.Connection], dict[str, Any]]:
        return lambda a, _db: {"days": getattr(a, days_attr)}

    def _basic_params(days_attr: str) -> Callable[[argparse.Namespace, sqlite3.Connection], tuple[Any, ...]]:
        return lambda a, _db: (_cutoff_iso(getattr(a, days_attr)),)

    # Type-tiered predicates for agent_shell_messages / agent_shell_artifacts /
    # agent_shell_tasks live inside prune_* fns; dry-run only needs eligible
    # rows for the archive counter, so we approximate with the longest window.
    plan.targets = [
        # --- Operator-shell telemetry -------------------------------------
        RetentionTarget(
            table="agent_shell_heartbeats",
            prune_fn="prune_agent_shell_heartbeats",
            kwargs_factory=_basic("agent_shell_heartbeats_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("agent_shell_heartbeats_days"),
            notes="write-side dedup lives in operator_store (Agent 6)",
        ),
        RetentionTarget(
            table="agent_shell_events",
            prune_fn="prune_agent_shell_events",
            kwargs_factory=_basic("agent_shell_events_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("agent_shell_events_days"),
            archive=True,
            notes="14d — semi-interesting operator telemetry, archive before delete",
        ),
        RetentionTarget(
            table="agent_shell_messages",
            prune_fn="prune_agent_shell_messages",
            kwargs_factory=lambda a, _db: {
                "days_context": a.agent_shell_messages_context_days,
                "days_other": a.agent_shell_messages_other_days,
            },
            predicate_sql=(
                "(message_type='context' AND created_at < ?) OR "
                "(message_type<>'context' AND created_at < ?)"
            ),
            predicate_params_factory=lambda a, _db: (
                _cutoff_iso(a.agent_shell_messages_context_days),
                _cutoff_iso(a.agent_shell_messages_other_days),
            ),
            archive=True,
            byte_col="content",
            notes="type-tiered: context=3d hard delete, other=14d archived",
        ),
        RetentionTarget(
            table="agent_shell_artifacts",
            prune_fn="prune_agent_shell_artifacts",
            kwargs_factory=lambda a, _db: {
                "days_strategy": a.agent_shell_artifacts_strategy_days,
                "days_proposal": a.agent_shell_artifacts_proposal_days,
                "days_other": a.agent_shell_artifacts_other_days,
            },
            predicate_sql="created_at < ?",  # widest window bound for size estimate
            predicate_params_factory=lambda a, _db: (
                _cutoff_iso(max(
                    a.agent_shell_artifacts_strategy_days,
                    a.agent_shell_artifacts_proposal_days,
                    a.agent_shell_artifacts_other_days,
                )),
            ),
            archive=True,
            byte_col="content",
            notes="type-tiered per spec Agent 2",
        ),
        RetentionTarget(
            table="agent_shell_tasks",
            prune_fn="prune_agent_shell_tasks",
            kwargs_factory=lambda a, _db: {
                "days_completed": a.agent_shell_tasks_completed_days,
                "days_failed": a.agent_shell_tasks_failed_days,
            },
            predicate_sql=(
                "status IN ('completed','failed','cancelled') AND "
                "completed_at IS NOT NULL AND completed_at < ?"
            ),
            predicate_params_factory=lambda a, _db: (
                _cutoff_iso(max(
                    a.agent_shell_tasks_completed_days,
                    a.agent_shell_tasks_failed_days,
                )),
            ),
            archive=True,
            notes="never touches status IN ('running','pending')",
        ),
        RetentionTarget(
            table="agent_shell_commands",
            prune_fn="prune_agent_shell_commands",
            kwargs_factory=_basic("agent_shell_commands_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("agent_shell_commands_days"),
            archive=True,
        ),
        # Sessions LAST inside the operator-shell family — parent table; the
        # child cascade happens inside prune_agent_shell_sessions.
        RetentionTarget(
            table="agent_shell_sessions",
            prune_fn="prune_agent_shell_sessions",
            kwargs_factory=_basic("agent_shell_sessions_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("agent_shell_sessions_days"),
            archive=True,
            notes="parent — Agent 2 cascade-deletes messages/events/artifacts/commands/tasks first",
        ),

        # --- Agent runs / run steps / tool calls --------------------------
        RetentionTarget(
            table="agent_run_steps",
            prune_fn="prune_run_steps",
            kwargs_factory=_basic("agent_run_steps_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("agent_run_steps_days"),
        ),

        # --- Filter family (clean_window_cutoff guard) --------------------
        RetentionTarget(
            table="filter_candidate_outcomes",
            prune_fn="prune_filter_candidate_outcomes",
            kwargs_factory=lambda a, db: {
                "days": a.filter_candidate_outcomes_days,
                "min_experiment_started_at": _select_min_open_experiment_started_at(db),
            },
            predicate_sql="entry_ts < ?",
            predicate_params_factory=_basic_params("filter_candidate_outcomes_days"),
            notes="clean_window_cutoff guard against open learning_experiments",
        ),
        RetentionTarget(
            table="filter_decisions",
            prune_fn="prune_filter_decisions",
            kwargs_factory=lambda a, db: {
                "days": a.filter_decisions_days,
                "min_experiment_started_at": _select_min_open_experiment_started_at(db),
            },
            predicate_sql="evaluated_at < ?",
            predicate_params_factory=_basic_params("filter_decisions_days"),
            notes="clean_window_cutoff guard",
        ),
        RetentionTarget(
            table="filter_decision_batches",
            prune_fn="prune_filter_decision_batches",
            kwargs_factory=_basic("filter_decision_batches_days"),
            predicate_sql="evaluated_at < ?",
            predicate_params_factory=_basic_params("filter_decision_batches_days"),
        ),
        RetentionTarget(
            table="filter_shadow_experiments",
            prune_fn="prune_filter_shadow_experiments",
            kwargs_factory=_basic("filter_shadow_experiments_days"),
            # Matches prune_filter_shadow_experiments: `status != 'collecting'
            # AND started_at < ?`. The prune fn keys on started_at (not
            # created_at) because a still-collecting experiment may have been
            # inserted long before its first evaluation.
            predicate_sql=(
                "status != 'collecting' AND started_at < ?"
            ),
            predicate_params_factory=_basic_params("filter_shadow_experiments_days"),
            notes="status='collecting' EXEMPT (matches prune fn)",
        ),

        # --- Market telemetry ---------------------------------------------
        RetentionTarget(
            table="market_ohlcv",
            prune_fn="prune_market_ohlcv",
            kwargs_factory=lambda a, _db: {
                "ttl_days": {
                    "1m": a.market_ohlcv_1m_days,
                    "5m": a.market_ohlcv_5m_days,
                    "15m": a.market_ohlcv_15m_days,
                    "1h": a.market_ohlcv_1h_days,
                    "4h": a.market_ohlcv_4h_days,
                    "1d": a.market_ohlcv_1d_days,
                },
            },
            # ts is stored as INTEGER milliseconds since epoch — compare with the
            # per-timeframe cutoff. legacy_unknown rows are counted always
            # because the prune fn hard-deletes them regardless of window.
            predicate_sql=(
                "exchange='legacy_unknown' OR "
                "(timeframe='1m' AND ts < ?) OR (timeframe='5m' AND ts < ?) OR "
                "(timeframe='15m' AND ts < ?) OR (timeframe='1h' AND ts < ?) OR "
                "(timeframe='4h' AND ts < ?) OR (timeframe='1d' AND ts < ?)"
            ),
            predicate_params_factory=lambda a, _db: (
                _cutoff_ms(a.market_ohlcv_1m_days),
                _cutoff_ms(a.market_ohlcv_5m_days),
                _cutoff_ms(a.market_ohlcv_15m_days),
                _cutoff_ms(a.market_ohlcv_1h_days),
                _cutoff_ms(a.market_ohlcv_4h_days),
                _cutoff_ms(a.market_ohlcv_1d_days),
            ),
            notes="per-timeframe TTL + hard-delete exchange='legacy_unknown'",
        ),
        RetentionTarget(
            table="funding_rate_snapshots",
            prune_fn="prune_funding_rate_snapshots",
            kwargs_factory=_basic("funding_rate_snapshots_days"),
            predicate_sql="observed_at < ?",
            predicate_params_factory=_basic_params("funding_rate_snapshots_days"),
        ),
        RetentionTarget(
            table="open_interest_snapshots",
            prune_fn="prune_open_interest_snapshots",
            kwargs_factory=_basic("open_interest_snapshots_days"),
            predicate_sql="observed_at < ?",
            predicate_params_factory=_basic_params("open_interest_snapshots_days"),
        ),
        RetentionTarget(
            table="coingecko_top_snapshots",
            prune_fn="prune_coingecko_top_snapshots",
            kwargs_factory=_basic("coingecko_top_snapshots_days"),
            predicate_sql="observed_at < ?",
            predicate_params_factory=_basic_params("coingecko_top_snapshots_days"),
        ),
        RetentionTarget(
            table="market_regimes",
            prune_fn="prune_market_regimes",
            kwargs_factory=_basic("market_regimes_days"),
            predicate_sql="detected_at < ?",
            predicate_params_factory=_basic_params("market_regimes_days"),
        ),

        # --- Universe -----------------------------------------------------
        RetentionTarget(
            table="universe_feature_snapshots",
            prune_fn="prune_universe_feature_snapshots",
            kwargs_factory=_basic("universe_feature_snapshots_days"),
            predicate_sql="ts < ?",
            predicate_params_factory=_basic_params("universe_feature_snapshots_days"),
        ),
        RetentionTarget(
            table="universe_snapshots",
            prune_fn="prune_universe_snapshots",
            kwargs_factory=_basic("universe_snapshots_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("universe_snapshots_days"),
            notes="cascade-deletes universe_members inside prune fn",
        ),
        RetentionTarget(
            table="universe_scanner_runs",
            prune_fn="prune_universe_scanner_runs",
            kwargs_factory=_basic("universe_scanner_runs_days"),
            predicate_sql="started_at < ?",
            predicate_params_factory=_basic_params("universe_scanner_runs_days"),
        ),

        # --- Alpha control / controller recommendations ------------------
        RetentionTarget(
            table="alpha_control_observations",
            prune_fn="prune_alpha_control_observations",
            kwargs_factory=_basic("alpha_control_observations_days"),
            predicate_sql="entry_ts < ?",
            predicate_params_factory=_basic_params("alpha_control_observations_days"),
        ),
        RetentionTarget(
            table="controller_recommendations",
            prune_fn="prune_controller_recommendations",
            kwargs_factory=_basic("controller_recommendations_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("controller_recommendations_days"),
        ),

        # --- Serenity / backtest / runtime sessions ----------------------
        RetentionTarget(
            table="serenity_evidence",
            prune_fn="prune_serenity_evidence",
            kwargs_factory=_basic("serenity_evidence_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("serenity_evidence_days"),
        ),
        RetentionTarget(
            table="serenity_run_events",
            prune_fn="prune_serenity_run_events",
            kwargs_factory=_basic("serenity_run_events_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("serenity_run_events_days"),
        ),
        RetentionTarget(
            table="runtime_sessions",
            prune_fn="prune_runtime_sessions",
            kwargs_factory=_basic("runtime_sessions_days"),
            # prune_runtime_sessions filters `ended_at IS NOT NULL AND
            # started_at < ?`; mirror the same predicate so the dry-run row
            # count matches the delete.
            predicate_sql="ended_at IS NOT NULL AND started_at < ?",
            predicate_params_factory=_basic_params("runtime_sessions_days"),
        ),
        RetentionTarget(
            table="backtest_runs",
            prune_fn="prune_backtest_runs",
            kwargs_factory=_basic("backtest_runs_days"),
            predicate_sql="created_at < ?",
            predicate_params_factory=_basic_params("backtest_runs_days"),
            notes="cascade-deletes backtest_trades inside prune fn",
        ),
    ]

    plan.fs_prunes = [
        _fs_prune_session_logs,
        _fs_prune_task_notes,
        _fs_prune_backups,
        _fs_prune_diagnostics,
        _fs_prune_pre_migration_backup,
        _fs_prune_tasks_db_agent_task_claims,
    ]
    return plan


# --- CLI argument parsing --------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aletheia.maintenance.data_retention_backfill",
        description="One-shot Phase 3 data retention backfill.",
    )
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="(default) print plan; make no changes.")
    parser.add_argument("--apply", action="store_true",
                        help="apply the plan. Requires gateway stopped.")
    parser.add_argument("--yes", action="store_true",
                        help="skip interactive confirmation before deletes.")
    parser.add_argument("--vacuum", action="store_true",
                        help="run VACUUM + PRAGMA optimize after deletes (implied by --apply).")
    parser.add_argument("--archive-dir", type=Path, default=None,
                        help="override archive output dir (default: ~/.aletheia/backups/data_retention_archive_YYYYMMDD/).")
    parser.add_argument("--tables", type=str, default="",
                        help="comma-separated allowlist; runs every target if empty.")
    parser.add_argument("--skip-fs", action="store_true",
                        help="skip filesystem prunes (DB only).")

    # Retention windows (spec Agent 7 keys). Defaults match spec.
    parser.add_argument("--agent-shell-messages-context-days", dest="agent_shell_messages_context_days", type=int, default=3)
    parser.add_argument("--agent-shell-messages-other-days", dest="agent_shell_messages_other_days", type=int, default=14)
    parser.add_argument("--agent-run-steps-days", dest="agent_run_steps_days", type=int, default=14)
    parser.add_argument("--agent-shell-events-days", dest="agent_shell_events_days", type=int, default=14)
    parser.add_argument("--agent-shell-heartbeats-days", dest="agent_shell_heartbeats_days", type=int, default=2)
    parser.add_argument("--agent-shell-artifacts-strategy-days", dest="agent_shell_artifacts_strategy_days", type=int, default=7)
    parser.add_argument("--agent-shell-artifacts-proposal-days", dest="agent_shell_artifacts_proposal_days", type=int, default=30)
    parser.add_argument("--agent-shell-artifacts-other-days", dest="agent_shell_artifacts_other_days", type=int, default=14)
    parser.add_argument("--agent-shell-tasks-completed-days", dest="agent_shell_tasks_completed_days", type=int, default=14)
    parser.add_argument("--agent-shell-tasks-failed-days", dest="agent_shell_tasks_failed_days", type=int, default=30)
    parser.add_argument("--agent-shell-sessions-days", dest="agent_shell_sessions_days", type=int, default=14)
    parser.add_argument("--agent-shell-commands-days", dest="agent_shell_commands_days", type=int, default=14)
    parser.add_argument("--filter-candidate-outcomes-days", dest="filter_candidate_outcomes_days", type=int, default=30)
    parser.add_argument("--filter-decisions-days", dest="filter_decisions_days", type=int, default=45)
    parser.add_argument("--filter-decision-batches-days", dest="filter_decision_batches_days", type=int, default=30)
    parser.add_argument("--filter-shadow-experiments-days", dest="filter_shadow_experiments_days", type=int, default=90)
    parser.add_argument("--funding-rate-snapshots-days", dest="funding_rate_snapshots_days", type=int, default=7)
    parser.add_argument("--open-interest-snapshots-days", dest="open_interest_snapshots_days", type=int, default=7)
    parser.add_argument("--coingecko-top-snapshots-days", dest="coingecko_top_snapshots_days", type=int, default=3)
    parser.add_argument("--universe-feature-snapshots-days", dest="universe_feature_snapshots_days", type=int, default=14)
    parser.add_argument("--universe-snapshots-days", dest="universe_snapshots_days", type=int, default=30)
    parser.add_argument("--universe-scanner-runs-days", dest="universe_scanner_runs_days", type=int, default=30)
    parser.add_argument("--controller-recommendations-days", dest="controller_recommendations_days", type=int, default=30)
    parser.add_argument("--alpha-control-observations-days", dest="alpha_control_observations_days", type=int, default=60)
    parser.add_argument("--market-regimes-days", dest="market_regimes_days", type=int, default=60)
    parser.add_argument("--serenity-evidence-days", dest="serenity_evidence_days", type=int, default=60)
    parser.add_argument("--serenity-run-events-days", dest="serenity_run_events_days", type=int, default=60)
    parser.add_argument("--runtime-sessions-days", dest="runtime_sessions_days", type=int, default=30)
    parser.add_argument("--backtest-runs-days", dest="backtest_runs_days", type=int, default=60)
    parser.add_argument("--market-ohlcv-1m-days", dest="market_ohlcv_1m_days", type=int, default=2)
    parser.add_argument("--market-ohlcv-5m-days", dest="market_ohlcv_5m_days", type=int, default=7)
    parser.add_argument("--market-ohlcv-15m-days", dest="market_ohlcv_15m_days", type=int, default=30)
    parser.add_argument("--market-ohlcv-1h-days", dest="market_ohlcv_1h_days", type=int, default=90)
    parser.add_argument("--market-ohlcv-4h-days", dest="market_ohlcv_4h_days", type=int, default=180)
    parser.add_argument("--market-ohlcv-1d-days", dest="market_ohlcv_1d_days", type=int, default=730)
    # Filesystem prune windows.
    parser.add_argument("--agent-shell-session-logs-days", dest="agent_shell_session_logs_days", type=int, default=14)
    parser.add_argument("--agent-shell-task-notes-completed-days", dest="agent_shell_task_notes_completed_days", type=int, default=7)
    parser.add_argument("--agent-shell-task-notes-failed-days", dest="agent_shell_task_notes_failed_days", type=int, default=30)
    parser.add_argument("--backups-keep-recent", dest="backups_keep_recent", type=int, default=7)
    parser.add_argument("--diagnostics-days", dest="diagnostics_days", type=int, default=14)
    parser.add_argument("--pre-migration-backup-days", dest="pre_migration_backup_days", type=int, default=30)
    return parser


# --- Pre-flight checks -----------------------------------------------------

def _preflight(args: argparse.Namespace, db: sqlite3.Connection, db_file: Path) -> dict[str, Any]:
    """Refuse to touch the DB unless every invariant holds.

    Returns a diagnostic dict; raises ``SystemExit`` on any failure. In dry-run
    mode integrity failures still abort so the user has time to open a rescue
    session before Phase 4.
    """
    report: dict[str, Any] = {}

    # 1) Gateway must not be running.
    try:
        from aletheia.gateway.pidfile import is_running as _is_running
        report["gateway_running"] = bool(_is_running())
    except Exception as exc:  # pragma: no cover - defensive
        report["gateway_running"] = f"probe_failed: {exc}"
    if report["gateway_running"] is True and args.apply:
        raise SystemExit(
            "ABORT: gateway is running. Stop it first with `aletheia gateway stop`."
        )

    # 2) Free disk space > 2 * app.db size.
    size_mb = _db_size_mb(db_file)
    report["app_db_size_mb"] = round(size_mb, 2)
    stat = shutil.disk_usage(db_file.parent)
    free_mb = stat.free / (1024 * 1024)
    report["disk_free_mb"] = round(free_mb, 2)
    if args.apply and free_mb < 2 * size_mb + 512:
        raise SystemExit(
            f"ABORT: free disk {free_mb:.1f} MiB is not > 2 x app.db {size_mb:.1f} MiB + 512 MiB slack."
        )

    # 3) integrity_check + foreign_key_check.
    integrity = db.execute("PRAGMA integrity_check").fetchall()
    report["integrity_check"] = [row[0] for row in integrity]
    if report["integrity_check"] != ["ok"]:
        raise SystemExit(f"ABORT: PRAGMA integrity_check failed: {report['integrity_check']}")
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
    report["foreign_key_check"] = [tuple(row) for row in fk]
    if report["foreign_key_check"]:
        raise SystemExit(
            f"ABORT: PRAGMA foreign_key_check returned {len(report['foreign_key_check'])} rows."
        )

    # 4) Baseline audit-chain + learning row counts. Persist to backups/ .
    baseline = {
        "captured_at": _now_iso(),
        "app_db_size_mb": report["app_db_size_mb"],
        "audit_chain": _row_counts(db, _AUDIT_CHAIN_TABLES),
        "learning": _row_counts(db, _LEARNING_TABLES),
    }
    report["baseline"] = baseline
    backup_dir = _backups_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = backup_dir / f"retention_baseline_{_stamp()}.json"
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")
    report["baseline_path"] = str(baseline_path)

    return report


def _postflight_verify(db: sqlite3.Connection, baseline: dict[str, Any]) -> None:
    """After DELETEs and after VACUUM: audit-chain + learning tables unchanged."""
    for group_key, tables in (
        ("audit_chain", _AUDIT_CHAIN_TABLES),
        ("learning", _LEARNING_TABLES),
    ):
        expected = baseline.get(group_key, {})
        actual = _row_counts(db, tables)
        for table, prior in expected.items():
            now = actual.get(table)
            if now != prior:
                raise SystemExit(
                    f"ABORT: {group_key}.{table} row count changed {prior} -> {now}. "
                    "Restore from ~/.aletheia/app.db.pre-migration-backup NOW."
                )
    integrity = db.execute("PRAGMA integrity_check").fetchall()
    if [row[0] for row in integrity] != ["ok"]:
        raise SystemExit(f"ABORT: post-VACUUM integrity_check failed: {integrity}")
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        raise SystemExit(f"ABORT: post-VACUUM foreign_key_check {len(fk)} rows.")


# --- Archive helpers -------------------------------------------------------

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _backups_dir() -> Path:
    from aletheia.config.loader import get_data_dir
    return Path(get_data_dir()) / "backups"


def _archive_dir(args: argparse.Namespace) -> Path:
    if args.archive_dir is not None:
        return Path(args.archive_dir).expanduser()
    root = _backups_dir()
    return root / f"data_retention_archive_{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def _archive_rows_for(
    db: sqlite3.Connection,
    target: RetentionTarget,
    args: argparse.Namespace,
) -> tuple[int, Path]:
    """Stream rows matching ``target.predicate_sql`` into ``<table>.jsonl.gz``.

    Rows are streamed row-by-row to avoid loading the whole set into memory.
    Column names are captured from the cursor description so schema drift does
    not break the archive.

    If the SELECT itself fails (schema drift — column renamed or table
    missing), the archive is skipped and the caller proceeds with the prune.
    A silent abort mid-apply would leave the DB half-mutated; degrading to
    "archive skipped, prune still runs" is the safer path — the recurring
    scheduler will pick up any leftover rows on its next pass.
    """
    out_dir = _archive_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target.table}.jsonl.gz"

    params = target.predicate_params_factory(args, db)
    try:
        cur = db.execute(
            f"SELECT * FROM {target.table} WHERE {target.predicate_sql}",
            params,
        )
    except sqlite3.OperationalError as exc:
        print(
            f"  WARN: archive SELECT on {target.table} failed ({exc}); "
            "proceeding with prune WITHOUT archive.",
            file=sys.stderr,
        )
        return 0, out_path
    columns = [c[0] for c in cur.description] if cur.description else []

    written = 0
    with gzip.open(out_path, "at", encoding="utf-8") as gz:
        for row in cur:
            record = {col: row[idx] for idx, col in enumerate(columns)}
            gz.write(json.dumps(record, default=_json_default, sort_keys=True))
            gz.write("\n")
            written += 1
    return written, out_path


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


# --- Dry-run estimation ----------------------------------------------------

def _estimate_eligible(
    db: sqlite3.Connection,
    target: RetentionTarget,
    args: argparse.Namespace,
) -> tuple[int, float]:
    """Return ``(rows, est_mb)`` for a target's DELETE predicate."""
    if not _table_exists(db, target.table):
        return (0, 0.0)
    params = target.predicate_params_factory(args, db)
    count_sql = f"SELECT COUNT(*) FROM {target.table} WHERE {target.predicate_sql}"
    try:
        rows = int(db.execute(count_sql, params).fetchone()[0])
    except sqlite3.OperationalError:
        return (0, 0.0)
    if rows == 0:
        return (0, 0.0)
    byte_col = target.byte_col
    if byte_col:
        try:
            byte_sql = (
                f"SELECT COALESCE(SUM(LENGTH({byte_col})), 0) FROM {target.table} "
                f"WHERE {target.predicate_sql}"
            )
            est_bytes = int(db.execute(byte_sql, params).fetchone()[0])
        except sqlite3.OperationalError:
            est_bytes = 0
    else:
        # Rough estimate: 512 bytes/row when we don't know the payload column.
        est_bytes = rows * 512
    return rows, est_bytes / (1024 * 1024)


def _print_plan_header() -> None:
    print(
        f"{'table':38} {'window':44} {'rows':>10} {'est_mb':>10}   fn(archive)"
    )
    print("-" * 130)


def _print_plan_row(
    target: RetentionTarget, rows: int, est_mb: float, window: str
) -> None:
    tag = target.prune_fn + (" [archive]" if target.archive else "")
    print(
        f"{target.table:38} {window:44} {rows:>10d} {est_mb:>10.2f}   {tag}"
    )


def _window_summary(target: RetentionTarget, args: argparse.Namespace) -> str:
    kwargs = target.kwargs_factory(args, sqlite3.connect(":memory:"))  # fake db is fine
    parts = []
    for key, val in kwargs.items():
        if isinstance(val, dict):
            parts.append(",".join(f"{k}={v}" for k, v in val.items()))
        else:
            parts.append(f"{key}={val}")
    return " ".join(parts) if parts else "(default)"


# --- Filesystem prunes -----------------------------------------------------

def _fs_prune_session_logs(args: argparse.Namespace) -> dict[str, Any]:
    """Remove *.jsonl transcripts whose DB session row is older than the window.

    Delegates to ``file_artifacts.prune_session_logs_db_join`` when available so
    the exact same code path drives the recurring nightly cleanup.
    """
    try:
        from aletheia.agent_shell.file_artifacts import prune_session_logs_db_join
    except ImportError:
        return {"skipped": "prune_session_logs_db_join not yet available"}
    count = prune_session_logs_db_join(days=args.agent_shell_session_logs_days)
    return {"prune_session_logs_db_join": int(count or 0)}


def _fs_prune_task_notes(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from aletheia.agent_shell.file_artifacts import prune_task_notes_db_join
    except ImportError:
        return {"skipped": "prune_task_notes_db_join not yet available"}
    count = prune_task_notes_db_join(
        days_completed=args.agent_shell_task_notes_completed_days,
        days_failed=args.agent_shell_task_notes_failed_days,
    )
    return {"prune_task_notes_db_join": int(count or 0)}


def _fs_prune_backups(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from aletheia.maintenance.filesystem_retention import prune_backups
    except ImportError:
        return {"skipped": "prune_backups not yet available"}
    return {"prune_backups": int(prune_backups(keep_recent=args.backups_keep_recent) or 0)}


def _fs_prune_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from aletheia.maintenance.filesystem_retention import prune_diagnostics_dir
    except ImportError:
        return {"skipped": "prune_diagnostics_dir not yet available"}
    return {"prune_diagnostics_dir": int(prune_diagnostics_dir(days=args.diagnostics_days) or 0)}


def _fs_prune_pre_migration_backup(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from aletheia.maintenance.filesystem_retention import prune_pre_migration_backup
    except ImportError:
        return {"skipped": "prune_pre_migration_backup not yet available"}
    return {
        "prune_pre_migration_backup": int(
            prune_pre_migration_backup(days=args.pre_migration_backup_days) or 0
        )
    }


def _fs_prune_tasks_db_agent_task_claims(args: argparse.Namespace) -> dict[str, Any]:
    """Delete expired ``agent_task_claims`` rows from ~/.aletheia/tasks.db.

    tasks.db is a separate SQLite file (see spec Agent 3), not the main app.db.
    Kept in the FS section so it runs even when the main DB deletion has been
    filtered via --tables.
    """
    from aletheia.config.loader import get_data_dir
    tasks_db = Path(get_data_dir()) / "tasks.db"
    if not tasks_db.exists():
        return {"tasks_db_agent_task_claims": 0, "note": "tasks.db missing"}
    conn = sqlite3.connect(str(tasks_db), timeout=30, isolation_level=None)
    try:
        try:
            cur = conn.execute(
                "DELETE FROM agent_task_claims WHERE expires_at IS NOT NULL AND expires_at < ?",
                (_now_iso(),),
            )
        except sqlite3.OperationalError as exc:
            return {"skipped": f"tasks.db.agent_task_claims not available: {exc}"}
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA optimize")
        return {"tasks_db_agent_task_claims": int(cur.rowcount or 0)}
    finally:
        conn.close()


# --- Apply pass ------------------------------------------------------------

def _confirm_or_abort(target: RetentionTarget, rows: int, args: argparse.Namespace) -> None:
    if args.yes or rows == 0:
        return
    prompt = f"Confirm delete {rows} rows from {target.table} [y/N]? "
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    if answer != "y":
        raise SystemExit(f"ABORT: user declined delete for {target.table}.")


def _dispatch_prune(target: RetentionTarget, args: argparse.Namespace, db: sqlite3.Connection) -> int:
    """Call the module-level prune fn. Returns rowcount (0 if fn missing).

    A missing function (attribute lookup miss) WARN-skips — that path exists so
    parallel-agent partial merges do not brick the whole apply pass. A
    ``TypeError`` (kwarg mismatch) is re-raised: silent skips would mask
    correctness bugs like the ones the Phase 3 review found. If the prune fn
    lands with a different keyword layout, the operator sees the failure
    immediately instead of a silent zero-count.
    """
    from aletheia.storage import repositories
    fn = getattr(repositories, target.prune_fn, None)
    if fn is None:
        print(f"  WARN: repositories.{target.prune_fn} not found — skipping {target.table}.",
              file=sys.stderr)
        return 0
    kwargs = target.kwargs_factory(args, db)
    result = fn(**kwargs)
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        return int(sum(v for v in result.values() if isinstance(v, int)))
    return 0


def _retro_strip_message_context(
    db: sqlite3.Connection, days_context: int
) -> dict[str, Any]:
    """Retro-strip ``content.context`` from surviving <days_context> rows.

    Rolling-forward writers (`_seed_branch_workspace` in operator.py + the
    Agent 6 append_message dedup) prevent NEW context blobs from bloating rows,
    but existing <3d rows that already have a persisted context blob remain
    until natural rotation — retention would leave a bump of ~8 MiB against
    the Day-0 app.db target. This bounded UPDATE closes that gap.

    Uses SQLite's ``json_remove`` on ``content`` for ``message_type='context'``
    rows created at or after ``now - days_context``. Follows the was_in_tx
    pattern so it participates in the post-prune invariant check.
    """
    cutoff = _cutoff_iso(days_context)
    result: dict[str, Any] = {"cutoff": cutoff, "days_context": days_context}
    if not _table_exists(db, "agent_shell_messages"):
        result["skipped"] = "agent_shell_messages not present"
        return result
    was_in_tx = db.in_transaction
    try:
        pre = db.execute(
            "SELECT COUNT(*) FROM agent_shell_messages "
            "WHERE message_type='context' AND created_at >= ? "
            "AND content IS NOT NULL AND json_type(content, '$.context') IS NOT NULL",
            (cutoff,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        result["skipped"] = f"content is not JSON or json1 missing: {exc}"
        return result
    eligible = int(pre[0] if pre else 0)
    result["eligible_rows"] = eligible
    if eligible == 0:
        return result
    cur = db.execute(
        "UPDATE agent_shell_messages "
        "SET content = json_remove(content, '$.context') "
        "WHERE message_type='context' AND created_at >= ? "
        "AND content IS NOT NULL AND json_type(content, '$.context') IS NOT NULL",
        (cutoff,),
    )
    if not was_in_tx:
        db.commit()
    result["updated_rows"] = int(cur.rowcount or 0)
    return result


def _run_universe_features_raw_migration() -> dict[str, Any]:
    """Invoke the DROP COLUMN migration for universe_feature_snapshots.raw.

    Runs after the DELETEs and post-DELETE invariant check, before VACUUM so
    the freed pages get reclaimed in the same pass. Idempotent — a second
    invocation is a fast no-op when the column has already been dropped.
    """
    try:
        from aletheia.storage.migration_drop_universe_features_raw import (
            run as _drop_raw_run,
        )
    except ImportError as exc:  # pragma: no cover - defensive
        return {"skipped": f"migration module unavailable: {exc}"}
    try:
        dropped = _drop_raw_run()
    except Exception as exc:  # pragma: no cover - defensive; ALTER can fail
        return {"error": str(exc)}
    return {"raw_column_dropped": bool(dropped)}


def _vacuum_and_optimize(db: sqlite3.Connection) -> dict[str, Any]:
    """Run the sequenced VACUUM + PRAGMA tail from spec.script_design."""
    events: dict[str, Any] = {}
    # BEGIN by making sure no explicit transaction is open (VACUUM requires this).
    if db.in_transaction:
        db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    events["wal_checkpoint"] = "ok"
    integrity = db.execute("PRAGMA integrity_check").fetchall()
    events["integrity_check"] = [row[0] for row in integrity]
    if events["integrity_check"] != ["ok"]:
        raise SystemExit(f"ABORT: pre-VACUUM integrity_check failed: {events['integrity_check']}")
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
    events["auto_vacuum"] = "INCREMENTAL"
    db.execute("VACUUM")
    events["vacuum"] = "ok"
    db.execute("PRAGMA journal_size_limit=67108864")
    events["journal_size_limit"] = 67108864
    db.execute("PRAGMA optimize")
    events["optimize"] = "ok"
    return events


# --- Entry point -----------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    from aletheia.config.loader import get_data_dir
    from aletheia.storage.sqlite import db_path, get_db

    data_dir = Path(get_data_dir())
    db_file = Path(db_path())

    # Ensure the "in dry-run by default" contract: --apply overrides --dry-run.
    if args.apply:
        args.dry_run = False

    print(f"aletheia data-retention backfill  data_dir={data_dir}  db={db_file}")
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'}  "
          f"archive_dir={_archive_dir(args) if any(t.archive for t in _build_plan(args).targets) else '(n/a)'}")

    db = get_db()
    preflight = _preflight(args, db, db_file)
    print("preflight:", json.dumps({k: v for k, v in preflight.items() if k != "baseline"}, indent=2))

    plan = _build_plan(args)
    if args.tables.strip():
        allow = {t.strip() for t in args.tables.split(",") if t.strip()}
        plan.targets = [t for t in plan.targets if t.table in allow]

    _print_plan_header()
    per_table_estimate: list[tuple[RetentionTarget, int, float]] = []
    total_rows = 0
    total_mb = 0.0
    for target in plan.targets:
        rows, est_mb = _estimate_eligible(db, target, args)
        window = _window_summary(target, args)
        _print_plan_row(target, rows, est_mb, window)
        per_table_estimate.append((target, rows, est_mb))
        total_rows += rows
        total_mb += est_mb
    print("-" * 130)
    print(f"{'TOTAL':38} {'':44} {total_rows:>10d} {total_mb:>10.2f}")

    if args.dry_run:
        # Also print FS prune preview using each fn (best-effort — some may return
        # only when actually called; that is acceptable for dry-run).
        print("\nfs prune preview:")
        for fn in plan.fs_prunes:
            print(f"  {fn.__name__}: (skipped in dry-run — will run in --apply)")
        return 0

    # Apply mode -----------------------------------------------------------
    total_pruned: dict[str, int] = {}
    archive_summary: dict[str, dict[str, Any]] = {}
    for target, rows, _est_mb in per_table_estimate:
        if rows == 0:
            total_pruned[target.table] = 0
            continue
        _confirm_or_abort(target, rows, args)
        if target.archive:
            written, path = _archive_rows_for(db, target, args)
            archive_summary[target.table] = {"rows": written, "path": str(path)}
        deleted = _dispatch_prune(target, args, db)
        total_pruned[target.table] = deleted

    # Retro-strip content.context from surviving <days_context> rows so the
    # Day-0 app.db size target is not undermined by pre-guardrail context
    # blobs riding along on rows younger than the messages retention cutoff.
    # Runs before the invariant check so any invariant regression is caught
    # here (json_remove only touches telemetry rows, not audit-chain).
    retro_strip = _retro_strip_message_context(
        db, args.agent_shell_messages_context_days
    )
    print("retro_strip:", json.dumps(retro_strip, indent=2))

    # Post-DELETE, pre-VACUUM invariant.
    _postflight_verify(db, preflight["baseline"])

    # Drop the always-empty universe_feature_snapshots.raw column before
    # VACUUM so the freed bytes get reclaimed in the same pass. Idempotent.
    raw_drop = _run_universe_features_raw_migration()
    print("universe_features_raw_migration:", json.dumps(raw_drop, indent=2))
    # Re-check invariants after the ALTER TABLE — DROP COLUMN rewrites the
    # table so an extra safety belt is worth the millisecond.
    _postflight_verify(db, preflight["baseline"])

    if args.vacuum or args.apply:
        vacuum_events = _vacuum_and_optimize(db)
        print("vacuum:", json.dumps(vacuum_events, indent=2))
        _postflight_verify(db, preflight["baseline"])

    # FS prunes.
    fs_summary: dict[str, Any] = {}
    if not args.skip_fs:
        for fn in plan.fs_prunes:
            try:
                fs_summary[fn.__name__] = fn(args)
            except Exception as exc:  # pragma: no cover - defensive
                fs_summary[fn.__name__] = {"error": str(exc)}

    print("\nsummary:")
    print(json.dumps({
        "pruned_rows": total_pruned,
        "archived": archive_summary,
        "fs": fs_summary,
        "retro_strip": retro_strip,
        "universe_features_raw_migration": raw_drop,
        "app_db_size_mb_after": round(_db_size_mb(db_file), 2),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run(args)


# --- Smoke test: `python -m aletheia.maintenance.data_retention_backfill` --
#
# ``main(None)`` uses sys.argv so CLI flags flow through normally. Bare
# invocation with no args stays safe because ``--apply`` defaults to False
# and ``--dry-run`` defaults to True.
if __name__ == "__main__":  # pragma: no cover - manual smoke test only
    sys.exit(main(None))
