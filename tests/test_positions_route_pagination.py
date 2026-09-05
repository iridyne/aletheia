"""Regression test for Issue #2891.

GET /api/positions previously declared no query parameters, so `?status=` and
`?limit=` sent by the frontend were silently dropped by FastAPI and every
response fell back to the repository default of 50 rows. This test proves:

1. `limit` is honoured server-side.
2. `status` filters correctly and does not bleed wrong rows.
3. An invalid `status` value is rejected (fail-closed, 422).
"""

import pytest
from fastapi.testclient import TestClient


def _seed(repo, models, n_open: int, n_closed: int) -> None:
    for i in range(n_open):
        repo.insert_position(
            models.Position(
                id=f"o{i}",
                symbol="BTCUSDT",
                market_type="spot",
                direction="long",
                quantity=1.0,
                entry_price=100.0,
                execution_mode="paper_internal",
                status="open",
            )
        )
    for i in range(n_closed):
        repo.insert_position(
            models.Position(
                id=f"c{i}",
                symbol="ETHUSDT",
                market_type="spot",
                direction="long",
                quantity=1.0,
                entry_price=100.0,
                execution_mode="paper_internal",
                status="closed",
            )
        )

@pytest.mark.tier_integration
@pytest.mark.domain_execution
def test_positions_limit_and_status_filter(api_only_runtime):
    from aletheia.server.app import app
    from aletheia.storage import models
    from aletheia.storage import repositories as repo

    _seed(repo, models, n_open=3, n_closed=260)
    with TestClient(app) as client:
        # Default call — limit 200 returns at most 200 rows total.
        r_all = client.get("/api/positions")
        assert r_all.status_code == 200
        assert len(r_all.json()) <= 200

        # status=closed + limit=100 must return exactly 100 closed rows, no open rows.
        r_closed = client.get("/api/positions?status=closed&limit=100")
        assert r_closed.status_code == 200
        body = r_closed.json()
        assert len(body) == 100, f"expected 100 rows, got {len(body)}"
        assert {p["status"] for p in body} == {"closed"}, "open row leaked into closed page"

        # status=open returns only the 3 seeded open positions.
        r_open = client.get("/api/positions?status=open")
        assert r_open.status_code == 200
        assert len(r_open.json()) == 3
        assert {p["status"] for p in r_open.json()} == {"open"}

        # Garbage status value must be rejected.
        r_bad = client.get("/api/positions?status=bogus")
        assert r_bad.status_code == 422, "invalid status must be rejected fail-closed"
