"""A rejected private fill must say WHICH validation failed, not just the exception class."""

import logging
from typing import cast

from aletheia.execution import reconciliation
from aletheia.execution.exchange_materialization import PrivateFillEvidenceInvalid


def test_rejected_materialization_log_carries_the_validator_reason(monkeypatch, caplog):
    def reject(_order_id, _rows):
        raise PrivateFillEvidenceInvalid("private fill report identity or timestamp is incomplete")

    monkeypatch.setattr(reconciliation, "materialize_private_entry", reject)
    monkeypatch.setattr(reconciliation, "_write_reconcile_event", lambda *a, **k: None)
    errors: list[str] = []

    with caplog.at_level(logging.WARNING, logger=reconciliation.logger.name):
        result = reconciliation._materialize_native_order(
            {"id": "o1", "execution_mode": "bitget_demo", "market_type": "usdt_futures", "status": "submitted"},
            cast(reconciliation._UTAReconciliationProjection, None), [], False, False, None, errors,
        )

    assert result == ("inconclusive", 0)
    assert "identity or timestamp is incomplete" in caplog.text
