"""Real-path deterministic harness for the Python Operator control loop."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch

from .clock import VirtualClock


class ControlLoopHarness:
    """Own an isolated config, migrated SQLite DB, virtual clock, and operation log."""

    def __init__(
        self,
        root: Path,
        *,
        now: datetime | None = None,
        initial_confidence: str = "medium",
        initial_sizing: float = 200.0,
        kill_switch: bool = False,
        artifact_root: Path | None = None,
        run_id: str | None = None,
        seed: int = 0,
        reproduce: str = "uv run pytest tests/simulation/control_loop/ -q",
    ) -> None:
        import aletheia.config.loader as loader
        from aletheia.config.schema import AletheiaConfig
        from aletheia.storage import sqlite as sqlite_store
        from aletheia.storage.migrations import run_migrations
        from aletheia.storage.sqlite import get_db

        self.root = Path(root)
        default_artifact_root = Path(
            os.getenv("DCLS_ARTIFACT_ROOT", "test-results/control-loop")
        )
        self.artifact_root = Path(artifact_root or default_artifact_root)
        raw_run_id = run_id or self.root.name or "dcls-failure"
        self.run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_run_id).strip("-")
        self.seed = seed
        self.reproduce = reproduce
        self.clock = VirtualClock(now or datetime(2026, 7, 13, 4, tzinfo=timezone.utc))
        self.operations: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []
        self.summaries: list[dict[str, Any]] = []
        self.signal_health = {
            "ohlcv_ready": True,
            "members_with_full_coverage": 8,
            "total_members": 8,
            "signal_win_rate_24h": 50.0,
            "signal_status": "healthy",
        }
        self._sequence = 0
        self._loader = loader
        self._original_data_dir = loader.DATA_DIR
        self._original_config_path = loader.CONFIG_PATH
        protected_data_dirs = {
            (Path(loader._PROJECT_ROOT) / "data").resolve(),
            loader.get_data_dir().resolve(),
        }
        active_connection = sqlite_store._db_conn
        self._original_db_connection = active_connection
        active_db_is_memory = False
        if active_connection is not None:
            active_db_file = active_connection.execute(
                "PRAGMA database_list"
            ).fetchone()[2]
            active_db_is_memory = active_db_file in {"", ":memory:"}
        # Pytest's autouse sandbox points DATA_DIR at tmp_path while SQLite is
        # explicitly in-memory; no active filesystem state exists there. Every
        # file-backed or not-yet-opened runtime directory remains protected.
        if not active_db_is_memory:
            protected_data_dirs.add(Path(self._original_data_dir).resolve())
        resolved_root = self.root.resolve()
        if any(
            resolved_root == protected_dir
            or protected_dir in resolved_root.parents
            for protected_dir in protected_data_dirs
        ):
            raise ValueError(
                "ControlLoopHarness requires an isolated data directory"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        loader.DATA_DIR = self.root
        loader.CONFIG_PATH = self.root / "config.yaml"
        loader._invalidate_config_cache()
        # Detach, rather than close, a caller-owned connection. Pytest supplies
        # an in-memory sandbox that must be restored when this harness closes;
        # the harness still opens and owns a separate file-backed connection.
        sqlite_store._db_conn = None
        run_migrations()

        cfg = AletheiaConfig()
        cfg.runtime.paused = False
        cfg.runtime.kill_switch_active = kill_switch
        cfg.operator.loop_enabled = True
        cfg.agent.permission_mode = "Paper"
        cfg.exchange.paper_execution_mode = "paper_internal"
        cfg.trading.min_confidence_to_trade = initial_confidence
        # DCLS outcomes use explicit zero-cost local fills so the requested
        # +/-1 USDT result is the complete net result, not a hidden fee estimate.
        cfg.trading.slippage_bps = 0.0
        cfg.trading.execution_fee_pct = 0.0
        cfg.risk.max_demo_position_notional_usdt = initial_sizing
        loader.save_config(cfg)
        self.db = get_db()
        self.initial_config = self.safe_config()
        import aletheia.agent_shell.operator_loop as operator_loop
        operator_loop._action_log.clear()

    def __enter__(self) -> "ControlLoopHarness":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc is not None:
                from .artifacts import write_failure_artifacts
                write_failure_artifacts(
                    self,
                    exc,
                    artifact_root=self.artifact_root,
                    run_id=self.run_id,
                    seed=self.seed,
                    reproduce=self.reproduce,
                )
        finally:
            self.close()

    def close(self) -> None:
        from aletheia.storage import sqlite as sqlite_store

        sqlite_store.reset_db()
        self._loader.DATA_DIR = self._original_data_dir
        self._loader.CONFIG_PATH = self._original_config_path
        self._loader._invalidate_config_cache()
        sqlite_store._db_conn = self._original_db_connection

    def load_config(self):
        self._loader._invalidate_config_cache()
        return self._loader.load_config()

    def safe_config(self) -> dict[str, Any]:
        cfg = self.load_config()
        return {
            "runtime": {
                "paused": cfg.runtime.paused,
                "kill_switch_active": cfg.runtime.kill_switch_active,
            },
            "agent": {"permission_mode": cfg.agent.permission_mode},
            "exchange": {"paper_execution_mode": cfg.exchange.paper_execution_mode},
            "operator": {"loop_enabled": cfg.operator.loop_enabled},
            "trading": {
                "min_confidence_to_trade": cfg.trading.min_confidence_to_trade,
                "default_stop_loss_pct": cfg.trading.default_stop_loss_pct,
            },
            "risk": {
                "max_demo_position_notional_usdt":
                    cfg.risk.max_demo_position_notional_usdt,
            },
        }

    def set_controls(
        self, *, confidence: str | None = None, sizing: float | None = None
    ) -> None:
        cfg = self.load_config()
        if confidence is not None:
            cfg.trading.min_confidence_to_trade = confidence
        if sizing is not None:
            cfg.risk.max_demo_position_notional_usdt = sizing
        self._loader.save_config(cfg)
        self._record("set_controls", confidence=confidence, sizing=sizing)

    def change_signal_quality(self, *, win_rate: float, status: str) -> None:
        self.signal_health["signal_win_rate_24h"] = float(win_rate)
        self.signal_health["signal_status"] = status
        self._record("change_signal_quality", win_rate=win_rate, status=status)

    def advance_time(self, hours: float) -> None:
        self.clock.advance(hours=hours)
        self._record("advance_time", hours=hours)

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"dcls-{prefix}-{self._sequence:04d}"

    def _insert_verified_outcome(
        self,
        *,
        won: bool,
        opened_at: datetime,
        closed_at: datetime,
    ) -> None:
        """Insert one complete native paper chain for deterministic simulation."""
        from aletheia.files import jsonl
        from aletheia import notifications
        from aletheia.portfolio import pnl
        from aletheia.portfolio.paper_account import record_position_opened
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import (
            Fill, Opportunity, Order, Position, RiskDecision, TradeProposal,
        )

        opportunity = Opportunity(
            id=self._next_id("outcome-opportunity"),
            symbol="BTC/USDT",
            market="spot",
            direction_bias="long",
            source_agent="dcls",
            scanner_family="dcls",
            status="closed",
            created_at=opened_at.isoformat(),
        )
        repo.insert_opportunity(opportunity)
        proposal = TradeProposal(
            id=self._next_id("proposal"),
            opportunity_id=opportunity.id,
            strategy_id="dcls",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60_000.0,
            execution_mode="paper_internal",
            status="executed",
            created_at=opened_at.isoformat(),
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            id=self._next_id("risk"),
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
            proposal_execution_mode="paper_internal",
            decision_role="authoritative",
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            created_at=opened_at.isoformat(),
        )
        repo.insert_risk_decision(decision)
        position_id = self._next_id("position")
        order = Order(
            id=self._next_id("order"),
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=0.001,
            price=60_000.0,
            execution_mode="paper_internal",
            position_id=position_id,
            status="filled",
            created_at=opened_at.isoformat(),
            filled_at=opened_at.isoformat(),
        )
        repo.insert_order(order)
        repo.insert_fill(Fill(
            id=self._next_id("fill"),
            order_id=order.id,
            fill_price=60_000.0,
            fill_quantity=0.001,
            fee=0.0,
            filled_at=opened_at.isoformat(),
        ))
        realized_pnl = 1.0 if won else -1.0
        exit_price = 60_000.0 + realized_pnl / 0.001
        repo.insert_position(Position(
            id=position_id,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            entry_price=60_000.0,
            current_price=60_000.0,
            execution_mode="paper_internal",
            strategy_id="dcls",
            proposal_id=proposal.id,
            order_id=order.id,
            opportunity_id=opportunity.id,
            status="open",
            entry_price_source="local_fill",
            entry_fill_verified=True,
            entry_fee=0.0,
            funding_cost=0.0,
            funding_source="not_applicable",
            opened_at=opened_at.isoformat(),
        ))
        record_position_opened(position_id)
        opened_iso = opened_at.isoformat()
        self.db.execute(
            "UPDATE paper_account_events SET created_at = ? "
            "WHERE action = 'position_opened' AND position_id = ?",
            (opened_iso, position_id),
        )
        self.db.execute(
            "UPDATE paper_account_events SET created_at = ? "
            "WHERE action = 'initial_funding' "
            "AND datetime(created_at) > datetime(?)",
            (opened_iso, opened_iso),
        )
        close_order = Order(
            id=self._next_id("close-order"),
            proposal_id=proposal.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type="market",
            quantity=0.001,
            price=exit_price,
            execution_mode="paper_internal",
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason="simulation_verified_exit",
            status="filled",
            created_at=closed_at.isoformat(),
            filled_at=closed_at.isoformat(),
        )
        close_fill = Fill(
            id=self._next_id("close-fill"),
            order_id=close_order.id,
            fill_price=exit_price,
            fill_quantity=0.001,
            fee=0.0,
            filled_at=closed_at.isoformat(),
        )

        def _materialize_close() -> None:
            repo.insert_order(close_order)
            repo.insert_fill(close_fill)

        # The database audit is the durable authority. Suppress only secondary
        # JSONL/notification sinks so a deterministic harness never emits local
        # desktop/network side effects while exercising the real close path.
        with ExitStack() as stack:
            stack.enter_context(patch.object(pnl, "_btc_price_at_close", return_value=60_000.0))
            stack.enter_context(patch.object(jsonl, "write_audit", return_value=None))
            stack.enter_context(patch.object(notifications, "notify_operator", return_value=None))
            result = pnl.close_position(
                position_id,
                exit_price=exit_price,
                reason="simulation_verified_exit",
                actual_exit_fee=0.0,
                closed_at=closed_at.isoformat(),
                materialize_close=_materialize_close,
                actor="dcls",
                source="control_loop_harness",
            )
        if not result.get("outcome_eligible"):
            raise RuntimeError(
                "DCLS close did not satisfy Outcome eligibility: "
                f"{result.get('outcome_exclusion_reason')}"
            )

        # Paper-account/audit/outbox factories use wall time. Align their
        # timestamps with the virtual fill so replayed age/window semantics are
        # internally ordered without weakening the production close path.
        closed_iso = closed_at.isoformat()
        with repo.transaction(immediate=True):
            self.db.execute(
                "UPDATE paper_account_events SET created_at = ? "
                "WHERE action = 'position_closed' AND position_id = ?",
                (closed_iso, position_id),
            )
            self.db.execute(
                "UPDATE audit_events SET timestamp = ? "
                "WHERE actor = 'dcls' AND source = 'control_loop_harness' "
                "AND entity_type = 'position' AND entity_id = ?",
                (closed_iso, position_id),
            )
            self.db.execute(
                "UPDATE outcome_hook_jobs SET created_at = ?, updated_at = ?, "
                "next_attempt_at = ?, "
                "completed_at = CASE WHEN completed_at IS NULL THEN NULL ELSE ? END "
                "WHERE position_id = ?",
                (closed_iso, closed_iso, closed_iso, closed_iso, position_id),
            )

    def _clear_dcls_trade_performance(self) -> None:
        """Delete only the trade facts owned by this deterministic harness."""
        from aletheia.storage import repositories as repo

        position_like = "dcls-position-%"
        order_likes = ("dcls-order-%", "dcls-close-order-%")
        with repo.transaction(immediate=True):
            self.db.execute(
                "DELETE FROM outcome_hook_jobs WHERE position_id LIKE ?",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM paper_account_events WHERE position_id LIKE ?",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM audit_events "
                "WHERE actor = 'dcls' AND source = 'control_loop_harness' "
                "AND entity_type = 'position' AND entity_id LIKE ?",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM position_disappearance_observations "
                "WHERE candidate_id IN ("
                "SELECT id FROM position_disappearance_candidates "
                "WHERE position_id LIKE ?)",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM position_disappearance_candidates "
                "WHERE position_id LIKE ?",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM serenity_post_mortems WHERE position_id LIKE ?",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM fills WHERE order_id IN ("
                "SELECT id FROM orders WHERE id LIKE ? OR id LIKE ?)",
                order_likes,
            )
            self.db.execute(
                "DELETE FROM positions WHERE id LIKE ?",
                (position_like,),
            )
            self.db.execute(
                "DELETE FROM orders WHERE id LIKE ? OR id LIKE ?",
                order_likes,
            )
            self.db.execute(
                "DELETE FROM risk_decisions WHERE id LIKE 'dcls-risk-%'"
            )
            self.db.execute(
                "DELETE FROM trade_proposals WHERE id LIKE 'dcls-proposal-%'"
            )
            self.db.execute(
                "DELETE FROM opportunities "
                "WHERE id LIKE 'dcls-outcome-opportunity-%'"
            )

    def _rebase_paper_ledger_by_event_time(self) -> None:
        """Restore cash-snapshot continuity in virtual event-time order.

        Replacement outcomes are appended after retained external events even
        when their virtual timestamps are earlier. Recompute every derived cash
        snapshot only after all replacement events exist, ordered exactly as the
        timestamp-based ledger readers expose them. Event identities, amounts,
        and timestamps remain unchanged. The caller owns the outer transaction
        so an invalid retained history rolls the whole replacement back.
        """
        balance = 0.0
        events = self.db.execute(
            "SELECT id, amount_usdt FROM paper_account_events "
            "ORDER BY datetime(created_at) ASC, rowid ASC"
        ).fetchall()
        for event in events:
            before = round(balance, 8)
            after = round(before + float(event["amount_usdt"]), 8)
            if after < -1e-8:
                raise RuntimeError(
                    "cannot replace DCLS outcomes: retained paper ledger "
                    "would have a negative cash balance"
                )
            self.db.execute(
                "UPDATE paper_account_events "
                "SET cash_balance_before_usdt = ?, cash_balance_after_usdt = ? "
                "WHERE id = ?",
                (before, max(0.0, after), event["id"]),
            )
            balance = after

    def change_trade_performance(
        self, *, win_rate: float, total: int, hours_since_last_fill: float
    ) -> None:
        from aletheia.storage import repositories as repo

        # Replace only DCLS-owned trade facts; candidates and any independently
        # seeded rows remain visible to later state-machine steps.
        latest_close = self.clock.now() - timedelta(hours=hours_since_last_fill)
        wins = max(0, min(total, round(total * win_rate / 100)))
        with repo.transaction(immediate=True):
            self._clear_dcls_trade_performance()
            # Insert oldest-to-newest so DCLS-owned events are internally ordered.
            for index in reversed(range(total)):
                closed_at = latest_close - timedelta(minutes=index)
                self._insert_verified_outcome(
                    won=index < wins,
                    opened_at=closed_at - timedelta(seconds=30),
                    closed_at=closed_at,
                )
            self._rebase_paper_ledger_by_event_time()
        self._record(
            "change_trade_performance", win_rate=win_rate, total=total,
            hours_since_last_fill=hours_since_last_fill,
        )

    def record_outcome(self, *, won: bool) -> None:
        from aletheia.storage import repositories as repo

        closed_at = self.clock.now()
        with repo.transaction(immediate=True):
            self._insert_verified_outcome(
                won=won,
                opened_at=closed_at - timedelta(hours=1),
                closed_at=closed_at,
            )
            self._rebase_paper_ledger_by_event_time()
        self._record("record_outcome", won=won)

    def seed_candidates(self, *, high: int, medium: int) -> None:
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import Opportunity
        # Candidate refresh must not delete the closed Opportunity rows that
        # prove the simulated trade Outcomes' attribution chain.
        self.db.execute(
            "DELETE FROM opportunities "
            "WHERE id LIKE 'dcls-high-candidate-%' "
            "OR id LIKE 'dcls-medium-candidate-%'"
        )
        self.db.commit()
        for confidence, count in (("high", high), ("medium", medium)):
            for index in range(count):
                repo.insert_opportunity(Opportunity(
                    id=self._next_id(f"{confidence}-candidate"),
                    symbol=f"DCLS{confidence[0].upper()}{index}/USDT", market="spot",
                    direction_bias="long", thesis="deterministic control-loop candidate",
                    confidence=confidence, confidence_score=80 if confidence == "high" else 55,
                    source_agent="dcls", scanner_family="dcls",
                    created_at=(self.clock.now() - timedelta(minutes=index)).isoformat(),
                ))
        self._record("seed_candidates", high=high, medium=medium)

    def _clocked_datetime(self):
        clock = self.clock

        class ClockedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = clock.now()
                return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)

        return ClockedDateTime

    def run_operator_cycle(self, *, trigger: str = "dcls") -> dict[str, Any]:
        import aletheia.agent_shell.operator_loop as operator_loop
        import aletheia.agent_shell.trading_context as trading_context
        with ExitStack() as stack:
            stack.enter_context(patch.object(operator_loop, "datetime", self._clocked_datetime()))
            stack.enter_context(patch.object(
                trading_context, "_get_data_health_summary",
                return_value=dict(self.signal_health),
            ))
            summary = operator_loop.run_operator_loop(trigger=trigger)
        self.summaries.append(summary)
        self._record("run_operator_cycle", trigger=trigger, summary=summary)
        return summary

    def run_candidate_filter(self) -> list[dict[str, Any]]:
        from aletheia.agents.paper_trader_ranker import build_paper_candidates
        candidates = build_paper_candidates(limit=100)
        self._record("run_candidate_filter", candidate_ids=[c.get("id") for c in candidates])
        return candidates

    def restart(self) -> None:
        from aletheia.storage.sqlite import get_db, reset_db
        reset_db()
        self._loader._invalidate_config_cache()
        self.db = get_db()
        self._record("restart")

    def record_result(self) -> dict[str, Any]:
        result = self.canonical_state()
        self._record("record_result", result=result)
        return result

    def _record(self, operation: str, **detail: Any) -> None:
        item = {"operation": operation, "at": self.clock.now().isoformat(), **detail}
        self.operations.append({"operation": operation, **{
            key: value for key, value in detail.items() if key != "summary"
        }})
        self.timeline.append(item)

    def canonical_state(self) -> dict[str, Any]:
        audit = [dict(row) for row in self.db.execute(
            "SELECT actor, event_type, entity_id, before_state, after_state, message "
            "FROM audit_events WHERE actor='operator_loop' ORDER BY rowid"
        ).fetchall()]
        summaries = []
        for summary in self.summaries:
            summaries.append({
                "assessment": summary.get("assessment"),
                "results": summary.get("results"),
                "actions_decided": summary.get("actions_decided"),
            })
        return {"config": self.safe_config(), "audit": audit, "summaries": summaries}

    def digest(self) -> str:
        payload = json.dumps(self.canonical_state(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
