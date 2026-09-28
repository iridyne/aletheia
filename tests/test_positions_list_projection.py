"""Regression test for Issue #3631.

`GET /api/positions` returned every column of the durable Position row, so the
per-position narrative `decision_reasoning` was ~59% of a 200-row / 3 MB payload
that every caller paid for, growing with each closed trade. The list is now a
summary projection; the complete row is served by `GET /api/positions/{id}`.

Pinned here:
1. Detail-only columns are absent from the list and present on the detail read.
2. The projection drops columns from the response, not from the stored row.
3. `/positions/reconciliation` still resolves — the literal path must win over
   `/positions/{position_id}`, which is registered after it for that reason.
"""

import pytest
from fastapi.testclient import TestClient

DETAIL_ONLY_COLUMNS = (
    "decision_reasoning",
    "entry_market_summary",
    "last_market_signal_evidence",
    "reconciliation_evidence",
    "reconciliation_identity",
    "trailing_json",
)


def _seed(repo, models) -> None:
    repo.insert_position(
        models.Position(
            id="pos-3631",
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="bitget_demo",
            status="closed",
            decision_reasoning="Thesis: funding flipped negative, core long with a 2% stop.",
            exit_reasoning='{"reason_code": "stop_loss"}',
            entry_market_summary='{"regime_tag": "risk_on"}',
            reconciliation_evidence='{"matched": true}',
            reconciliation_identity='{"venue": "bitget_demo"}',
        )
    )


@pytest.mark.tier_integration
@pytest.mark.domain_execution
def test_positions_list_is_a_summary_projection(api_only_runtime):
    from aletheia.server.app import app
    from aletheia.storage import models
    from aletheia.storage import repositories as repo

    _seed(repo, models)
    with TestClient(app) as client:
        body = client.get("/api/positions").json()

        assert len(body) == 1
        row = body[0]
        assert row["id"] == "pos-3631"
        assert row["exit_reasoning"] == '{"reason_code": "stop_loss"}', (
            "exit_reasoning is read off the list row by the causal-chain view"
        )
        assert [key for key in DETAIL_ONLY_COLUMNS if key in row] == []

        # The projection strips the response; the durable row keeps the narrative.
        stored = repo.get_position("pos-3631")
        assert stored is not None
        assert stored["decision_reasoning"].startswith("Thesis:")

        detail = client.get("/api/positions/pos-3631")
        assert detail.status_code == 200
        full = detail.json()
        assert full["id"] == row["id"]
        assert full["decision_reasoning"].startswith("Thesis:")
        assert full["entry_market_summary"] == '{"regime_tag": "risk_on"}'
        assert full["reconciliation_evidence"] == '{"matched": true}'
        assert full["reconciliation_identity"] == '{"venue": "bitget_demo"}'

        assert client.get("/api/positions/no-such-position").status_code == 404
        assert client.get("/api/positions/reconciliation").status_code == 200
