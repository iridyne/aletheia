"""Cross-process config concurrency regression tests — #1074."""

import multiprocessing
import os
import time
from pathlib import Path
from unittest.mock import patch

import yaml
import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.config.loader import (
    ConfigConflictError,
    config_file_hash,
    mutate_config,
    save_config,
    _acquire_config_file_lock,
    _release_config_file_lock,
)


# ─── Multiprocessing workers (module-level so they're picklable under 'spawn') ─


def _mp_set_permission_mode(config_path_str: str, value: str, ready: object, go: object) -> None:
    """Worker: set agent.permission_mode, synchronised via events for maximum contention."""
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
    cl.mutate_config(lambda cfg: setattr(cfg.agent, "permission_mode", value) or True)


def _mp_set_dashboard_port(config_path_str: str, value: int, ready: object, go: object) -> None:
    """Worker: set app.dashboard_port, synchronised via events for maximum contention."""
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
    cl.mutate_config(lambda cfg: setattr(cfg.app, "dashboard_port", value) or True)


# ─── Tests ────────────────────────────────────────────────────────────────────


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


def test_concurrent_cross_process_mutations_both_survive(tmp_path):
    """Two processes mutating independent fields: both changes must survive."""
    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    p1 = ctx.Process(
        target=_mp_set_permission_mode,
        args=(str(config_file), "Full", ready_a, go),
    )
    p2 = ctx.Process(
        target=_mp_set_dashboard_port,
        args=(str(config_file), 9999, ready_b, go),
    )
    p1.start()
    p2.start()
    ready_a.wait(timeout=10)
    ready_b.wait(timeout=10)
    go.set()
    p1.join(timeout=15)
    p2.join(timeout=15)

    assert p1.exitcode == 0, f"process A failed with code {p1.exitcode}"
    assert p2.exitcode == 0, f"process B failed with code {p2.exitcode}"

    final = AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text()) or {})
    assert final.agent.permission_mode == "Full", "process A's mutation was lost"
    assert final.app.dashboard_port == 9999, "process B's mutation was lost"


def test_cache_warm_targeted_mutation_preserves_other_writer(tmp_path):
    """Process A warms cache at revision N; process B writes N+1; A's targeted
    mutation must see B's field in the final result."""
    import aletheia.config.loader as cl

    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        # A warms its cache at revision N (persist_migrations=True applies any
        # schema normalization and ensures the cache is actually populated).
        from aletheia.config.loader import load_config
        cl._config_cache = None
        _ = load_config(persist_migrations=True)
        assert cl._config_cache is not None, "cache should be populated"

        # B directly writes a new field to disk (simulating another process)
        raw = yaml.safe_load(config_file.read_text()) or {}
        raw.setdefault("app", {})["dashboard_port"] = 7777
        config_file.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
        # Do NOT invalidate cl._config_cache so it still holds revision N

        # A mutates a different field via mutate_config (must bypass its warm cache)
        from aletheia.config.loader import mutate_config as _mc
        _mc(lambda cfg: setattr(cfg.agent, "permission_mode", "Full") or True)

        final = AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text()) or {})
        assert final.agent.permission_mode == "Full", "A's mutation did not apply"
        assert final.app.dashboard_port == 7777, "B's field was overwritten by A's stale snapshot"
    finally:
        for p in patches:
            p.stop()


def test_save_config_cas_rejects_stale_snapshot(tmp_path):
    """save_config with a wrong expected_hash raises ConfigConflictError."""
    import aletheia.config.loader as cl

    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        h = config_file_hash()  # correct hash

        # Another writer modifies the file
        raw = yaml.safe_load(config_file.read_text()) or {}
        raw.setdefault("app", {})["dashboard_port"] = 8888
        config_file.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

        # Our caller still has the old hash — save must be rejected
        cfg = AletheiaConfig()
        with pytest.raises(ConfigConflictError):
            save_config(cfg, expected_hash=h)

        # With the correct (updated) hash it succeeds
        h2 = config_file_hash()
        save_config(cfg, expected_hash=h2)  # must not raise
    finally:
        for p in patches:
            p.stop()


def test_lock_acquire_timeout_raises(tmp_path, monkeypatch):
    """If the file lock cannot be acquired in time, TimeoutError propagates."""
    import aletheia.config.loader as cl

    config_file = tmp_path / "config.yaml"
    _seed(config_file)

    patches = _patch_loader(config_file)
    for p in patches:
        p.start()
    try:
        # Acquire the lock ourselves so the next acquire times out
        held_fd = _acquire_config_file_lock()
        try:
            monkeypatch.setattr(cl, "_CONFIG_WRITE_LOCK_TIMEOUT_S", 0.05)
            with pytest.raises(TimeoutError, match="config_write_lock_timeout"):
                _acquire_config_file_lock()
        finally:
            _release_config_file_lock(held_fd)
    finally:
        for p in patches:
            p.stop()
