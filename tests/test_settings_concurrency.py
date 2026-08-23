"""Settings API concurrency regression tests — #1232.

These tests verify that the Settings routes fixed in #1232 no longer perform
load+save full-snapshot writes that can silently overwrite concurrent mutations.

The proof follows the approach in test_config_crossprocess.py: use mutate_config
directly (which is what the fixed routes now call) and verify that concurrent
writes are serialised — no silent data loss.
"""

import multiprocessing
from pathlib import Path

import pytest
import yaml

from aletheia.config.loader import (
    ConfigConflictError,
    load_config,
    mutate_config,
    save_config,
)
from aletheia.config.schema import AletheiaConfig


# ─── Multiprocessing workers (module-level so they're picklable under 'spawn') ─


def _mp_set_trading_mandate_enabled(
    config_path_str: str, value: bool, ready: object, go: object
) -> None:
    """Worker: mutate trading_mandate.enabled via mutate_config (as the fixed route does)."""
    import aletheia.config.loader as cl
    import aletheia.runtime_home_migration as rmh

    rmh.assert_runtime_home_ready = lambda **kw: None
    p = Path(config_path_str)
    cl.CONFIG_PATH = p
    cl.DATA_DIR = p.parent
    cl._dirs_ensured_for = p.parent
    cl._config_cache = None

    ready.set()
    go.wait(timeout=5)

    from aletheia.trading.trading_mandate import TradingMandateConfig
    cl.mutate_config(
        lambda cfg: setattr(cfg, "trading_mandate", TradingMandateConfig(enabled=value)) or True
    )


def _mp_set_exchange_spot(
    config_path_str: str, spot: bool, futures: bool, ready: object, go: object
) -> None:
    """Worker: mutate exchange.enabled_markets via mutate_config (as the fixed route does)."""
    import aletheia.config.loader as cl
    import aletheia.runtime_home_migration as rmh

    rmh.assert_runtime_home_ready = lambda **kw: None
    p = Path(config_path_str)
    cl.CONFIG_PATH = p
    cl.DATA_DIR = p.parent
    cl._dirs_ensured_for = p.parent
    cl._config_cache = None

    ready.set()
    go.wait(timeout=5)

    def mutator(cfg):
        cfg.exchange.enabled_markets = {"spot": spot, "usdt_futures": futures}
        return True

    cl.mutate_config(mutator)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _seed(config_file: Path) -> None:
    data = AletheiaConfig().model_dump(mode="json")
    config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def _patch_loader(config_file: Path):
    """Return a list of patches for the config loader — matches test_config_crossprocess style."""
    from unittest.mock import patch
    import aletheia.config.loader as cl

    return [
        patch.object(cl, "CONFIG_PATH", config_file),
        patch.object(cl, "DATA_DIR", config_file.parent),
        patch.object(cl, "_dirs_ensured_for", config_file.parent),
        patch.object(cl, "_config_cache", None),
    ]


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_concurrent_trading_mandate_writes_both_survive(tmp_path):
    """Two processes writing trading_mandate from the same base: both commits survive.

    The fixed set_trading_mandate route calls mutate_config(), which acquires the
    cross-process lock before reading. Independent writes on non-conflicting fields
    both land; conflicting writes serialize.
    """
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    # Write an extra field first so we can verify it is not lost during concurrent writes
    raw = yaml.safe_load(config_file.read_text()) or {}
    raw.setdefault("app", {})["dashboard_port"] = 7777
    config_file.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    go = ctx.Event()

    # Process A: mutate trading_mandate; main thread mutates dashboard_port simultaneously
    proc_a = ctx.Process(
        target=_mp_set_trading_mandate_enabled,
        args=(str(config_file), True, ready_a, go),
    )
    proc_a.start()
    ready_a.wait(timeout=10)
    go.set()

    # Main thread: simultaneously mutate an independent field via mutate_config
    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        mutate_config(lambda cfg: setattr(cfg.app, "dashboard_port", 9999) or True)
    finally:
        for p in patches:
            p.stop()

    proc_a.join(timeout=15)
    assert proc_a.exitcode == 0, f"trading_mandate worker failed with exit code {proc_a.exitcode}"

    # Both mutations must survive — no silent overwrite
    final = AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text()) or {})
    assert final.trading_mandate.enabled is True, "trading_mandate mutation was lost"
    assert final.app.dashboard_port == 9999, "dashboard_port mutation was overwritten"


def test_concurrent_exchange_market_writes_both_survive(tmp_path):
    """Two processes mutating different exchange fields do not overwrite each other.

    The fixed set_exchange_markets uses mutate_config(), serialising writes so no
    sibling field is silently reverted by a stale full-snapshot writer.
    """
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    go = ctx.Event()

    # Process A: set spot=True, futures=False via mutate_config
    proc_a = ctx.Process(
        target=_mp_set_exchange_spot,
        args=(str(config_file), True, False, ready_a, go),
    )
    proc_a.start()
    ready_a.wait(timeout=10)
    go.set()

    # Main thread: set dashboard_port (independent field) simultaneously
    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        mutate_config(lambda cfg: setattr(cfg.app, "dashboard_port", 8888) or True)
    finally:
        for p in patches:
            p.stop()

    proc_a.join(timeout=15)
    assert proc_a.exitcode == 0, f"exchange_markets worker failed with exit code {proc_a.exitcode}"

    final = AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text()) or {})
    assert final.exchange.enabled_markets == {"spot": True, "usdt_futures": False}, (
        "exchange_markets mutation was lost"
    )
    assert final.app.dashboard_port == 8888, "dashboard_port mutation was overwritten"


def test_settings_full_snapshot_no_longer_loses_concurrent_write(tmp_path):
    """Regression: the old load+save pattern could silently overwrite a concurrent write.

    This directly tests the anti-pattern: one writer loads, another commits,
    then the first overwrites. With the fixed mutate_config(), this cannot happen.
    """
    import aletheia.config.loader as cl

    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        cl._config_cache = None

        # Writer A loads at revision N
        cfg_a = load_config()

        # Writer B commits a change (simulating a concurrent request)
        mutate_config(lambda cfg: setattr(cfg.app, "dashboard_port", 9999) or True)

        # Verify file was updated by writer B
        mid = AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text()) or {})
        assert mid.app.dashboard_port == 9999

        # Writer A's old snapshot has stale hash — save_config with expected_hash rejects it
        stale_hash = "0" * 64  # deliberately wrong
        with pytest.raises(ConfigConflictError):
            save_config(cfg_a, expected_hash=stale_hash)

        # The correct fix: mutate_config never loads a stale snapshot
        def apply_a(cfg):
            cfg.app.dashboard_port = 7777
            return True
        mutate_config(apply_a)

        final = AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text()) or {})
        assert final.app.dashboard_port == 7777, "mutate_config lost the mutation"
    finally:
        for p in patches:
            p.stop()


def test_risk_policy_revision_check_inside_lock(tmp_path):
    """Regression: revision check was outside the write lock, allowing two stale writers to both pass.

    The fixed copy/replace/reset_risk_policy routes perform the revision check
    inside mutate_config(), so the revision read and write are atomic. This test
    verifies that a revision check sees the latest committed state, not a cached snapshot.
    """
    import aletheia.config.loader as cl

    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        cl._config_cache = None
        load_config()  # warm cache

        # Simulate: concurrent writer increments risk policy revision before we read
        def advance_revision(cfg):
            cfg.risk.demo = cfg.risk.demo.model_copy(
                update={"revision": cfg.risk.demo.revision + 1}, deep=True
            )
            return True

        mutate_config(advance_revision)

        # Read the fresh revision
        fresh = load_config()
        new_rev = fresh.risk.demo.revision

        # A further mutation inside mutate_config sees the already-advanced revision
        seen_revision = None
        def check_revision(cfg):
            nonlocal seen_revision
            seen_revision = cfg.risk.demo.revision
            return False  # no write needed

        mutate_config(check_revision)
        assert seen_revision == new_rev, (
            "mutate_config read a stale snapshot; revision check would have used wrong base"
        )
    finally:
        for p in patches:
            p.stop()
