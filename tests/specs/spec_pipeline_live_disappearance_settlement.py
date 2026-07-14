"""Live disappearance settlement must materialize exact observed close facts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.specs.spec_pipeline_tpsl_private_materialization import (
    _seed_position,
    _setup_db,
)


def _ready_candidate(position):
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.storage.models import PositionDisappearanceCandidate

    return PositionDisappearanceCandidate(
        id=f"candidate-{position.id}",
        position_id=position.id,
        identity_key=f"identity-{position.id}",
        broker_id=position.execution_mode,
        account_environment="live",
        account_fingerprint="bitget-live-account-scope",
        execution_mode=position.execution_mode,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        first_observed_at=position.opened_at,
        last_observed_at=position.opened_at,
        confirmation_count=2,
        semantics_version=OUTCOME_SEMANTICS_VERSION,
    )


@spec_claim(
    SpecClaim(
        "PL-LDS-01",
        "pipeline_live_disappearance_settlement",
        "§1 exact live close materialization",
        "L2c",
        "A twice-confirmed live disappearance with exact account-scoped private "
        "fills closes local state and persists an adopted Order and exact Fills, "
        "without issuing any exchange mutation.",
        status="implemented",
    )
)
def test_live_disappearance_materializes_observed_order_fill_and_position(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.verified_exit import (
        VerifiedExitFill,
        classify_verified_exit,
    )
    from aletheia.portfolio import disappearance_settlement, outcome_closure
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_live_limited",
        suffix="external-close",
        plan_ids={},
    )
    candidate = _ready_candidate(position)
    repo.insert_position_disappearance_candidate(candidate)
    queried_entry_ids: list[tuple[str, ...]] = []
    exit_fill_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 1_000

    class _ReadOnlyClient:
        async def get_verified_position_exit(self, request):
            queried_entry_ids.append(request.known_entry_order_ids)
            fill = VerifiedExitFill(
                fill_id="REMOTE-EXIT-FILL-1",
                order_id="REMOTE-EXIT-ORDER-1",
                price=59_000.0,
                quantity=0.01,
                fee=0.2,
                fee_currency="USDT",
                realized_pnl=-10.0,
                filled_at_ms=exit_fill_ms,
            )
            return classify_verified_exit(
                request,
                position_absent=True,
                history_complete=True,
                close_fills=(fill,),
                ambiguous_activity=False,
                pages=1,
                source_endpoint="private-fill-history",
            )

        async def place_order(self, *args, **kwargs):
            raise AssertionError("disappearance settlement must never place an order")

        async def place_tpsl_order(self, *args, **kwargs):
            raise AssertionError("disappearance settlement must never place TPSL")

        async def cancel_order(self, *args, **kwargs):
            raise AssertionError("disappearance settlement must never cancel an order")

        async def cancel_plan_order(self, *args, **kwargs):
            raise AssertionError("disappearance settlement must never cancel TPSL")

        async def close(self):
            return None

    monkeypatch.setattr(
        disappearance_settlement,
        "_client_for_mode",
        lambda mode: _ReadOnlyClient(),
    )
    monkeypatch.setattr(
        outcome_closure,
        "_run_outcome_hooks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("verified external close must not enter feedback")
        ),
    )

    result = disappearance_settlement.settle_ready_disappearance_candidate(
        candidate.id
    )

    assert result["closed"] is True, (result, queried_entry_ids)
    assert result["closure_type"] == "verified_external"
    assert queried_entry_ids == [("ENTRY-TPSL-external-close",)]
    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    assert stored["current_price"] == pytest.approx(59_000.0)
    assert stored["close_reason"] == "verified_external"
    assert stored["close_category"] == "administrative"
    assert stored["exit_price_source"] == "broker_fill"
    assert stored["exit_fill_verified"] == 1
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "administrative_close"
    assert repo.list_outcome_hook_jobs(position_id=position.id) == []

    close_order = db.execute(
        "SELECT * FROM orders WHERE position_id=? AND close_reason=?",
        (position.id, "verified_external"),
    ).fetchone()
    assert close_order is not None
    assert close_order["source"] == "adopted"
    assert close_order["exchange_order_id"] == "REMOTE-EXIT-ORDER-1"
    assert close_order["status"] == "filled"
    close_fill = db.execute(
        "SELECT * FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()
    assert close_fill["id"] == (
        "private-fill:bitget_live_limited:REMOTE-EXIT-FILL-1"
    )
    assert close_fill["fill_price"] == pytest.approx(59_000.0)
    assert close_fill["fill_quantity"] == pytest.approx(0.01)
    assert close_fill["fee"] == pytest.approx(0.2)
    assert close_fill["fee_source"] == "reconciled"
    assert close_fill["filled_at"] == datetime.fromtimestamp(
        exit_fill_ms / 1000, tz=timezone.utc
    ).isoformat()

    repeated = disappearance_settlement.settle_ready_disappearance_candidate(
        candidate.id
    )
    assert repeated["reason"] == "already_resolved"
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason=?",
        (position.id, "verified_external"),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 1


@spec_claim(
    SpecClaim(
        "PL-LDS-04",
        "pipeline_live_disappearance_settlement",
        "§4 atomic local settlement",
        "L2c",
        "Observed close Order/Fill facts, the closed Position, and the resolved "
        "disappearance candidate commit together; a candidate-write failure rolls "
        "the whole local settlement back.",
        status="implemented",
    )
)
def test_live_disappearance_rolls_back_close_chain_when_candidate_write_fails(
    tmp_path, monkeypatch
):
    db = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.verified_exit import VerifiedExitFill, classify_verified_exit
    from aletheia.portfolio import disappearance_settlement
    from aletheia.storage import repositories as repo

    position = _seed_position(
        "bitget_live_limited",
        suffix="atomic-rollback",
        plan_ids={},
    )
    candidate = _ready_candidate(position)
    repo.insert_position_disappearance_candidate(candidate)
    exit_fill_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 1_000

    class _ReadOnlyClient:
        async def get_verified_position_exit(self, request):
            return classify_verified_exit(
                request,
                position_absent=True,
                history_complete=True,
                close_fills=(VerifiedExitFill(
                    fill_id="REMOTE-ATOMIC-FILL",
                    order_id="REMOTE-ATOMIC-ORDER",
                    price=59_000.0,
                    quantity=0.01,
                    fee=0.2,
                    fee_currency="USDT",
                    realized_pnl=-10.0,
                    filled_at_ms=exit_fill_ms,
                ),),
                ambiguous_activity=False,
                pages=1,
                source_endpoint="private-fill-history",
            )

        async def close(self):
            return None

    monkeypatch.setattr(
        disappearance_settlement,
        "_client_for_mode",
        lambda mode: _ReadOnlyClient(),
    )
    original_update_candidate = repo.update_position_disappearance_candidate

    def fail_candidate_write(candidate_id: str, **kwargs):
        raise RuntimeError("candidate write failed")

    monkeypatch.setattr(
        repo,
        "update_position_disappearance_candidate",
        fail_candidate_write,
    )

    with pytest.raises(RuntimeError, match="candidate write failed"):
        disappearance_settlement.settle_ready_disappearance_candidate(candidate.id)

    monkeypatch.setattr(
        repo,
        "update_position_disappearance_candidate",
        original_update_candidate,
    )
    assert repo._select_one("positions", position.id)["status"] == "open"
    assert repo._select_one(
        "position_disappearance_candidates", candidate.id
    )["status"] == "pending"
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE exchange_order_id='REMOTE-ATOMIC-ORDER'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE id LIKE '%REMOTE-ATOMIC-FILL'"
    ).fetchone()[0] == 0


@spec_claim(
    SpecClaim(
        "PL-LDS-02",
        "pipeline_live_disappearance_settlement",
        "§2 exact remote order identity",
        "L2b",
        "Private exit evidence without an exact remote order identity remains "
        "inconclusive and cannot close or materialize local lifecycle state.",
        status="implemented",
    )
)
def test_verified_exit_without_remote_order_id_is_inconclusive():
    from aletheia.execution.verified_exit import (
        VerifiedExitFill,
        VerifiedExitRequest,
        classify_verified_exit,
    )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    request = VerifiedExitRequest(
        broker_id="bitget_live_limited",
        account_environment="live",
        account_scope="bitget-live-account-scope",
        execution_mode="bitget_live_limited",
        position_id="position-missing-remote-order",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        history_start_ms=now_ms - 60_000,
        history_end_ms=now_ms,
    )
    result = classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=(VerifiedExitFill(
            fill_id="fill-without-order",
            order_id=None,
            price=59_000.0,
            quantity=0.01,
            fee=0.2,
            fee_currency="USDT",
            realized_pnl=-10.0,
            filled_at_ms=now_ms - 1_000,
        ),),
        ambiguous_activity=False,
        pages=1,
        source_endpoint="private-fill-history",
    )

    assert result.state == "inconclusive"
    assert result.reason == "malformed_fill_evidence"


@spec_claim(
    SpecClaim(
        "PL-LDS-05",
        "pipeline_live_disappearance_settlement",
        "§5 usable fee evidence",
        "L2b",
        "A verified external close requires every private fee to be finite and "
        "non-negative before any local Order, Fill, Position, or PnL mutation.",
        status="implemented",
    )
)
@pytest.mark.parametrize("invalid_fee", [float("nan"), float("inf"), -0.01])
def test_verified_exit_rejects_non_finite_or_negative_fee(invalid_fee):
    from aletheia.execution.verified_exit import (
        VerifiedExitFill,
        VerifiedExitRequest,
        classify_verified_exit,
    )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    request = VerifiedExitRequest(
        broker_id="bitget_live_limited",
        account_environment="live",
        account_scope="bitget-live-account-scope",
        execution_mode="bitget_live_limited",
        position_id="position-invalid-fee",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        history_start_ms=now_ms - 60_000,
        history_end_ms=now_ms,
    )
    result = classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=(VerifiedExitFill(
            fill_id="fill-invalid-fee",
            order_id="order-invalid-fee",
            price=59_000.0,
            quantity=0.01,
            fee=invalid_fee,
            fee_currency="USDT",
            realized_pnl=-10.0,
            filled_at_ms=now_ms - 1_000,
        ),),
        ambiguous_activity=False,
        pages=1,
        source_endpoint="private-fill-history",
    )

    assert result.state == "inconclusive"
    assert result.reason == "malformed_fill_evidence"


@spec_claim(
    SpecClaim(
        "PL-LDS-03",
        "pipeline_live_disappearance_settlement",
        "§3 live reconciler wiring",
        "L2c",
        "The live Position reconciler routes a ready disappearance candidate to "
        "the read-only verified settlement phase while retaining the ban on live "
        "external-position adoption.",
        status="implemented",
    )
)
def test_live_reconciler_routes_ready_candidate_to_verified_settlement(
    tmp_path, monkeypatch
):
    import yaml

    from tests.test_position_reconciler import _stub_cache, setup_env
    from aletheia.config import loader
    from aletheia.portfolio import (
        disappearance_confirmation,
        disappearance_settlement,
        position_reconciler,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    setup_env(tmp_path, monkeypatch)
    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({
            "exchange": {"mode": "live"},
            "trading": {"auto_adopt_enabled": True},
        }),
        encoding="utf-8",
    )
    loader._invalidate_config_cache()
    position = Position(
        id="position-live-ready-routing",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=60_000.0,
        current_price=60_000.0,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(position)
    _stub_cache(monkeypatch, [])
    monkeypatch.setattr(
        disappearance_confirmation,
        "process_disappearance_evidence",
        lambda **kwargs: {
            "created": 0,
            "observations": [{
                "position_id": position.id,
                "candidate_id": "candidate-live-ready-routing",
                "ready_for_verified_exit": True,
            }],
        },
    )
    settled: list[list[str]] = []
    monkeypatch.setattr(
        disappearance_settlement,
        "settle_ready_disappearance_candidates",
        lambda candidate_ids: (
            settled.append(candidate_ids)
            or [{"candidate_id": candidate_ids[0], "closed": False}]
        ),
    )

    summary = position_reconciler.run_reconcile()

    assert settled == [["candidate-live-ready-routing"]]
    assert summary["settlement"] == [{
        "candidate_id": "candidate-live-ready-routing",
        "closed": False,
    }]
    assert summary["adopted"] == []
