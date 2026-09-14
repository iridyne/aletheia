"""Regression tests for Settings honesty — dead config fields must not
pretend to be active controls.

Issue #399: the Strategy Factory subsystem was removed with Position C
(2026-07-02) but its config class, its `POST /api/settings/strategy_factory`
route and its GET projection survived as a compatibility shell. The shell is now
DELETED, so the tests that asserted the shell's honest-but-inert behavior are
gone with it; two of them actively guarded the dead code's survival
(`assert sf_fields, "must still exist for config-file backward compat"`).

`AletheiaConfig` is `extra="forbid"`, so a config.yaml still carrying a
`strategy_factory:` section would fail to LOAD once the field is gone. Removing a
config section therefore requires a drop migration, which `config/loader.py` now
performs alongside the other removed-key migrations.

What remains here is the Event Triggers half of the same honesty rule: a removed
field must be REJECTED, not silently accepted.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Event Triggers POST — dead fields must be rejected (extra="forbid")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tickers, expected", [([], []), ([" nvda ", "NVDA"], ["NVDA"])])
def test_equity_save_retires_legacy_list_and_empty_disables_observer(monkeypatch, tickers, expected):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.server.routes import settings
    from aletheia.agent_runtime import scheduler

    cfg = AletheiaConfig()
    cfg.market_data.observed_equities = ["AAPL"]
    cfg.runtime.paused = False
    tokens = list(cfg.market_data.observed_stock_tokens)
    saved, audits = [], []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    def mutate(change):
        if change(cfg):
            saved.append(cfg)
        return True
    monkeypatch.setattr(settings, "mutate_config", mutate)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", audits.append)
    result = settings.update_observed_equities(settings.EquitiesConfigUpdate(tickers=tickers))
    assert result == {"observed_equities": expected}
    assert cfg.market_data.observed_equities == expected
    assert not hasattr(cfg.market_data, "observed_stocks")
    assert cfg.market_data.observed_stock_tokens == tokens
    assert saved == [cfg]
    assert audits[0]["before"] == {"observed_equities": ["AAPL"]}
    assert audits[0]["after"] == {"observed_equities": expected}
    if not expected:
        monkeypatch.setattr(scheduler, "load_config", lambda: cfg)
        def forbidden(*args, **kwargs):
            raise AssertionError("cleared equities must not run the old stock list")
        monkeypatch.setattr(scheduler, "_run_async_with_timeout", forbidden)
        scheduler._run_equity_observer()


@pytest.mark.parametrize("canonical", [None, [], ["AAPL"]])
def test_legacy_equities_decode_has_no_runtime_alias(canonical):
    from aletheia.config.schema import MarketDataConfig

    raw = {"observed_stocks": ["TSLA"]}
    if canonical is not None:
        raw["observed_equities"] = canonical
    cfg = MarketDataConfig.model_validate(raw, extra="forbid")
    assert cfg.observed_equities == (canonical or ["TSLA"])
    assert raw["observed_stocks"] == ["TSLA"]
    assert not hasattr(cfg, "observed_stocks")
    assert "observed_stocks" not in cfg.model_dump()
    cfg.observed_equities = []
    assert MarketDataConfig.model_validate(cfg.model_dump()).observed_equities == []


@pytest.mark.parametrize("legacy", [None, "TSLA", [1]])
def test_legacy_equities_decode_rejects_malformed_ignored_list(legacy):
    from pydantic import ValidationError
    from aletheia.config.schema import MarketDataConfig

    with pytest.raises(ValidationError):
        MarketDataConfig.model_validate({"observed_equities": ["AAPL"], "observed_stocks": legacy})


@pytest.mark.parametrize("field", ["observed_stocks", "observed_xstocks", "observed_equities"])
def test_market_data_rejects_retired_stock_list_before_config_access(monkeypatch, field):
    from fastapi import FastAPI
    from aletheia.server.routes import settings

    def forbidden():
        raise AssertionError("retired request field must fail before config access")

    monkeypatch.setattr(settings, "load_config", forbidden)
    app = FastAPI()
    app.include_router(settings.router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/settings/market-data", json={field: ["TSLA"]})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["loc"] == ["body", field] and error["type"] == "extra_forbidden"
               for error in errors)


@pytest.mark.parametrize("tokens", [None, [], ["rNVDA/USDT"]])
def test_legacy_tokens_decode_preserves_defaults_merge_and_clear(tokens):
    from aletheia.config.schema import MarketDataConfig

    raw = {"observed_xstocks": ["TSLAON/USDT", "TSLAON/USDT"]}
    if tokens is not None:
        raw["observed_stock_tokens"] = tokens
    base = MarketDataConfig().observed_stock_tokens if tokens is None else tokens
    cfg = MarketDataConfig.model_validate(raw, extra="forbid")
    assert cfg.observed_stock_tokens == list(dict.fromkeys(base + raw["observed_xstocks"]))
    assert raw["observed_xstocks"] == ["TSLAON/USDT", "TSLAON/USDT"]
    assert not hasattr(cfg, "observed_xstocks")
    assert "observed_xstocks" not in cfg.model_dump()
    cfg.observed_stock_tokens = []
    assert MarketDataConfig.model_validate(cfg.model_dump()).observed_stock_tokens == []


@pytest.mark.parametrize("legacy", [None, "TSLAON/USDT", [1]])
def test_legacy_tokens_decode_rejects_malformed_list(legacy):
    from pydantic import ValidationError
    from aletheia.config.schema import MarketDataConfig

    with pytest.raises(ValidationError):
        MarketDataConfig.model_validate({"observed_xstocks": legacy})


@pytest.mark.parametrize("tokens", [None, [], ["rNVDA/USDT"]])
def test_canonical_token_save_replaces_decoded_legacy_only_when_explicit(monkeypatch, tokens):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.server.routes import settings
    from aletheia.trading.instruments import stock_tokens_from_config

    cfg = AletheiaConfig()
    cfg.market_data = type(cfg.market_data).model_validate({
        "observed_stock_tokens": ["rAAPL/USDT"], "observed_xstocks": ["TSLAON/USDT"]})
    audits, mutations = [], []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    def mutate(change):
        mutations.append(change(cfg))
    monkeypatch.setattr(settings, "_mutate_config_with_candidate_publication_permit", mutate)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", audits.append)
    body = {"observed_stock_tokens": tokens} if tokens is not None else {"ohlcv_cache_ttl_min": 12}
    settings.update_market_data_config(settings.MarketDataConfigUpdate(**body))
    assert mutations == [True]
    assert audits[0]["before"]["observed_stock_tokens"] == ["rAAPL/USDT", "TSLAON/USDT"]
    assert not hasattr(cfg.market_data, "observed_xstocks")
    if tokens is None:
        assert cfg.market_data.observed_stock_tokens == ["rAAPL/USDT", "TSLAON/USDT"]
    else:
        assert cfg.market_data.observed_stock_tokens == tokens
        assert [item.symbol for item in stock_tokens_from_config(cfg)] == (["RNVDA/USDT"] if tokens else [])
        assert "observed_xstocks" not in audits[0]["after"]


@pytest.fixture()
def event_trigger_app():
    from aletheia.server.routes import settings as settings_mod
    from aletheia.config.schema import EventTriggerConfig, AletheiaConfig
    from fastapi import FastAPI

    cfg = MagicMock(spec=AletheiaConfig)
    cfg.event_triggers = EventTriggerConfig()

    app = FastAPI()
    app.include_router(settings_mod.router)

    with (
        patch.object(settings_mod, "load_config", return_value=cfg),
        patch.object(settings_mod, "save_config"),
        patch("aletheia.files.jsonl.write_audit"),
    ):
        yield TestClient(app, raise_server_exceptions=False), cfg


def test_event_trigger_rejects_trigger_strategy_design(event_trigger_app):
    client, _ = event_trigger_app
    resp = client.post(
        "/api/settings/event_triggers",
        json={"trigger_strategy_design": True},
    )
    # extra="forbid" on SettingsRequest — unknown field must fail validation
    assert resp.status_code == 422


def test_event_trigger_rejects_strategy_design_min_move_pct(event_trigger_app):
    client, _ = event_trigger_app
    resp = client.post(
        "/api/settings/event_triggers",
        json={"strategy_design_min_move_pct": 7.5},
    )
    assert resp.status_code == 422


def test_event_trigger_live_fields_still_accepted(event_trigger_app):
    client, _ = event_trigger_app
    resp = client.post(
        "/api/settings/event_triggers",
        json={"enabled": True, "price_move_pct_threshold": 4.0},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"].get("price_move_pct_threshold") == 4.0


# ---------------------------------------------------------------------------
# The removed shell must stay removed
# ---------------------------------------------------------------------------

def test_strategy_factory_shell_is_gone():
    """No config class, no route, no GET projection.

    A compatibility shell that returns `{"deprecated": true, "updated": {}}` is a
    second, inert API surface. Keeping it taught readers the subsystem still
    existed and kept 13 dead fields alive in the schema.
    """
    import ast
    import pathlib

    from aletheia.config.schema import AletheiaConfig

    assert not hasattr(AletheiaConfig(), "strategy_factory"), (
        "AletheiaConfig grew a strategy_factory section again"
    )

    root = pathlib.Path(__file__).parent.parent
    for relative in ("core/aletheia/config/schema.py", "core/aletheia/server/routes/settings.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "StrategyFactory" not in source, (
            f"{relative} references the removed Strategy Factory shell"
        )
        assert "strategy_factory" not in source, (
            f"{relative} references the removed strategy_factory config section"
        )

    # And the route itself must not come back under any decorator form.
    settings_src = (root / "core/aletheia/server/routes/settings.py").read_text(encoding="utf-8")
    tree = ast.parse(settings_src)
    routes = {
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    assert "/strategy_factory" not in routes


def test_removed_strategy_factory_section_still_loads(tmp_path, monkeypatch):
    """An existing config.yaml with the removed section must LOAD, not explode.

    `AletheiaConfig` is `extra="forbid"`, so deleting the field without a drop
    migration turns every pre-existing config file into a hard startup failure.
    This is the regression that caught it: 28 test modules failed collection with
    `strategy_factory: Extra inputs are not permitted` because they read a real
    config file carrying the old section.
    """
    import yaml

    import aletheia.config.loader as loader

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "strategy_factory": {"backtest_gate_enabled": True, "sweep_top_k": 10},
            "trading": {"default_stop_loss_pct": 2.0},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._invalidate_config_cache()

    cfg = loader.load_config(use_cache=False, persist_migrations=False)

    assert cfg.trading.default_stop_loss_pct == 2.0
    assert not hasattr(cfg, "strategy_factory")


def test_unimplemented_system_tools_are_not_registered():
    """A registered tool with no handler advertises a capability that cannot run.

    `create_playbook_draft` / `create_prompt_draft` /
    `create_code_change_proposal` had zero handler implementations, yet were
    registered under the `system` class, which `TOOL_CLASSES` permitted for
    `Full`. `is_tool_permitted` already fails closed for unknown names
    (`TOOL_REGISTRY.get(name, "restricted_tool")` → empty allow-set), so removing
    them is strictly more restrictive, not less.
    """
    from aletheia.agent_runtime.permissions import (
        TOOL_CLASSES,
        TOOL_REGISTRY,
        is_tool_permitted,
    )
    from aletheia.agent_runtime.tool_registry import init_tool_registry

    init_tool_registry()
    for phantom in (
        "create_playbook_draft",
        "create_prompt_draft",
        "create_code_change_proposal",
    ):
        assert phantom not in TOOL_REGISTRY
        for mode in ("Observe", "Paper", "Full"):
            assert is_tool_permitted(phantom, mode) is False

    assert "system" not in TOOL_CLASSES, (
        "the system tool class has no members; an empty permission class invites "
        "a future tool to be added without deciding its real class"
    )
