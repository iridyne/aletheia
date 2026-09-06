"""Small credential-free proof of the production ownership composition."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import AgentRun, Order, Position


pytestmark = [pytest.mark.production_parity, pytest.mark.real_db]


@pytest.fixture(autouse=True)
def production_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as store
    from aletheia.agent_runtime.runtime_session import (
        end_runtime_session,
        reset_current_session,
        start_runtime_session,
    )
    from aletheia.storage.migrations import apply_migrations

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(store, "db_path", lambda: tmp_path / "app.db")
    store.reset_db()
    store._db_conn = None
    apply_migrations()
    reset_current_session()
    start_runtime_session(loader.load_config())
    yield
    end_runtime_session()
    store.reset_db()


def test_fresh_and_representative_upgrade_keep_lifecycle_authority(tmp_path):
    from aletheia.storage import migrations
    from aletheia.storage.sqlite import get_db

    db = get_db()
    assert migrations.migration_status()["schema_version"] == migrations.SCHEMA_VERSION
    assert migrations.migration_status()["current"] is True
    assert db.execute(
        "SELECT authority_source FROM observation_authority_state "
        "WHERE state_key='experiment_authority'"
    ).fetchone()[0] == "lab"
    assert "idx_agent_runs_one_active_per_agent" in {
        row[1] for row in db.execute("PRAGMA index_list(agent_runs)")
    }

    upgrade = sqlite3.connect(tmp_path / "representative-upgrade.db", isolation_level=None)
    upgrade.row_factory = sqlite3.Row
    upgrade.execute("PRAGMA foreign_keys=ON")
    for step in migrations._MIGRATIONS:
        if step.schema_version == "aletheia-sqlite-v71":
            break
        step.apply(upgrade)
    upgrade.execute(
        "INSERT INTO agent_runs "
        "(id,agent_name,trigger_type,permission_mode,started_at,status) "
        "VALUES ('run-legacy','market_observer','manual','Observe',datetime('now'),'running')"
    )
    upgrade.execute(
        "INSERT INTO orders "
        "(id,symbol,market_type,direction,order_type,quantity,price,execution_mode,status,source) "
        "VALUES ('order-legacy','BTC/USDT','spot','long','market',1,100,'paper_internal','filled','adopted')"
    )
    upgrade.execute(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,execution_mode,status,source) "
        "VALUES ('position-legacy','BTC/USDT','spot','long',1,100,'paper_internal','open','adopted')"
    )
    migrations._apply_agent_run_lifecycle_v71(upgrade)
    migrations._apply_order_lifecycle_v72(upgrade)
    migrations._apply_position_lifecycle_v73(upgrade)
    assert upgrade.execute(
        "SELECT status FROM agent_runs WHERE id='run-legacy'"
    ).fetchone()[0] == "stale"
    for table, row_id in (
        ("agent_runs", "run-legacy"),
        ("orders", "order-legacy"),
        ("positions", "position-legacy"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            upgrade.execute(f"UPDATE {table} SET status='invented' WHERE id=?", (row_id,))
    assert not upgrade.execute("PRAGMA foreign_key_check").fetchall()
    upgrade.close()


@pytest.mark.parametrize(
    "trigger_types",
    (("manual", "manual"), ("manual", "scheduled")),
)
def test_real_writer_lane_admits_one_agent_run(trigger_types):
    barrier = threading.Barrier(2)

    def claim(trigger_type: str):
        run = AgentRun(
            agent_name="market_observer",
            trigger_type=trigger_type,
            permission_mode="Observe",
            status="queued",
        )
        barrier.wait(timeout=3)
        return run.id, repo.claim_agent_run(run)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, trigger_types))

    active = repo.list_agent_runs_window(hours=1)
    assert len(active) == 1
    assert active[0]["status"] == "queued"
    assert sum(conflict is None for _run_id, conflict in results) == 1
    assert {conflict for _run_id, conflict in results if conflict} == {active[0]["id"]}


def test_real_writer_lane_serializes_order_and_position_owners():
    order = Order(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1,
        price=100,
        execution_mode="paper_internal",
        source="adopted",
    )
    repo.insert_order(order)
    order_barrier = threading.Barrier(2)

    def transition(status: str):
        order_barrier.wait(timeout=3)
        try:
            repo.transition_order(order.id, status=status)
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(transition, ("cancelled", "filled")))
    assert repo.get_order(order.id)["status"] == "filled"

    position = Position(
        id="position-parity",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1,
        entry_price=100,
        execution_mode="paper_internal",
    )
    repo.insert_position(position)
    close_orders = []
    for suffix in ("manual", "scheduled"):
        close_order = Order(
            id=f"close-{suffix}",
            position_id=position.id,
            symbol=position.symbol,
            market_type=position.market_type,
            direction=position.direction,
            order_type="market",
            quantity=position.quantity,
            price=position.entry_price,
            execution_mode=position.execution_mode,
            source="adopted",
        )
        repo.insert_order(close_order)
        close_orders.append(close_order)
    position_barrier = threading.Barrier(2)

    def claim(close_order: Order):
        position_barrier.wait(timeout=3)
        try:
            repo.claim_position_close(
                position.id,
                order_id=close_order.id,
                status="pending",
                requested_at=datetime.now(timezone.utc).isoformat(),
            )
        except repo.PositionClaimConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, close_orders))
    stored = repo.get_position(position.id)
    assert sum(claims) == 1
    assert stored["pending_close_order_id"] in {order.id for order in close_orders}


@pytest.mark.parametrize("source", ("legacy", "lab"))
def test_observation_owner_matrix_selects_one_epoch(monkeypatch, source):
    from aletheia.observation_lab import canonical
    from aletheia.observation_lab.experiment_owner import (
        require_experiment_mutation_owner,
        select_experiment_mutation_owner,
    )

    authority = canonical.AuthoritySource(
        source, f"{source}:parity", "test", "parity", datetime.now(timezone.utc).isoformat()
    )
    monkeypatch.setattr(canonical, "get_authority_source", lambda **_kwargs: authority)
    selected = select_experiment_mutation_owner()
    assert (selected.source, selected.epoch) == (source, authority.epoch)
    require_experiment_mutation_owner(source, authority.epoch)


@pytest.mark.parametrize(
    "boundary,owner,shadow",
    (("legacy", "legacy", False), ("shadow_read", "legacy", True), ("uta_v1", "uta", False)),
)
def test_uta_owner_matrix_keeps_reads_and_mutations_on_one_account(
    monkeypatch, boundary, owner, shadow
):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.uta.manager import select_account_port

    monkeypatch.setattr("aletheia.uta.manager.get_credential_generation", lambda _scope: 3)
    cfg = AletheiaConfig()
    cfg.exchange.uta_read_boundaries = {"okx_demo": boundary}
    cfg.exchange.uta_account_config_generations = {"okx_demo": 5}
    selected = select_account_port("okx_demo", cfg)
    assert selected.authoritative_read_owner == owner
    assert selected.mutation_owner == owner
    assert selected.shadow_read_enabled is shadow
    assert selected.identity.credential_generation > 5


def test_public_universe_candidate_reaches_compiled_plan_and_monitor(monkeypatch):
    import asyncio

    from aletheia.agent_runtime.fast_trigger_watch import _load_entry_plan_watchers
    from aletheia.agent_shell.entry_planner import plan_next_candidate
    from aletheia.config.loader import load_config
    from aletheia.learning.entry_context import empty_entry_learning_context
    from aletheia.trading.entry_analysis import (
        EntryAnalysisContext,
        EntryAnchor,
        EntryHypothesis,
    )
    from aletheia.trading.entry_contract import entry_prompt_fingerprint
    from tests.test_candidate_selection_utility import (
        _controlled_admitted_target_candidate,
    )

    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.regime.enabled = False
    candidate = _controlled_admitted_target_candidate(
        cfg,
        monkeypatch,
        mechanism="continuation",
        direction="long",
        market_type="spot",
    )
    generation_rows = repo.get_db().execute(
        "SELECT * FROM candidate_planning_generations "
        "WHERE last_candidate_id=? AND is_current=1",
        (candidate.id,),
    ).fetchall()
    assert len(generation_rows) == 1
    generation = dict(generation_rows[0])
    observed_at = datetime.now(timezone.utc)
    primary = EntryAnchor(
        venue=candidate.venue,
        symbol=candidate.symbol,
        market_type=candidate.market_type,
        kind="causal_breakout",
        price=float(candidate.entry_price_usd),
        directional_role="entry_candidate",
        known_at_ms=int(observed_at.timestamp() * 1000),
        source_fact_refs=(f"candidate:{candidate.id}",),
        source_fingerprint="candidate-public-fixture",
    )
    invalidation = EntryAnchor(
        venue=candidate.venue,
        symbol=candidate.symbol,
        market_type=candidate.market_type,
        kind="confirmed_swing",
        price=float(candidate.entry_price_usd) * 0.95,
        directional_role="invalidation_candidate",
        known_at_ms=int(observed_at.timestamp() * 1000),
        source_fact_refs=("public-fixture:swing",),
        source_fingerprint="public-swing-fixture",
    )
    analysis = EntryAnalysisContext(
        analysis_cutoff_at=observed_at.isoformat(),
        instrument={
            "venue": candidate.venue,
            "symbol": candidate.symbol,
            "market_type": candidate.market_type,
            "direction": candidate.scanner_direction,
        },
        planning_generation={
            "id": generation["id"],
            "candidate_id": candidate.id,
            "candidate_family": candidate.scanner_family,
            "cycle_number": generation["cycle_number"],
            "entry_behavior_fingerprint": generation["entry_behavior_fingerprint"],
            "schema_fingerprint": generation["schema_fingerprint"],
        },
        candidate={
            "candidate_price": candidate.entry_price_usd,
            "fact_refs": [f"candidate:{candidate.id}"],
        },
        candidate_opportunity_context=json.loads(
            candidate.candidate_opportunity_context_json
        ),
        inference_identity={
            "caller": "agent_shell.entry_planner",
            "prompt_fingerprint": entry_prompt_fingerprint(),
            "model_provider": "public-fixture",
        },
        candle_windows={
            "4h": {"availability": "unavailable", "bars": []},
            "1h": {"availability": "unavailable", "bars": []},
            "15m": {"availability": "available", "bars": [{} for _ in range(96)]},
        },
        market_structure={
            "15m": {"availability": "available", "atr14": 2.0, "break_tolerance": 0.1},
            "fact_refs": [invalidation.anchor_id],
        },
        quant_context={"values": {"atr_pct": 2.0}, "fact_refs": []},
        portfolio_context={"availability": "available", "fact_refs": []},
        execution_context={
            "availability": "available",
            "bid": float(candidate.entry_price_usd) - 0.1,
            "ask": float(candidate.entry_price_usd) + 0.1,
            "spread_bps": 2,
            "depth_10bps_usd": 100_000,
            "estimated_costs": 0.1,
            "fact_refs": [],
        },
        anchors=(primary, invalidation),
        entry_profiles=(
            {"id": "grounded-anchor-resting-maker"},
            {"id": "grounded-anchor-retrace-reclaim"},
        ),
        learning_context=empty_entry_learning_context(
            as_of_at=observed_at.isoformat(),
            mechanism="continuation",
            direction="long",
            market_type="spot",
            policy=candidate.candidate_selection_policy,
            policy_epoch=candidate.candidate_selection_policy_epoch,
        ),
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.entry_planner.build_entry_analysis_context",
        lambda *_args, **_kwargs: analysis,
    )

    planner_view = {}

    async def deterministic_planner(payload):
        context = EntryAnalysisContext.model_validate(payload)
        planner_view.update(
            profiles=[item["id"] for item in context.entry_profiles],
            anchors=[
                (anchor.kind, anchor.directional_role, anchor.price)
                for anchor in context.anchors
            ],
        )
        profile = next(
            item
            for item in context.entry_profiles
            if item["id"] == "grounded-anchor-retrace-reclaim"
        )
        primary = next(
            anchor
            for anchor in context.anchors
            if anchor.directional_role == "entry_candidate"
        )
        invalidation = next(
            anchor
            for anchor in context.anchors
            if anchor.directional_role == "invalidation_candidate"
        )
        return EntryHypothesis(
            decision="ENTRY_PLAN",
            reason_code="OK",
            mechanism="continuation",
            direction="long",
            primary_anchor_ref=primary.anchor_id,
            invalidation_anchor_ref=invalidation.anchor_id,
            entry_profile_id=profile["id"],
            execution_urgency="BOUNDED_URGENT",
            risk_tier="LOW",
            horizon="INTRADAY",
            supporting_fact_refs=(primary.anchor_id,),
        )

    result = asyncio.run(
        plan_next_candidate(
            deterministic_planner,
            cfg=cfg,
            owner_token="production-parity-planner",
        )
    )
    stored_generation = repo.get_candidate_planning_generation(generation_rows[0]["id"])
    assert result["status"] == "planned", (
        stored_generation["result_json"],
        planner_view,
    )
    plan = repo.get_entry_plan(result["entry_plan_id"])
    assert plan is not None and plan["planning_generation_id"] == generation_rows[0]["id"]

    plans, engines = asyncio.run(_load_entry_plan_watchers(cfg))
    assert result["entry_plan_id"] in plans
    assert result["entry_plan_id"] in engines


def test_parity_sources_cannot_use_inmemory_or_seed_objects_under_proof():
    sources = (
        Path(__file__),
        Path(__file__).parent / "e2e" / "test_active_mainline_golden_path.py",
    )
    forbidden = (
        ":" + "memory:",
        "insert_" + "candidate(",
        "insert_" + "entry_plan(",
    )
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), source
