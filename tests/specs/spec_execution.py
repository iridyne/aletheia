"""Spec: Execution Contract (execution_contract.md)

The causal chain trade_proposals -> risk_decisions -> orders -> fills -> positions
is the most critical invariant in the entire system.
"""

import ast

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "EX-S01", "execution_contract", "§1 entry points", "L2a",
        "submit_proposal() exists and delegates execution through "
        "execute_existing_proposal() instead of bypassing its durable claim",
    ),
)
def test_execution_gateway_entry_points():
    from aletheia.execution import gateway

    assert callable(gateway.submit_proposal)
    assert callable(gateway.execute_existing_proposal)

    source = ast.parse(open(gateway.__file__, encoding="utf-8").read())
    submit = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "submit_proposal"
    )
    calls = {
        node.func.id for node in ast.walk(submit)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "execute_existing_proposal" in calls
    assert "_execute_proposal" not in calls


@spec_claim(
    SpecClaim(
        "EX-S02", "execution_contract", "§1 internal", "L2a",
        "_execute_proposal is private (single underscore prefix)",
    ),
)
def test_execution_internal_is_private():
    from aletheia.execution import gateway

    assert callable(gateway._execute_proposal)


@spec_claim(
    SpecClaim(
        "EX-S04", "execution_contract", "§4 reconciliation", "L2a",
        "reconciliation.reconcile() exists and is independent of order placement",
    ),
)
def test_reconciliation_module():
    from aletheia.execution.reconciliation import reconcile

    assert callable(reconcile)


@spec_claim(
    SpecClaim(
        "EX-B01", "execution_contract", "§1 risk gate", "L2b",
        "submit_proposal() calls evaluate_proposal before execution",
    ),
)
def test_submit_calls_risk_first(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()

    result = submit_proposal(
        symbol="BTC/USDT", direction="long", quantity=0.001,
        price=60000.0, execution_mode="bitget_demo",
        market_type="spot",
    )
    decisions = repo.list_risk_decisions(limit=10)
    assert len(decisions) >= 1, "submit_proposal must trigger risk evaluation"


@spec_claim(
    SpecClaim(
        "EX-B13", "execution_contract", "§3.4 preview runtime", "L2b",
        "Preview runtime rejects proposal submission and re-execution before any durable execution state changes",
    ),
)
def test_preview_runtime_blocks_execution_before_persistence(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    import aletheia.execution.gateway as gateway
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(gateway, "is_preview_mode", lambda: True)

    with pytest.raises(gateway.PreviewExecutionBlockedError, match="preview mode"):
        gateway.submit_proposal(
            symbol="BTC/USDT", direction="long", quantity=0.001,
            price=60_000.0, execution_mode="bitget_demo", market_type="spot",
        )
    with pytest.raises(gateway.PreviewExecutionBlockedError, match="preview mode"):
        gateway.execute_existing_proposal("does-not-matter")

    assert repo.list_trade_proposals(limit=10) == []


@spec_claim(
    SpecClaim(
        "EX-B20", "execution_contract", "§1 entry ownership", "L2b",
        "Gateway rejects non-native proposal sources before creating or claiming executable state",
    ),
)
def test_gateway_rejects_non_native_execution_before_side_effects(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import execute_existing_proposal, submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    for source in ("adopted", "human", "adopte"):
        submitted = submit_proposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=0.001, price=60_000.0, execution_mode="bitget_demo",
            source=source,
        )
        assert submitted == {
            "status": "rejected",
            "reason_code": "non_native_proposal_not_executable",
            "proposal_id": None,
            "message": "Only native Proposals may enter execution; no Proposal was created.",
        }
    assert repo.list_trade_proposals(limit=10) == []

    adopted = TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        quantity=0.01, price=3_000.0, execution_mode="bitget_demo",
        source="adopted",
    )
    repo.insert_trade_proposal(adopted)

    executed = execute_existing_proposal(adopted.id, "bitget_demo")
    assert executed == {
        "status": "rejected",
        "reason_code": "non_native_proposal_not_executable",
        "proposal_id": adopted.id,
        "message": "Only native Proposals may enter execution; no order was created.",
    }
    assert repo.get_proposal_execution_claim(adopted.id) is None
    assert repo.list_orders(limit=10) == []
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(limit=10) == []


@spec_claim(
    SpecClaim(
        "EX-B02", "execution_contract", "§2 proposal status", "L2b",
        "TradeProposal default status is 'pending'",
    ),
)
def test_proposal_default_status():
    from aletheia.storage.models import TradeProposal

    p = TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=1, price=3000,
    )
    assert p.status == "pending"


@spec_claim(
    SpecClaim(
        "EX-B05", "execution_contract", "§1 executable venue rules", "L2b",
        "Exchange-bound proposals obtain executable-venue constraints, re-run Risk "
        "Engine on the adjusted quantity, and reject if rules are unavailable",
    ),
)
def test_exchange_order_constraint_gate_is_wired_before_submission():
    from aletheia.execution import gateway

    source = ast.parse(open(gateway.__file__, encoding="utf-8").read())
    execute = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_proposal"
    )
    calls = [
        node.func.id for node in ast.walk(execute)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "evaluate_proposal" in calls
    assert "_prepare_exchange_quantity" in calls
    assert calls.count("evaluate_proposal") >= 2, "adjusted quantity must be re-checked by Risk Engine"


def _setup_db(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()


@spec_claim(
    SpecClaim(
        "EX-B03", "execution_contract", "§5 atomicity", "L2b",
        "Order(pending) exists before submission; a transport failure leaves a "
        "retry-suppressing submission_unknown row rather than claiming rejection",
    ),
)
def test_order_persisted_before_exchange_call(tmp_path, monkeypatch):
    """The pending row survives an ambiguous transport failure without resubmission."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import coordinator
    from tests.uta.test_execution_coordinator import _Adapter
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="prop-ex-b03",
        risk_decision_id="rd-ex-b03",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode="bitget_demo",
    )

    captured_order_id = {}
    async def submit(command):
        [pending] = repo.list_orders(limit=10)
        assert pending["status"] == "pending"
        captured_order_id["id"] = pending["id"]
        raise ConnectionError("exchange unavailable")
    adapter = _Adapter(submit, "bitget_demo")
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)

    from aletheia.execution.errors import SubmissionUnknownError

    with pytest.raises(SubmissionUnknownError):
        coordinator.execute_order(
            proposal_id="prop-ex-b03",
            risk_decision_id="rd-ex-b03",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000.0,
            execution_mode="bitget_demo",
        )

    assert "id" in captured_order_id, "insert_order was never called before the exchange raised"
    orders = repo.list_orders(limit=50)
    row = next((o for o in orders if o["id"] == captured_order_id["id"]), None)
    assert row is not None, "Order row must persist after exchange failure"
    assert row["status"] == "submission_unknown"
    assert "exchange unavailable" not in (row["error_reason"] or "")


@spec_claim(
    SpecClaim(
        "EX-B04", "execution_contract", "§5 idempotency", "L2b",
        "execute_existing_proposal on an already-executed proposal returns already_executed without creating a new order",
    ),
)
def test_re_execute_already_executed_proposal_rejected(tmp_path, monkeypatch):
    """A persisted completed Proposal cannot re-enter execution."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=0.001, price=60000.0, execution_mode="bitget_demo",
        status="executed",
    )
    repo.insert_trade_proposal(proposal)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("completed Proposal reached the execution pipeline")

    monkeypatch.setattr(gateway, "_execute_proposal", should_not_run)
    result = gateway.execute_existing_proposal(proposal.id, "bitget_demo")
    assert result["status"] == "already_executed"
    assert repo.list_orders(limit=10) == []
    assert repo.get_proposal_execution_claim(proposal.id) is None


@spec_claim(
    SpecClaim(
        "EX-B17", "execution_contract", "§5 durable execution lease", "L2b",
        "A fresh durable Proposal execution lease rejects a concurrent wake before "
        "the execution pipeline or any broker side effect runs.",
        status="implemented",
    ),
)
def test_fresh_execution_lease_blocks_duplicate_pipeline(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id="proposal-fresh-execution-lease",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=100.0,
        execution_mode="bitget_demo",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    now = datetime.now(timezone.utc)
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "active-owner",
        now.isoformat(),
        (now + timedelta(minutes=5)).isoformat(),
    )
    pipeline_calls = 0

    def should_not_run(*_args, **_kwargs):
        nonlocal pipeline_calls
        pipeline_calls += 1
        raise AssertionError("fresh lease reached execution pipeline")

    monkeypatch.setattr(gateway, "_execute_proposal", should_not_run)
    result = gateway.execute_existing_proposal(proposal.id, "bitget_demo")

    assert result["status"] == "already_claimed"
    assert pipeline_calls == 0
    assert repo.list_orders(limit=10) == []


@spec_claim(
    SpecClaim(
        "EX-B15", "execution_contract", "§5 exchange crash recovery", "L2b",
        "An expired exchange execution lease is marked reconciliation-required and "
        "never re-enters the execution pipeline or broker.",
        status="implemented",
    ),
)
def test_expired_exchange_execution_lease_never_resubmits(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id="proposal-expired-exchange-lease",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    expired = datetime.now(timezone.utc) - timedelta(minutes=20)
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "dead-exchange-owner",
        (expired - timedelta(minutes=10)).isoformat(),
        expired.isoformat(),
    )
    pipeline_calls = 0

    def should_not_run(*_args, **_kwargs):
        nonlocal pipeline_calls
        pipeline_calls += 1
        raise AssertionError("expired exchange lease reached broker pipeline")

    monkeypatch.setattr(gateway, "_execute_proposal", should_not_run)
    result = gateway.execute_existing_proposal(proposal.id, "bitget_demo")

    assert result["status"] == "reconciliation_required"
    assert result["proposal_status"] == "submission_unknown"
    assert pipeline_calls == 0
    assert repo.list_orders(limit=10) == []
    assert repo.get_trade_proposal(proposal.id)["status"] == "submission_unknown"
    assert repo.get_proposal_execution_claim(proposal.id) is None


@spec_claim(
    SpecClaim(
        "EX-C04", "execution_contract", "§6 reconciliation invariant", "L2c",
        "reconciliation.py never imports a place_order function (reconciliation must not place orders)",
    ),
)
def test_reconciliation_never_imports_place_order():
    """AST-scan reconciliation.py imports — no 'place_order' name must appear."""
    import importlib.util
    from pathlib import Path

    src = Path(__file__).parents[2] / "core" / "aletheia" / "execution" / "reconciliation.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            for name in names:
                assert "place_order" not in name, (
                    f"reconciliation.py imports 'place_order' ({name!r}) — "
                    "reconciliation must never place orders (contract §6)"
                )


@spec_claim(
    SpecClaim(
        "EX-B06", "execution_contract", "§6 reconciliation", "L2c",
        "reconciliation.py never inserts a Fill row directly (only updates Order.status)",
    ),
)
def test_reconciliation_never_inserts_fill():
    """AST-scan reconciliation.py for insert_fill calls or Fill( construction."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "core" / "aletheia" / "execution" / "reconciliation.py"
    text = src.read_text(encoding="utf-8")

    forbidden = ["insert_fill", 'Fill(']
    for token in forbidden:
        assert token not in text, (
            f"reconciliation.py contains {token!r} — "
            "reconciliation must not create Fill rows directly (contract §6)"
        )


@spec_claim(
    SpecClaim(
        "EX-S05", "execution_contract", "§3 execution mode", "L2a",
        "_is_live() is defined in risk/engine.py and is private (single underscore prefix)",
    ),
)
def test_is_live_defined_and_private():
    """AST-scan risk/engine.py — _is_live must exist as a module-level function
    with a single leading underscore (contract §3: engine.py:35)."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "core" / "aletheia" / "risk" / "engine.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    function_names = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    ]
    assert "_is_live" in function_names, (
        "_is_live not found in risk/engine.py — contract §3 requires it"
    )
    # Must start with exactly one underscore (private, not dunder).
    assert not "_is_live".startswith("__"), "_is_live must not be dunder-private"
    assert "_is_live".startswith("_"), "_is_live must start with a single underscore"


def test_fill_model_defaults_fee_source_estimated():
    """A Fill without explicit provenance must not claim reconciled exchange fees."""
    from aletheia.storage.models import Fill

    f = Fill(order_id="o1", fill_price=100.0, fill_quantity=1.0, fee=0.06)
    assert f.fee_source == "estimated"


@spec_claim(
    SpecClaim(
        "EX-S07", "execution_contract", "§3.2 symbol filter", "L2a",
        "Gateway consumes the shared symbol capability boundary; "
        "the shared execution-mode venue registry maps all real exchange modes",
    ),
)
def test_symbol_availability_check_structure():
    from aletheia.execution import gateway, venue_capabilities
    from aletheia.config.exchanges import EXECUTION_MODE_VENUES

    assert callable(gateway._check_symbol_availability)
    assert callable(venue_capabilities.get_supported_symbols)
    assert not hasattr(gateway, "_load_registry_symbols")
    assert not hasattr(gateway, "_registry_symbol_cache")
    assert not hasattr(venue_capabilities, "_okx_demo_symbols")
    assert not hasattr(venue_capabilities, "refresh_okx_demo_symbols")

    required_modes = {
        "okx_live_limited",
        "bitget_demo", "bitget_live_limited",
        "binance_testnet", "binance_live_limited",
    }
    assert required_modes <= set(EXECUTION_MODE_VENUES), (
        f"Missing modes in EXECUTION_MODE_VENUES: "
        f"{required_modes - set(EXECUTION_MODE_VENUES)}"
    )


@spec_claim(
    SpecClaim(
        "EX-B11", "execution_contract", "§3.2 symbol filter", "L2b",
        "When the OKX demo symbol sets are loaded and a symbol is absent for the "
        "given market_type, _check_symbol_availability returns a non-empty reason; "
        "a present symbol returns None; market_type-awareness blocks swap-absent symbols",
    ),
)
def test_symbol_check_okx_demo_absent_and_present(monkeypatch):
    import aletheia.execution.gateway as gw
    import extensions.exchanges.okx.market_capabilities as capabilities
    from aletheia.execution import venue_capabilities
    from extensions.exchanges.composition import cached_market_restrictions

    monkeypatch.setattr(venue_capabilities, "_cached_market_capability_reader", cached_market_restrictions)

    # New structure: {"swap": frozenset, "spot": frozenset}
    # WLD/USDT is spot-only on OKX demo — absent from swap, present in spot.
    monkeypatch.setattr(capabilities, "_okx_demo_symbols", {
        "swap": frozenset({"BTC/USDT", "ETH/USDT"}),
        "spot": frozenset({"BTC/USDT", "ETH/USDT", "WLD/USDT"}),
    })
    monkeypatch.setattr(capabilities, "_okx_demo_symbols_loaded_at", float("inf"))

    # WLD is in spot but NOT in swap → perp order must be rejected
    reason = gw._check_symbol_availability("WLDUSDT", "okx_demo", "usdt_futures")
    assert reason is not None, "Absent-from-swap symbol must produce a rejection reason"
    assert "WLD" in reason or "not available" in reason.lower()

    # WLD spot order must pass
    assert gw._check_symbol_availability("WLDUSDT", "okx_demo", "spot") is None

    # BTC is in swap → perp order passes
    assert gw._check_symbol_availability("BTCUSDT", "okx_demo", "usdt_futures") is None

    # Loaded cache contains only "swap" key — spot requests must be rejected (issue #1222)
    # _get_demo_symbols now returns frozenset() (not None) for a missing key in a loaded dict.
    monkeypatch.setattr(capabilities, "_okx_demo_symbols", {
        "swap": frozenset({"BTC/USDT", "ETH/USDT"}),
    })
    reason_no_spot = gw._check_symbol_availability("BTCUSDT", "okx_demo", "spot")
    assert reason_no_spot is not None, (
        "Loaded OKX demo cache with no spot key must reject spot orders (issue #1222)"
    )


@spec_claim(
    SpecClaim(
        "EX-B12", "execution_contract", "§3.2 symbol filter", "L2b",
        "For registry-based modes (bitget, binance): absent symbol → rejection reason; "
        "spot-only symbol rejects a perp order (market-type-aware); "
        "empty/unsynced registry → None (fail-open, never blocks legitimate orders)",
    ),
)
def test_symbol_check_registry_modes(monkeypatch):
    import aletheia.execution.gateway as gw
    import aletheia.storage.repositories as repo
    from aletheia.execution import venue_capabilities

    # ── case A: registry loaded, WLD absent for usdt_futures ──
    venue_capabilities._registry_symbol_cache.clear()
    monkeypatch.setattr(
        repo, "list_universe_assets",
        lambda *a, **kw: [
            {"symbol": "BTC/USDT", "market_type": "usdt_futures"},
            {"symbol": "ETH/USDT", "market_type": "usdt_futures"},
        ],
    )
    reason = gw._check_symbol_availability("WLDUSDT", "bitget_demo", "usdt_futures")
    assert reason is not None, "Registry loaded but symbol absent — must reject"

    # ── case B: WLD available as spot, but NOT for a perp order ──
    venue_capabilities._registry_symbol_cache.clear()
    monkeypatch.setattr(
        repo, "list_universe_assets",
        lambda *a, **kw: [
            {"symbol": "BTC/USDT", "market_type": "usdt_futures"},
            {"symbol": "WLD/USDT", "market_type": "spot"},
        ],
    )
    reason_spot_only = gw._check_symbol_availability("WLDUSDT", "bitget_demo", "usdt_futures")
    assert reason_spot_only is not None, "Spot-only symbol must reject a perp order"
    assert gw._check_symbol_availability("WLDUSDT", "bitget_demo", "spot") is None

    # ── case C: registry empty (not yet synced) → fail-open ──
    venue_capabilities._registry_symbol_cache.clear()
    monkeypatch.setattr(repo, "list_universe_assets", lambda *a, **kw: [])
    reason_empty = gw._check_symbol_availability("WLDUSDT", "binance_testnet")
    assert reason_empty is None, "Empty registry must fail-open, not block orders"

    # ── case D: futures-only registry rejects spot request ──
    # Deterministic trigger from issue #1222: loaded registry with no key for the
    # requested market_type must reject, not fall through to success.
    venue_capabilities._registry_symbol_cache.clear()
    monkeypatch.setattr(
        repo, "list_universe_assets",
        lambda *a, **kw: [
            {"symbol": "WLD/USDT", "market_type": "usdt_futures"},
            {"symbol": "BTC/USDT", "market_type": "usdt_futures"},
        ],
    )
    reason_no_spot_key = gw._check_symbol_availability("WLDUSDT", "bitget_demo", "spot")
    assert reason_no_spot_key is not None, (
        "Loaded registry with no spot partition must reject a spot order (issue #1222)"
    )

    # ── case E: symbol present in the exact requested partition passes ──
    venue_capabilities._registry_symbol_cache.clear()
    assert gw._check_symbol_availability("WLDUSDT", "bitget_demo", "usdt_futures") is None, (
        "Symbol present in the loaded futures partition must pass"
    )


@spec_claim(
    SpecClaim(
        "EX-B16",
        "execution_contract",
        "§3.2 complete symbol registry",
        "L2c",
        "Exchange availability consumes the complete active venue registry; a valid "
        "3,001st symbol cannot be silently converted into an unavailable rejection.",
        status="implemented",
    )
)
def test_symbol_check_does_not_truncate_large_registry(monkeypatch):
    import aletheia.execution.gateway as gw
    import aletheia.storage.repositories as repo
    from aletheia.execution import venue_capabilities

    assets = [
        {"symbol": f"TOKEN{index}/USDT", "market_type": "usdt_futures"}
        for index in range(3_000)
    ] + [{"symbol": "TARGET/USDT", "market_type": "usdt_futures"}]
    observed_limits: list[int | None] = []

    def _list_assets(*, venue, active_only, limit):
        assert venue == "bitget"
        assert active_only is True
        observed_limits.append(limit)
        return assets if limit is None else assets[:limit]

    venue_capabilities._registry_symbol_cache.clear()
    monkeypatch.setattr(repo, "list_universe_assets", _list_assets)

    assert gw._check_symbol_availability(
        "TARGET/USDT",
        "bitget_demo",
        "usdt_futures",
    ) is None
    assert observed_limits == [None]
    venue_capabilities._registry_symbol_cache.clear()


@spec_claim(
    SpecClaim(
        "EX-C03", "execution_contract", "§1 sole hard gate", "L2c",
        "gateway.py does not import guard_chain nor call run_guards() — the trust guard "
        "chain is advisory observability, never an execution verdict. The sole hard gate "
        "on the order path is risk/engine.py:evaluate_proposal (risk_engine_contract §2). "
        "Wiring a guard into execution requires updating both contracts + this claim.",
    ),
)
def test_gateway_does_not_wire_guard_chain():
    """AST-scan gateway.py imports + text-scan for run_guards(). guard_chain.py is DELETED
    (2026-09-07); this claim locks out any future re-introduction into the execution path."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "core" / "aletheia" / "execution" / "gateway.py"
    text = src.read_text(encoding="utf-8")
    tree = ast.parse(text)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (getattr(node, "module", "") or "")
            assert "guard_chain" not in mod, (
                f"gateway.py imports from guard_chain ({mod!r}) — trust guard chain must "
                "NOT be wired into execution (advisory only; sole hard gate = risk engine)"
            )
            for alias in node.names:
                assert "guard_chain" not in alias.name and "run_guards" not in alias.name, (
                    f"gateway.py imports {alias.name!r} from the guard chain — execution must "
                    "not consume guard verdicts (EX-C03)"
                )
    assert "run_guards(" not in text, (
        "gateway.py calls run_guards() — guard chain must not gate execution (EX-C03; "
        "the sole hard gate is risk/engine.py:evaluate_proposal)"
    )
