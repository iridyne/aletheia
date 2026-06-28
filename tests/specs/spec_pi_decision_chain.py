"""Spec: PI Agent decision-chain fixes (Round 2 PI-agent audit, 2026-06-28).

Locks the two PI decision-chain fixes made during the PI-agent SDD audit
(see docs/issues/PI_AGENT_SDD_AUDIT_2026-06-28.md):

  PI-S01 (A5) — the pi_trading_decision scheduler job is a ~15min safety-net
  fallback, DECOUPLED from pi_daemon.heartbeat_sec. The old code computed its
  interval as `heartbeat_sec / 60`, which (with the 60s default) fired it every
  1 minute instead of the contracted ~15min.

  PI-S02 (E1) — the guard chain on the PI proposal path is ADVISORY (pre-risk
  observability), not a hard block. A guard "block" verdict must NOT silently
  drop the PI's decision (`return None`); the verdict is surfaced in the proposal
  rationale and the proposal flows to the Risk Engine, which is the real gate.

Structural (L2a) checks — they assert the fix is present in source and guard
against a regression that silently reverts either behavior.
"""
from __future__ import annotations

import ast
import pathlib

from tests.specs.spec_meta import SpecClaim, spec_claim


def _project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (_project_root() / rel).read_text(encoding="utf-8")


@spec_claim(
    SpecClaim("PI-S01", "agent_contracts", "§pi_trading_decision cadence", "L2a",
              "pi_trading_decision scheduler fallback interval is ~15min and is NOT coupled "
              "to pi_daemon.heartbeat_sec (decoupling fix A5)"),
)
def test_pi_trading_decision_fallback_decoupled_from_heartbeat():
    src = _src("aletheia/agent_runtime/scheduler.py")
    # The fallback interval must be a fixed ~15min, not derived from heartbeat_sec.
    assert "_pi_fallback_min = 15" in src, (
        "pi_trading_decision fallback interval is not the fixed ~15min safety-net (A5)"
    )
    # The old coupling (heartbeat_sec / 60) must be gone from the job-interval computation.
    assert "cfg.pi_daemon.heartbeat_sec / 60" not in src, (
        "pi_trading_decision interval still coupled to heartbeat_sec — fires far more often "
        "than the contracted ~15min (A5 regression)"
    )


@spec_claim(
    SpecClaim("PI-S02", "agent_contracts", "§guard chain advisory", "L2a",
              "guard chain on the PI proposal path is advisory: a block verdict is logged + "
              "surfaced in rationale but does NOT early-return None; Risk Engine is the gate"),
)
def test_guard_chain_is_advisory_not_hard_block():
    src = _src("aletheia/agent_shell/trading_decision.py")
    # New advisory behavior present.
    assert "guard_chain advisory (not blocking" in src, (
        "guard advisory log marker missing — guard may still hard-block the PI decision"
    )
    # Guard verdict must still be surfaced for observability/audit.
    assert '"guards": guard_summary' in src, (
        "guard_summary no longer attached to the proposal rationale (lost observability)"
    )
    # The old hard-block path must be gone: locate submit_decision_as_proposal and
    # assert no `return None` follows a `guard_summary["blocks"]` test.
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "submit_decision_as_proposal"),
        None,
    )
    assert fn is not None, "submit_decision_as_proposal not found"
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            cond = ast.unparse(node.test)
            if "guard_summary" in cond and "blocks" in cond:
                body = ast.unparse(node.body)
                assert "return None" not in body, (
                    "guard block branch still hard-returns None — not advisory (E1 regression)"
                )


@spec_claim(
    SpecClaim("PI-S03", "agent_contracts", "§operator_loop ⇄ TS convergence", "L2a",
              "Python operator_loop.decide_actions no longer EMITS the operator actions that "
              "converged to the TS operator-shell (retire_strategy / demote_strategy / "
              "request_backfill); it keeps only dynamic config tuning. Prevents the dead-write / "
              "no-op-stub mechanisms from re-appearing as a parallel operator path"),
)
def test_operator_loop_converged_actions_not_emitted():
    src = _src("aletheia/agent_shell/operator_loop.py")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "decide_actions"),
        None,
    )
    assert fn is not None, "decide_actions not found"
    # Collect every action_type=... string literal produced by decide_actions.
    emitted: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg == "action_type":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                emitted.add(node.value.value)
    converged = {"retire_strategy", "demote_strategy", "request_backfill"}
    leaked = converged & emitted
    assert not leaked, (
        f"decide_actions still emits converged-to-TS actions {leaked} — these are owned by "
        "the TS operator-shell now (retire/demote via command registry, backfill via Auto "
        "Repair). Python operator_loop must keep only config tuning (owner decision 2026-06-28)."
    )
    # Sanity: it must still emit the RETAINED config-tuning actions (not over-pruned).
    assert "tighten_confidence" in emitted and "loosen_confidence" in emitted, (
        "config-tuning actions were over-pruned — those are the ONE thing Python keeps"
    )


@spec_claim(
    SpecClaim("PI-S04", "promotion_gates_contract", "§4 PI approval gate", "L2b",
              "promotion to paper_running requires a recommend_promote PiDecision: blocked when "
              "absent, allowed when present (the PI/operator approval gate is the single chokepoint)"),
)
def test_recommend_promote_gates_paper_running(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import StrategyRecord, PiDecision
    from aletheia.storage.repositories import insert_strategy, insert_pi_decision
    from aletheia.trading.strategy_lifecycle import promote_strategy

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    strat = StrategyRecord(name="GateTest", state="paper_candidate")
    insert_strategy(strat)

    # No recommend_promote on record → promotion to paper_running is BLOCKED.
    blocked = promote_strategy(strat.id, "paper_running")
    assert blocked.get("success") is False, "paper_running promotion must be gated"
    assert "approval" in (blocked.get("error", "").lower()) or "recommend_promote" in blocked.get("error", ""), (
        f"expected PI-approval gate error, got: {blocked.get('error')!r}"
    )

    # With a recommend_promote PiDecision (proposal_id == strategy_id) → gate passes.
    insert_pi_decision(PiDecision(
        proposal_id=strat.id, action="recommend_promote", symbol="BTC/USDT", direction="long",
    ))
    allowed = promote_strategy(strat.id, "paper_running")
    assert allowed.get("success") is True, (
        f"with recommend_promote present, paper_running promotion must pass the gate; got {allowed}"
    )
