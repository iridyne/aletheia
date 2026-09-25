"""Executable fairness contract for private-ledger funding recovery."""

from __future__ import annotations

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.test_broker_funding_economics import (
    configured_funding_retention as configured_funding_retention,
)
from tests.test_broker_funding_economics import (
    test_expired_funding_prefix_does_not_starve_recoverable_close as _assert_prefix_progress,
)
from tests.test_broker_funding_economics import (
    test_funding_cursor_progress_survives_connection_reopen as _assert_restart_progress,
)


@spec_claim(
    SpecClaim(
        "EX-B18",
        "execution_contract",
        "§11.6 broker funding retention recovery",
        "L2b",
        "Typed retention expiry preserves NULL economics, while bounded durable "
        "cursor rotation prevents terminal or retryable prefixes from starving "
        "a later recoverable venue.",
    ),
)
@pytest.mark.asyncio
async def test_broker_funding_recovery_is_fail_closed_and_fair(
    tmp_path,
    monkeypatch,
):
    prefix = await _assert_prefix_progress(tmp_path / "prefix", monkeypatch)
    restart = await _assert_restart_progress(tmp_path / "restart", monkeypatch)
    assert prefix is not None and prefix["terminal_total"] == 52, (
        "typed retention expiry must terminalize the expired prefix"
    )
    assert restart is not None and restart["history_expired"] == 1, (
        "cursor rotation must recover the fresh close after a connection reopen"
    )
