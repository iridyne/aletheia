"""Executable fairness contract for private-ledger funding recovery."""

from __future__ import annotations

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.test_broker_funding_economics import (
    test_expired_history_prefix_does_not_starve_recoverable_close as _assert_expired_prefix_progress,
)
from tests.test_broker_funding_economics import (
    test_funding_queue_cursor_rotates_past_retryable_or_conflicting_prefix as _assert_cursor_rotation,
)


@spec_claim(
    SpecClaim(
        "EX-B18",
        "execution_contract",
        "§11.6 broker funding retention recovery",
        "L2b",
        "Typed retention expiry preserves NULL economics, while durable cursor "
        "rotation prevents retryable or conflicting prefixes from starving a "
        "later recoverable venue.",
    ),
)
@pytest.mark.asyncio
async def test_broker_funding_recovery_is_fail_closed_and_fair(
    tmp_path,
    monkeypatch,
):
    await _assert_expired_prefix_progress(
        tmp_path,
        monkeypatch,
        expired_count=50,
        with_transient=False,
    )
    await _assert_cursor_rotation(
        tmp_path,
        monkeypatch,
        prefix_kind="transient_failure",
    )
