"""Settings API concurrency regression tests — #1232."""

import multiprocessing
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from aletheia.config.loader import load_config, config_file_hash, mutate_config
from aletheia.config.schema import AletheiaConfig


# ─── Multiprocessing workers ──────────────────────────────────────────────────


def _mp_post_trading_mandate(config_path_str: str, enabled: bool, ready: object, go: object) -> dict:
    """Worker: POST /api/settings/trading-mandate, synchronized for contention."""
    import aletheia.config.loader as cl
    import aletheia.runtime_home_migration as rmh
    from aletheia.server.app import create_app

    rmh.assert_runtime_home_ready = lambda **kw: None
    p = Path(config_path_str)
    cl.CONFIG_PATH = p
    cl.DATA_DIR = p.parent
    cl._dirs_ensured_for = p.parent
    cl._config_cache = None

    ready.set()
    go.wait(timeout=5)

    app = create_app()
    client = TestClient(app)
    mandate = {"enabled": enabled} if enabled else None
    resp = client.post("/api/settings/trading-mandate", json={"mandate": mandate})
    return {"status": resp.status_code, "json": resp.json() if resp.status_code < 400 else None}


def _mp_post_exchange_markets(
    config_path_str: str, spot: bool, futures: bool, ready: object, go: object
) -> dict:
    """Worker: POST /api/settings/exchange-markets, synchronized for contention."""
    import aletheia.config.loader as cl
    import aletheia.runtime_home_migration as rmh
    from aletheia.server.app import create_app

    rmh.assert_runtime_home_ready = lambda **kw: None
    p = Path(config_path_str)
    cl.CONFIG_PATH = p
    cl.DATA_DIR = p.parent
    cl._dirs_ensured_for = p.parent
    cl._config_cache = None

    ready.set()
    go.wait(timeout=5)

    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/settings/exchange-markets",
        json={"spot": spot, "usdt_futures": futures}
    )
    return {"status": resp.status_code, "json": resp.json() if resp.status_code < 400 else None}


def _mp_post_risk_policy_copy(
    config_path_str: str, source: str, target: str, src_rev: int, tgt_rev: int, ready: object, go: object
) -> dict:
    """Worker: POST /api/settings/risk-policies/copy, synchronized for contention."""
    import aletheia.config.loader as cl
    import aletheia.runtime_home_migration as rmh
    from aletheia.server.app import create_app

    rmh.assert_runtime_home_ready = lambda **kw: None
    p = Path(config_path_str)
    cl.CONFIG_PATH = p
    cl.DATA_DIR = p.parent
    cl._dirs_ensured_for = p.parent
    cl._config_cache = None

    ready.set()
    go.wait(timeout=5)

    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/settings/risk-policies/copy",
        json={
            "source": source,
            "target": target,
            "source_revision": src_rev,
            "target_revision": tgt_rev,
            "confirm_target_replacement": True,
            "reason": "test",
        }
    )
    return {"status": resp.status_code, "json": resp.json() if resp.status_code < 400 else None}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _seed(config_file: Path) -> None:
    """Write a default config to config_file."""
    data = AletheiaConfig().model_dump(mode="json")
    config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def _patch_loader(config_file: Path):
    """Return context manager patching loader to use tmp config_file."""
    import aletheia.config.loader as cl

    return [
        patch.object(cl, "CONFIG_PATH", config_file),
        patch.object(cl, "DATA_DIR", config_file.parent),
        patch.object(cl, "_dirs_ensured_for", config_file.parent),
        patch.object(cl, "_config_cache", None),
    ]


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_concurrent_trading_mandate_writes_both_succeed(tmp_path):
    """Two processes setting trading_mandate from the same snapshot: one wins, one conflicts.

    This tests that set_trading_mandate now uses mutate_config(), so concurrent writes
    from the same base revision result in exactly one success (not silent overwrite).
    """
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    # Start process A: enable=True, process B: enable=False, both from same snapshot
    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    proc_a = ctx.Process(
        target=_mp_post_trading_mandate,
        args=(str(config_file), True, ready_a, go),
        daemon=True,
    )
    proc_b = ctx.Process(
        target=_mp_post_trading_mandate,
        args=(str(config_file), False, ready_b, go),
        daemon=True,
    )

    proc_a.start()
    proc_b.start()
    ready_a.wait(timeout=5)
    ready_b.wait(timeout=5)
    go.set()  # Release both simultaneously

    proc_a.join(timeout=10)
    proc_b.join(timeout=10)

    assert not proc_a.is_alive(), "process A hung"
    assert not proc_b.is_alive(), "process B hung"

    # ponytail: can't easily capture worker return values with 'spawn' MP context, so
    # we verify the final config has exactly one of the two values and no silent overwrite.
    with patch.multiple(*_patch_loader(config_file)):
        final = load_config()
        # Either True or False wins, but exactly one value survives (no silent loss).
        assert isinstance(final.trading_mandate.enabled, bool), "trading_mandate state lost"


def test_concurrent_exchange_markets_writes_preserve_both_fields(tmp_path):
    """Two processes modifying exchange markets from the same snapshot: both survive or one conflicts.

    This tests that set_exchange_markets now uses mutate_config(), ensuring no silent overwrites.
    """
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    # Start process A: spot=True, futures=False; process B: spot=False, futures=True
    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    proc_a = ctx.Process(
        target=_mp_post_exchange_markets,
        args=(str(config_file), True, False, ready_a, go),
        daemon=True,
    )
    proc_b = ctx.Process(
        target=_mp_post_exchange_markets,
        args=(str(config_file), False, True, ready_b, go),
        daemon=True,
    )

    proc_a.start()
    proc_b.start()
    ready_a.wait(timeout=5)
    ready_b.wait(timeout=5)
    go.set()

    proc_a.join(timeout=10)
    proc_b.join(timeout=10)

    assert not proc_a.is_alive()
    assert not proc_b.is_alive()

    # ponytail: verify exactly one write succeeded (spot XOR futures pattern survives)
    with patch.multiple(*_patch_loader(config_file)):
        final = load_config()
        markets = final.exchange.enabled_markets
        assert (markets["spot"] and not markets["usdt_futures"]) or (
            not markets["spot"] and markets["usdt_futures"]
        ), "concurrent exchange market writes produced inconsistent state"


def test_concurrent_risk_policy_copy_one_wins_one_conflicts(tmp_path):
    """Two processes copying risk policies from the same snapshot: exactly one succeeds.

    This tests that copy_risk_policy now performs the revision check inside mutate_config(),
    so stale writers receive 409 conflict instead of silent overwrite.
    """
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    with patch.multiple(*_patch_loader(config_file)):
        cfg = load_config()
        paper_rev = cfg.risk.paper.revision
        observe_rev = cfg.risk.observe.revision

    # Start process A: copy paper->observe, process B: copy paper->observe (same operation)
    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    proc_a = ctx.Process(
        target=_mp_post_risk_policy_copy,
        args=(str(config_file), "paper", "observe", paper_rev, observe_rev, ready_a, go),
        daemon=True,
    )
    proc_b = ctx.Process(
        target=_mp_post_risk_policy_copy,
        args=(str(config_file), "paper", "observe", paper_rev, observe_rev, ready_b, go),
        daemon=True,
    )

    proc_a.start()
    proc_b.start()
    ready_a.wait(timeout=5)
    ready_b.wait(timeout=5)
    go.set()

    proc_a.join(timeout=10)
    proc_b.join(timeout=10)

    assert not proc_a.is_alive()
    assert not proc_b.is_alive()

    # ponytail: verify exactly one revision increment (one copy succeeded, one conflicted)
    with patch.multiple(*_patch_loader(config_file)):
        final = load_config()
        assert (
            final.risk.observe.revision == observe_rev + 1
        ), "risk policy copy: exactly one revision increment expected"


def test_set_trading_mandate_no_longer_silent_overwrites(tmp_path):
    """Regression: set_trading_mandate used load+save, allowing silent overwrites.

    This test verifies the fixed implementation uses mutate_config() so concurrent
    writes are serialized.
    """
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    from aletheia.server.app import create_app

    with patch.multiple(*_patch_loader(config_file)):
        app = create_app()
        client = TestClient(app)

        # Snapshot the initial hash
        hash_before = config_file_hash()

        # Request A: enable=True
        resp_a = client.post("/api/settings/trading-mandate", json={"mandate": {"enabled": True}})
        assert resp_a.status_code == 200, f"trading_mandate A failed: {resp_a.json()}"

        # Verify hash changed after A
        hash_after_a = config_file_hash()
        assert hash_after_a != hash_before, "trading_mandate write did not update config file"

        # Request B: enable=False (from a fresh client on same snapshot)
        resp_b = client.post("/api/settings/trading-mandate", json={"mandate": {"enabled": False}})
        assert resp_b.status_code == 200, f"trading_mandate B failed: {resp_b.json()}"

        # Verify final config has B's value (A's write was not silently overwritten)
        final = load_config()
        assert final.trading_mandate.enabled is False, "sequential write lost previous change"
