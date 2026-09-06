from types import SimpleNamespace

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.trust.uta_qualification import UTAQualificationError
from aletheia.uta.manager import (
    create_selected_uta_mutation_adapter,
    select_account_port,
)


@pytest.mark.parametrize(
    "execution_mode",
    (
        "binance_testnet",
        "okx_demo",
        "bitget_demo",
        "binance_live_limited",
        "okx_live_limited",
        "bitget_live_limited",
    ),
)
@pytest.mark.parametrize(
    "boundary,read_owner,mutation_owner,shadow",
    (
        ("legacy", "legacy", "legacy", False),
        ("shadow_read", "legacy", "legacy", True),
        ("uta_v1", "uta", "uta", False),
    ),
)
def test_account_owner_matrix_is_selected_once(
    monkeypatch,
    execution_mode,
    boundary,
    read_owner,
    mutation_owner,
    shadow,
):
    monkeypatch.setattr("aletheia.uta.manager.get_credential_generation", lambda _scope: 3)
    cfg = AletheiaConfig()
    cfg.exchange.uta_read_boundaries = {execution_mode: boundary}
    cfg.exchange.uta_account_config_generations = {execution_mode: 5}

    selected = select_account_port(execution_mode, cfg)

    assert selected.identity.execution_mode == execution_mode
    assert selected.authoritative_read_owner == read_owner
    assert selected.mutation_owner == mutation_owner
    assert selected.shadow_read_enabled is shadow
    assert selected.identity.credential_generation > 5


@pytest.mark.parametrize("boundary", ("legacy", "shadow_read"))
def test_unselected_and_shadow_uta_cannot_mutate(boundary):
    cfg = AletheiaConfig()
    cfg.exchange.uta_read_boundaries = {"okx_demo": boundary}

    with pytest.raises(UTAQualificationError) as caught:
        create_selected_uta_mutation_adapter("okx_demo", cfg)

    assert caught.value.reason_code == "uta_mutation_boundary_inactive"


def test_stale_account_generation_fails_before_adapter_creation(monkeypatch):
    cfg = AletheiaConfig()
    cfg.exchange.uta_read_boundaries = {"okx_demo": "uta_v1"}
    selected = select_account_port("okx_demo", cfg)
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda _cfg: pytest.fail("stale generation must fail before adapter creation"),
    )

    with pytest.raises(UTAQualificationError) as caught:
        create_selected_uta_mutation_adapter(
            "okx_demo",
            cfg,
            expected_generation=selected.identity.credential_generation + 1,
        )

    assert caught.value.reason_code == "uta_account_generation_changed"


def test_gateway_consumes_selected_mutation_owner(monkeypatch):
    from aletheia.execution import gateway

    selected = SimpleNamespace(mutation_owner="legacy")
    monkeypatch.setattr("aletheia.uta.manager.select_account_port", lambda *_args: selected)
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.require_uta_mutation_qualification",
        lambda *_args, **_kwargs: pytest.fail("legacy owner cannot qualify UTA mutation"),
    )

    assert gateway._qualified_uta_mutation_selected(AletheiaConfig(), "okx_demo") is False
