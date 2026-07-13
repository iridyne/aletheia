"""Real-path deterministic harness for the Python Operator control loop."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
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
    ) -> None:
        import aletheia.config.loader as loader
        from aletheia.config.schema import AletheiaConfig
        from aletheia.storage.migrations import run_migrations
        from aletheia.storage.sqlite import get_db, reset_db

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
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
        loader.DATA_DIR = self.root
        loader.CONFIG_PATH = self.root / "config.yaml"
        loader._invalidate_config_cache()
        reset_db()
        run_migrations()

        cfg = AletheiaConfig()
        cfg.runtime.paused = False
        cfg.runtime.kill_switch_active = kill_switch
        cfg.operator.loop_enabled = True
        cfg.agent.permission_mode = "Paper"
        cfg.exchange.paper_execution_mode = "paper_internal"
        cfg.trading.min_confidence_to_trade = initial_confidence
        cfg.risk.max_demo_position_notional_usdt = initial_sizing
        loader.save_config(cfg)
        self.db = get_db()
        self.initial_config = self.safe_config()
        import aletheia.agent_shell.operator_loop as operator_loop
        operator_loop._action_log.clear()

    def __enter__(self) -> "ControlLoopHarness":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        from aletheia.storage.sqlite import reset_db
        reset_db()
        self._loader.DATA_DIR = self._original_data_dir
        self._loader.CONFIG_PATH = self._original_config_path
        self._loader._invalidate_config_cache()

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

    def change_trade_performance(
        self, *, win_rate: float, total: int, hours_since_last_fill: float
    ) -> None:
        from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import (
            Fill, Order, Position, RiskDecision, TradeProposal,
        )

        for table in ("fills", "orders", "risk_decisions", "trade_proposals", "positions"):
            self.db.execute(f"DELETE FROM {table}")
        self.db.commit()
        filled_at = self.clock.now() - timedelta(hours=hours_since_last_fill)
        proposal = TradeProposal(
            id=self._next_id("proposal"), symbol="BTC/USDT", market_type="spot",
            direction="long", quantity=0.001, execution_mode="paper_internal",
            status="executed", created_at=filled_at.isoformat(),
        )
        decision = RiskDecision(
            id=self._next_id("risk"), proposal_id=proposal.id, approved=True,
            allowed_execution_mode="paper_internal",
            proposal_execution_mode="paper_internal", created_at=filled_at.isoformat(),
        )
        order = Order(
            id=self._next_id("order"), proposal_id=proposal.id,
            risk_decision_id=decision.id, symbol="BTC/USDT", market_type="spot",
            direction="long", order_type="market", quantity=0.001,
            execution_mode="paper_internal", status="filled",
            created_at=filled_at.isoformat(), filled_at=filled_at.isoformat(),
        )
        repo.insert_trade_proposal(proposal)
        repo.insert_risk_decision(decision)
        repo.insert_order(order)
        repo.insert_fill(Fill(
            id=self._next_id("fill"), order_id=order.id, fill_price=60_000.0,
            fill_quantity=0.001, filled_at=filled_at.isoformat(),
        ))
        wins = max(0, min(total, round(total * win_rate / 100)))
        for index in range(total):
            closed_at = self.clock.now() - timedelta(minutes=index + 1)
            repo.insert_position(Position(
                id=self._next_id("position"), symbol="BTC/USDT", market_type="spot",
                direction="long", quantity=0.01, entry_price=60_000.0,
                realized_pnl=1.0 if index < wins else -1.0,
                execution_mode="paper_internal", strategy_id="dcls",
                status="closed", close_reason="simulation_verified_exit",
                close_category="strategy", exit_price_source="local_fill",
                exit_fill_verified=True, outcome_eligible=True,
                outcome_exclusion_reason="eligible",
                outcome_semantics_version=OUTCOME_SEMANTICS_VERSION,
                opened_at=(closed_at - timedelta(hours=1)).isoformat(),
                closed_at=closed_at.isoformat(),
            ))
        self._record(
            "change_trade_performance", win_rate=win_rate, total=total,
            hours_since_last_fill=hours_since_last_fill,
        )

    def record_outcome(self, *, won: bool) -> None:
        from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import Position
        closed_at = self.clock.now()
        repo.insert_position(Position(
            id=self._next_id("position"), symbol="BTC/USDT", market_type="spot",
            direction="long", quantity=0.01, entry_price=60_000.0,
            realized_pnl=1.0 if won else -1.0, execution_mode="paper_internal",
            strategy_id="dcls", status="closed", close_reason="simulation_verified_exit",
            close_category="strategy", exit_price_source="local_fill",
            exit_fill_verified=True, outcome_eligible=True,
            outcome_exclusion_reason="eligible",
            outcome_semantics_version=OUTCOME_SEMANTICS_VERSION,
            opened_at=(closed_at - timedelta(hours=1)).isoformat(),
            closed_at=closed_at.isoformat(),
        ))
        self._record("record_outcome", won=won)

    def seed_candidates(self, *, high: int, medium: int) -> None:
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import Opportunity
        self.db.execute("DELETE FROM opportunities")
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
