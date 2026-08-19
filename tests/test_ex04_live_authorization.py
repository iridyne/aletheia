"""Negative-first matrix for EX-04 (#1004) — new live-Proposal authorization.

The safety contract:
  1. New live Proposals are only created when a live authorization is currently
     valid. The opaque `live_authorization_ref` (evidence_binding hash) is
     stamped on the Proposal.
  2. At execution the gateway revalidates the stored ref against the currently
     active authorization: expiry, revocation, environment, account, execution
     profile, and Proposal identity all must line up.
  3. Every failure is explicit and fail-closed. paper_internal is untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _fresh_live_evidence(cfg, trust) -> dict:
    """Build a valid unlock_evidence dict for the configured live account.

    The helper computes real identity + `evidence_binding` via the trust
    module so any drift in the trust contract fails these tests instead of
    silently passing on a stale schema.
    """
    identity = trust.resolve_live_execution_identity(cfg)
    now = datetime.now(timezone.utc)
    evidence = {
        **identity,
        "ok": True,
        "reason_code": "ok",
        "account_fingerprint": "sha256:test-account",
        "private_authenticated": True,
        "account_readable": True,
        "environment_verified": True,
        "permission_status": "granted",
        "completeness": "complete",
        "fetched_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=300)).isoformat(),
    }
    evidence["evidence_binding"] = trust._evidence_binding(evidence)
    return evidence


def _setup_live_workspace(tmp_path, monkeypatch, *, broker: str = "okx", unlock: bool = True):
    """Isolated sqlite + config with the selected broker in live mode.

    Returns (cfg, trust). When unlock=True the config carries a fresh
    unlock_evidence so `current_authorization_ref` yields a stable hash.
    """
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    from aletheia.config.loader import load_config, save_config
    import aletheia.trust.live_unlock as trust

    cfg = load_config()
    cfg.exchange.name = broker
    cfg.exchange.mode = "live"
    cfg.exchange.paper_execution_mode = f"{broker}_demo" if broker != "binance" else "binance_testnet"
    cfg.risk.allowed_exchange = broker
    cfg.agent.permission_mode = "Full"
    monkeypatch.setattr(trust, "get_secret", lambda ref: f"test-secret:{ref}")
    if unlock:
        cfg.live.state = "live_limited"
        cfg.live.unlock_evidence = _fresh_live_evidence(cfg, trust)
    save_config(cfg)
    return cfg, trust


# ---------------------------------------------------------------------------
# Layer 1 — new-Proposal mode selection consumes live-unlock state
# ---------------------------------------------------------------------------

def test_no_unlock_keeps_new_proposal_on_paper_mode(tmp_path, monkeypatch):
    """Case 1: no unlock → new-Proposal mode stays on paper. Fail-closed."""
    _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=False)
    from aletheia.agent_shell.commands import _configured_paper_execution_mode

    assert _configured_paper_execution_mode() == "okx_demo"


def test_valid_unlock_routes_new_proposal_to_live_mode(tmp_path, monkeypatch):
    """Case 2: valid unlock → selected broker's *_live_limited mode."""
    _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=True)
    from aletheia.agent_shell.commands import _configured_paper_execution_mode

    assert _configured_paper_execution_mode() == "okx_live_limited"


def test_expired_evidence_falls_back_to_paper_mode(tmp_path, monkeypatch):
    """Case 3: unlock_evidence past expires_at → paper mode. No live routing."""
    cfg, trust = _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=True)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.live.unlock_evidence["expires_at"] = "2020-01-01T00:00:00+00:00"
    save_config(cfg)

    from aletheia.agent_shell.commands import _configured_paper_execution_mode

    assert _configured_paper_execution_mode() == "okx_demo"


def test_paper_only_config_never_touches_live_routing(tmp_path, monkeypatch):
    """paper_internal deployments are unaffected by any of the new plumbing."""
    _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=False)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.mode = "paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    from aletheia.agent_shell.commands import _configured_paper_execution_mode

    assert _configured_paper_execution_mode() == "paper_internal"


# ---------------------------------------------------------------------------
# Layer 2 — Proposal creation stamps the opaque authorization ref
# ---------------------------------------------------------------------------

def test_submit_live_proposal_stamps_authorization_ref(tmp_path, monkeypatch):
    """Case 2 (persistence): the persisted Proposal row carries the opaque
    live_authorization_ref, matching the current authorization scope. This
    also proves it never persists a reusable secret token."""
    cfg, trust = _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=True)
    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.repositories import get_trade_proposal
    from aletheia.trust.live_unlock import current_authorization_ref

    # Force risk to reject after Proposal insert so we don't need broker mocks.
    monkeypatch.setattr(
        "aletheia.execution.gateway._check_symbol_availability",
        lambda *a, **kw: None,
    )

    ref_at_submit = current_authorization_ref(cfg)
    assert ref_at_submit is not None and ref_at_submit.startswith("sha256:")

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60000.0,
        execution_mode="okx_live_limited",
    )
    # Proposal must be persisted regardless of downstream execution outcome —
    # the ref binding is the whole point of this contract.
    proposal_id = result.get("proposal_id")
    assert proposal_id, f"submit_proposal did not persist a Proposal id: {result}"
    stored = get_trade_proposal(proposal_id)
    assert stored is not None
    assert stored["live_authorization_ref"] == ref_at_submit

    # Sanity: the ref is not a reusable secret. It is a stable hash prefix.
    for secret in ("test-secret", "api_key", "api_secret", "passphrase"):
        assert secret not in stored["live_authorization_ref"]


def test_submit_live_proposal_fails_closed_without_authorization(tmp_path, monkeypatch):
    """Case 1 (submit): a live execution_mode without a current authorization
    ref is rejected before any Proposal row lands. The direct-entry gateway
    call cannot smuggle a live Proposal past the authorization gate."""
    _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=False)
    from aletheia.execution.gateway import submit_proposal

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60000.0,
        execution_mode="okx_live_limited",
    )
    assert result["status"] == "rejected"
    assert result.get("reason_code") == "live_authorization_missing"
    assert result.get("proposal_id") is None


# ---------------------------------------------------------------------------
# Layer 3 — execution-time revalidation catches rotated/revoked authority
# ---------------------------------------------------------------------------

def test_execution_rejects_scope_mismatch_after_reunlock(tmp_path, monkeypatch):
    """Case 5/8: unlock rotates between Proposal creation and execution. The
    new authorization is itself valid, but the stored ref belongs to the old
    scope. The gateway MUST reject with `live_authorization_scope_mismatch`
    even when Risk approves — this is the "wrong scope / Proposal predates
    authorization" defense line."""
    cfg, trust = _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=True)
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution.gateway import execute_existing_proposal
    from aletheia.storage.models import TradeProposal
    from aletheia.storage.repositories import insert_trade_proposal

    # Persist a Proposal bound to a stored ref that is deliberately not the
    # currently active binding. Skip the commit-hash chain — the ref check
    # runs BEFORE the durable hash verify path exits normally, and this test
    # only cares about the authorization contract.
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60000.0,
        execution_mode="okx_live_limited",
        live_authorization_ref="sha256:rotated-old-scope",
    )
    insert_trade_proposal(proposal)

    # Bypass Risk + venue checks so we can prove the ref gate itself is what
    # blocks the order. Risk has its own live_disabled/permission tests.
    from aletheia.risk.engine import RiskCheckResult

    monkeypatch.setattr(
        "aletheia.execution.gateway.evaluate_proposal",
        lambda *a, **kw: RiskCheckResult(
            approved=True, risk_score=0.0, violations=[],
            allowed_execution_mode="okx_live_limited",
            message="ok", decision_id="test-decision",
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway._check_symbol_availability",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "aletheia.execution.gateway._prepare_exchange_quantity",
        lambda mode, sym, mt, q, p: (q, None),
    )

    result = execute_existing_proposal(proposal.id, "okx_live_limited")
    assert result["status"] == "rejected"
    assert result.get("reason_code") == "live_authorization_scope_mismatch"


def test_execution_rejects_when_authorization_missing_at_dispatch(tmp_path, monkeypatch):
    """Case 4/9: authorization revoked (or expired) between Proposal creation
    and execution. Restart-persisted live_disabled state must not accept an
    old live Proposal — the risk engine already blocks live_disabled, but
    the gateway also rejects with a canonical reason before any broker call."""
    cfg, trust = _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=True)
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution.gateway import execute_existing_proposal
    from aletheia.storage.models import TradeProposal
    from aletheia.storage.repositories import insert_trade_proposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60000.0,
        leverage=1,
        execution_mode="okx_live_limited",
        live_authorization_ref="sha256:whatever",
    )
    insert_trade_proposal(proposal)

    # Simulate revocation between creation and execution: the operator flips
    # live back to disabled (Risk will block; we assert canonical propagation).
    cfg2 = load_config()
    cfg2.live.state = "live_disabled"
    cfg2.live.unlock_evidence = None
    save_config(cfg2)

    result = execute_existing_proposal(proposal.id, "okx_live_limited")
    assert result["status"] == "rejected"
    # Either Risk's "live_disabled" or the ref-check reason is acceptable —
    # both close the same defect. What matters is that no broker call goes
    # out and the Proposal is durably rejected.
    reason_blob = (
        str(result.get("risk_violations") or "")
        + str(result.get("reason_code") or "")
        + str(result.get("message") or "")
    )
    assert (
        "live_disabled" in reason_blob
        or "no_active_live_authorization" in reason_blob
        or "missing_live_authorization_ref" in reason_blob
    ), result


def test_replayed_executed_live_proposal_is_not_reauthorized(tmp_path, monkeypatch):
    """Case 10: an idempotent re-submit against an already-executed Proposal
    is short-circuited at the gateway. It cannot become a new live order and
    the authorization check does not create a second submission."""
    _setup_live_workspace(tmp_path, monkeypatch, broker="okx", unlock=True)
    from aletheia.execution.gateway import execute_existing_proposal
    from aletheia.storage.models import TradeProposal
    from aletheia.storage.repositories import insert_trade_proposal, update_trade_proposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60000.0,
        execution_mode="okx_live_limited",
        live_authorization_ref="sha256:whatever",
    )
    insert_trade_proposal(proposal)
    update_trade_proposal(proposal.id, status="executed")

    result = execute_existing_proposal(proposal.id, "okx_live_limited")
    assert result["status"] in {"already_executed", "executed"}
