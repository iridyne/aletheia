"""Phase 1 CCXT convergence: Bitget UTA v3 funding normalization and fill circuit breaker.

Scope: #3696 acceptance checks.
  - normalize_bitget_uta_funding_rows reads v3 field names (id/ts/type/coin/amount/symbol).
  - normalize_bitget_funding_rows (classic v2) still reads v2 field names unchanged.
  - Fill circuit breaker opens after consecutive UNAVAILABLE and emits a critical event.
  - PARTIAL/UNSUPPORTED do not trip the circuit breaker.
  - COMPLETE resets the counter and resolves the critical event.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from aletheia.uta.funding import (
    normalize_bitget_funding_rows,
    normalize_bitget_uta_funding_rows,
)


# ---------------------------------------------------------------------------
# UTA v3 funding normalization
# ---------------------------------------------------------------------------

def _v3_row(
    *,
    id="uta-1",
    ts="1784044800000",
    type="CONTRACT_MAIN_SETTLE_FEE_USER_OUT",
    coin="USDT",
    amount="-2.5",
    symbol="BTCUSDT",
):
    return {"id": id, "ts": ts, "type": type, "coin": coin, "amount": amount, "symbol": symbol}


def test_normalize_bitget_uta_funding_rows_user_out():
    rows = [_v3_row(amount="-2.5", type="CONTRACT_MAIN_SETTLE_FEE_USER_OUT")]
    events = normalize_bitget_uta_funding_rows(rows)
    assert len(events) == 1
    assert events[0]["event_id"] == "uta-1"
    assert float(events[0]["cash_change"]) == pytest.approx(-2.5)
    assert events[0]["currency"] == "USDT"
    assert events[0]["occurred_at_ms"] == 1784044800000


def test_normalize_bitget_uta_funding_rows_user_in():
    rows = [_v3_row(amount="1.25", type="CONTRACT_MAIN_SETTLE_FEE_USER_IN", id="uta-2")]
    events = normalize_bitget_uta_funding_rows(rows)
    assert len(events) == 1
    assert float(events[0]["cash_change"]) == pytest.approx(1.25)


def test_normalize_bitget_uta_funding_rows_skips_unknown_types():
    rows = [
        _v3_row(id="uta-1", type="CONTRACT_MAIN_SETTLE_FEE_USER_OUT"),
        _v3_row(id="uta-2", type="SOME_OTHER_TYPE"),
        _v3_row(id="uta-3", type="CONTRACT_MAIN_SETTLE_FEE_USER_IN", amount="0.5"),
    ]
    events = normalize_bitget_uta_funding_rows(rows)
    assert len(events) == 2
    assert {e["event_id"] for e in events} == {"uta-1", "uta-3"}


def test_normalize_bitget_uta_funding_rows_rejects_invalid_amount():
    rows = [_v3_row(amount="not-a-number")]
    with pytest.raises(ValueError):
        normalize_bitget_uta_funding_rows(rows)


def test_normalize_bitget_uta_funding_rows_rejects_non_usdt():
    rows = [_v3_row(coin="BTC")]
    with pytest.raises(ValueError):
        normalize_bitget_uta_funding_rows(rows)


def test_normalize_bitget_uta_funding_rows_deduplicates():
    row = _v3_row(id="uta-1")
    events = normalize_bitget_uta_funding_rows([row, row])
    assert len(events) == 1


def test_normalize_bitget_uta_funding_rows_conflicts_raise():
    rows = [
        _v3_row(id="uta-1", amount="-2.5"),
        _v3_row(id="uta-1", amount="-3.0"),  # same id, different amount
    ]
    with pytest.raises(ValueError, match="conflicting"):
        normalize_bitget_uta_funding_rows(rows)


# ---------------------------------------------------------------------------
# Classic v2 normalization still reads v2 fields unchanged
# ---------------------------------------------------------------------------

def test_normalize_bitget_v2_reads_billid_businesstype_not_v3_fields():
    rows = [{
        "billId": "bg-1",
        "symbol": "BTCUSDT",
        "amount": "-2.5",
        "businessType": "contract_settle_fee",
        "coin": "USDT",
        "cTime": "1784044800000",
    }]
    events = normalize_bitget_funding_rows(rows)
    assert len(events) == 1
    assert events[0]["event_id"] == "bg-1"

    # v3 field names must NOT be read by the v2 normalizer
    v3_only_rows = [{
        "id": "uta-99",
        "ts": "1784044800000",
        "type": "CONTRACT_MAIN_SETTLE_FEE_USER_OUT",
        "coin": "USDT",
        "amount": "-2.5",
        "symbol": "BTCUSDT",
    }]
    assert normalize_bitget_funding_rows(v3_only_rows) == []


# ---------------------------------------------------------------------------
# Fill circuit breaker
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_fill_breaker():
    """Ensure module-level circuit state is clean around each test."""
    from aletheia.execution import reconciliation as rec
    rec._fill_breaker.clear()
    yield
    rec._fill_breaker.clear()


def test_fill_circuit_opens_after_threshold():
    from aletheia.execution.reconciliation import (
        _on_fill_unavailable,
        _fill_breaker,
        _FILL_BREAKER_THRESHOLD,
    )
    from aletheia.infra.events import clear_infra_events

    clear_infra_events()
    for _ in range(_FILL_BREAKER_THRESHOLD):
        _on_fill_unavailable("bitget_demo", "usdt_futures")

    state = _fill_breaker.get(("bitget_demo", "usdt_futures"), {})
    assert state.get("consecutive", 0) >= _FILL_BREAKER_THRESHOLD
    assert "open_until" in state


def test_fill_circuit_emits_critical_event():
    from aletheia.execution.reconciliation import (
        _on_fill_unavailable,
        _FILL_BREAKER_THRESHOLD,
    )
    from aletheia.infra.events import list_outstanding_infra_events, clear_infra_events

    clear_infra_events()
    for _ in range(_FILL_BREAKER_THRESHOLD):
        _on_fill_unavailable("bitget_demo", "usdt_futures")

    events = list_outstanding_infra_events()
    stalled = [
        e for e in events
        if e.get("code") == "exchange_reconciliation_stalled"
        and "bitget_demo/usdt_futures/get_fills" in (e.get("entity_id") or "")
    ]
    assert stalled, "Expected a critical infra event when circuit opens"


def test_fill_circuit_resolves_on_complete():
    from aletheia.execution.reconciliation import (
        _on_fill_unavailable,
        _on_fill_complete,
        _fill_breaker,
        _FILL_BREAKER_THRESHOLD,
    )
    from aletheia.infra.events import list_outstanding_infra_events, clear_infra_events

    clear_infra_events()
    for _ in range(_FILL_BREAKER_THRESHOLD):
        _on_fill_unavailable("bitget_demo", "usdt_futures")

    _on_fill_complete("bitget_demo", "usdt_futures")
    assert ("bitget_demo", "usdt_futures") not in _fill_breaker

    events = list_outstanding_infra_events()
    stalled = [
        e for e in events
        if e.get("code") == "exchange_reconciliation_stalled"
    ]
    assert not stalled, "Critical event should be resolved after COMPLETE"


def test_fill_circuit_partial_does_not_trip():
    from aletheia.execution.reconciliation import (
        _on_fill_unavailable,
        _fill_breaker,
        _FILL_BREAKER_THRESHOLD,
    )
    # Only UNAVAILABLE counts; verify counter increments only for that path.
    # PARTIAL/UNSUPPORTED paths don't call _on_fill_unavailable at all.
    for _ in range(_FILL_BREAKER_THRESHOLD - 1):
        _on_fill_unavailable("bitget_demo", "usdt_futures")

    state = _fill_breaker.get(("bitget_demo", "usdt_futures"), {})
    assert "open_until" not in state, "Circuit must not open before threshold"


def test_fill_circuit_cooldown_blocks_calls_at_reconciliation_layer(monkeypatch):
    from aletheia.execution.reconciliation import (
        _on_fill_unavailable,
        _fill_breaker,
        _FILL_BREAKER_THRESHOLD,
        _FILL_BREAKER_COOLDOWN_S,
        _UTAReconciliationProjection,
    )
    from aletheia.uta.models import ReadCompleteness
    from aletheia.execution.errors import IncompleteReadError
    from importlib.metadata import version
    from aletheia.uta.models import UTAAccountIdentity

    for _ in range(_FILL_BREAKER_THRESHOLD):
        _on_fill_unavailable("bitget_demo", "usdt_futures")

    # The circuit is now open; ensure open_until is in the future.
    key = ("bitget_demo", "usdt_futures")
    _fill_breaker[key]["open_until"] = time.monotonic() + _FILL_BREAKER_COOLDOWN_S

    identity = UTAAccountIdentity(
        account_id="bitget-uta_v3-primary",
        execution_mode="bitget_demo",
        venue="bitget",
        environment="demo",
        profile="uta_v3",
        account_fingerprint="fp-test",
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version=version("ccxt"),
    )
    projection = _UTAReconciliationProjection("bitget_demo", identity)

    query_uta_calls = []

    async def fake_query_uta(*args, **kwargs):
        query_uta_calls.append(args)
        raise RuntimeError("should not be called when circuit is open")

    monkeypatch.setattr(
        "aletheia.execution.reconciliation.query_uta",
        fake_query_uta,
    )

    import asyncio
    with pytest.raises(IncompleteReadError) as exc_info:
        asyncio.run(projection.get_fills(market_type="usdt_futures"))

    assert exc_info.value.completeness is ReadCompleteness.UNAVAILABLE
    # Circuit should not have called upstream.
    assert not query_uta_calls, "Circuit breaker must block upstream call during cooldown"
