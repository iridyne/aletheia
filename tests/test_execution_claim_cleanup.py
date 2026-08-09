"""Tests for execution claim sweeper.

A crashed worker can leave a proposal_execution_claims row that strands the
proposal. The sweeper releases expired claims for proposals in a terminal stuck
state (submission_unknown with no Order) so reconciliation can proceed.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

_REPO = "aletheia.storage.repositories"
_SCHED = "aletheia.agent_runtime.scheduler"


def _make_claim(proposal_id="prop_1", lease_expires_at=None):
    if lease_expires_at is None:
        lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    return {
        "proposal_id": proposal_id,
        "owner_token": "dead_owner_token",
        "claimed_at": (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat(),
        "lease_expires_at": lease_expires_at,
    }


def _patch_sweeper(**overrides):
    """Build the common patch context for sweeper tests."""
    patches = {
        "list": patch(f"{_REPO}.list_stale_execution_claims"),
        "release": patch(f"{_REPO}.release_proposal_execution_claim"),
        "cfg": patch(f"{_SCHED}.load_config"),
    }
    for key, val in overrides.items():
        if key in patches:
            patches[key] = val
    return patches


def test_sweeper_releases_expired_claim_without_order():
    """Expired claim for submission_unknown proposal without Order must be released."""
    from aletheia.agent_runtime.scheduler import _run_execution_claim_sweeper

    claim = _make_claim()

    with patch(f"{_REPO}.list_stale_execution_claims") as mock_list, \
         patch(f"{_REPO}.has_order_for_proposal", return_value=False), \
         patch(f"{_REPO}.release_proposal_execution_claim") as mock_release, \
         patch(f"{_REPO}.get_trade_proposal") as mock_get_proposal, \
         patch(f"{_SCHED}.load_config") as mock_cfg:
        mock_list.return_value = [claim]
        # Proposal in submission_unknown (terminal stuck state)
        mock_get_proposal.return_value = {"id": "prop_1", "status": "submission_unknown"}
        mock_cfg.return_value.execution_claim_sweep_enabled = True

        _run_execution_claim_sweeper()

        # Claim should be released
        mock_release.assert_called_once_with("prop_1", "dead_owner_token")


def test_sweeper_skips_active_lease():
    """Claims with active (non-expired) lease must NOT be released."""
    from aletheia.agent_runtime.scheduler import _run_execution_claim_sweeper

    # Lease expires in the future
    claim = _make_claim(lease_expires_at=(
        datetime.now(timezone.utc) + timedelta(seconds=300)
    ).isoformat())

    with patch(f"{_REPO}.list_stale_execution_claims") as mock_list, \
         patch(f"{_REPO}.release_proposal_execution_claim") as mock_release, \
         patch(f"{_SCHED}.load_config") as mock_cfg:
        mock_list.return_value = [claim]
        mock_cfg.return_value.execution_claim_sweep_enabled = True

        _run_execution_claim_sweeper()

        # Active claim must NOT be released
        mock_release.assert_not_called()


def test_sweeper_skips_claim_with_order():
    """Claims for proposals that already have an Order must NOT be touched."""
    from aletheia.agent_runtime.scheduler import _run_execution_claim_sweeper

    claim = _make_claim()

    with patch(f"{_REPO}.list_stale_execution_claims") as mock_list, \
         patch(f"{_REPO}.has_order_for_proposal", return_value=True), \
         patch(f"{_REPO}.release_proposal_execution_claim") as mock_release, \
         patch(f"{_REPO}.get_trade_proposal") as mock_get_proposal, \
         patch(f"{_SCHED}.load_config") as mock_cfg:
        mock_list.return_value = [claim]
        mock_get_proposal.return_value = {"id": "prop_1", "status": "submission_unknown"}
        mock_cfg.return_value.execution_claim_sweep_enabled = True

        _run_execution_claim_sweeper()

        # Must NOT release — Order exists, reconciliation handles it
        mock_release.assert_not_called()


def test_sweeper_respects_enabled_flag():
    """Sweeper must do nothing when disabled in config."""
    from aletheia.agent_runtime.scheduler import _run_execution_claim_sweeper

    with patch(f"{_REPO}.list_stale_execution_claims") as mock_list, \
         patch(f"{_REPO}.release_proposal_execution_claim") as mock_release, \
         patch(f"{_SCHED}.load_config") as mock_cfg:
        mock_cfg.return_value.execution_claim_sweep_enabled = False

        _run_execution_claim_sweeper()

        mock_list.assert_not_called()
        mock_release.assert_not_called()


def test_release_expired_execution_claims_buffer():
    """release_expired_execution_claims must respect the buffer parameter."""
    from aletheia.storage.repositories import release_expired_execution_claims

    now = datetime.now(timezone.utc)
    # Claim expired 90s ago, buffer is 60s → should be released
    claim_old = _make_claim("prop_old", (now - timedelta(seconds=90)).isoformat())
    # Claim expired 30s ago, buffer is 60s → should NOT be released (within buffer)
    claim_recent = _make_claim("prop_recent", (now - timedelta(seconds=30)).isoformat())

    with patch(f"{_REPO}.list_stale_execution_claims") as mock_list, \
         patch(f"{_REPO}.release_proposal_execution_claim"):
        # Simulate list_stale returning only the old claim (already filtered by buffer)
        mock_list.return_value = [claim_old]

        released = release_expired_execution_claims(buffer_sec=60.0)

        # Only the old claim should be released
        assert "prop_old" in released
        assert "prop_recent" not in released
