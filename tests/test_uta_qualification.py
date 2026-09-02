from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _artifact(identity, sha, **updates):
    value = {
        "qualified": True,
        "execution_mode": identity.execution_mode,
        "environment": identity.environment,
        "code_sha": sha,
        "uta_contract_version": 1,
        "adapter": identity.adapter_implementation,
        "adapter_version": identity.adapter_version,
        "profile": identity.profile,
        "driver_version": identity.ccxt_version,
        "account_fingerprint": identity.account_fingerprint,
        "credential_generation": identity.credential_generation,
        "unresolved_items": [],
        "execution_probe": {
            "entry": {"fill_ids": ["entry-fill"]},
            "modify": "acknowledged",
            "cancel": "acknowledged",
            "close": {"fill_ids": ["close-fill"]},
            "verified_exit": "verified_fill_exit",
            "reconciliation": "exact_order_and_private_fills_complete",
        },
    }
    value.update(updates)
    return value


def test_uta_qualification_is_bound_to_exact_sha_and_identity(tmp_path, monkeypatch):
    from aletheia.uta.models import UTAAccountIdentity

    identity = UTAAccountIdentity(
        account_id="binance_testnet",
        execution_mode="binance_testnet",
        venue="binance",
        environment="testnet",
        profile="binance_testnet",
        account_fingerprint="sha256:account",
        credential_generation=3,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version="4.5",
    )
    path = tmp_path / "qualifications" / "uta-binance_testnet.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_artifact(identity, "head-1")), encoding="utf-8")
    monkeypatch.setattr("aletheia.trust.uta_qualification.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("aletheia.trust.uta_qualification.current_commit", lambda: "head-1")
    monkeypatch.setattr("aletheia.trust.uta_qualification.is_worktree_dirty", lambda: False)
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.create_uta_adapter",
        lambda _mode, _cfg: SimpleNamespace(identity=identity),
    )
    from aletheia.trust.uta_qualification import uta_profile_qualified

    assert uta_profile_qualified("binance_testnet", object()) == (True, "qualified")
    rotated = SimpleNamespace(
        identity=identity.model_copy(update={"credential_generation": 4})
    )
    assert uta_profile_qualified(
        "binance_testnet", object(), adapter=rotated
    ) == (False, "qualification_identity_mismatch")

    monkeypatch.setattr("aletheia.trust.uta_qualification.is_worktree_dirty", lambda: True)
    assert uta_profile_qualified("binance_testnet", object()) == (
        False,
        "qualification_worktree_dirty",
    )
    path.write_text(json.dumps(_artifact(identity, "head-0")), encoding="utf-8")
    assert uta_profile_qualified("binance_testnet", object()) == (
        False,
        "qualification_code_mismatch",
    )
    monkeypatch.setattr("aletheia.trust.uta_qualification.is_worktree_dirty", lambda: False)

    mismatches = (
        ({"code_sha": "head-0"}, "qualification_code_mismatch"),
        ({"uta_contract_version": 2}, "qualification_contract_mismatch"),
        ({"profile": "other-profile"}, "qualification_identity_mismatch"),
        ({"adapter": "OtherAdapter"}, "qualification_identity_mismatch"),
        ({"adapter_version": "2"}, "qualification_identity_mismatch"),
        ({"driver_version": "4.6"}, "qualification_identity_mismatch"),
        ({"account_fingerprint": "sha256:other"}, "qualification_identity_mismatch"),
        ({"credential_generation": 2}, "qualification_identity_mismatch"),
        ({"environment": "demo"}, "qualification_identity_mismatch"),
    )
    for updates, reason in mismatches:
        path.write_text(
            json.dumps(_artifact(identity, "head-1", **updates)),
            encoding="utf-8",
        )
        assert uta_profile_qualified("binance_testnet", object()) == (False, reason)


def test_uta_mutation_qualification_fails_closed_with_bounded_reason(monkeypatch):
    from aletheia.trust.uta_qualification import (
        UTAQualificationError,
        require_uta_mutation_qualification,
    )

    cfg = SimpleNamespace(exchange=SimpleNamespace(uta_read_boundary_for=lambda _mode: "uta_v1"))
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.uta_profile_qualified",
        lambda _mode, _cfg: (True, "qualified"),
    )
    require_uta_mutation_qualification("binance_testnet", cfg)
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.uta_profile_qualified",
        lambda _mode, _cfg: (False, "qualification_code_mismatch"),
    )

    with pytest.raises(UTAQualificationError) as caught:
        require_uta_mutation_qualification("binance_testnet", cfg)

    assert caught.value.reason_code == "qualification_code_mismatch"
    assert str(caught.value) == "UTA mutation requires current qualification"


@pytest.mark.parametrize(
    "execution_mode,venue,profile_name",
    [
        ("okx_demo", "okx", "okx_demo"),
        ("bitget_demo", "bitget", "classic_v2"),
    ],
)
def test_demo_qualification_requires_protection_evidence(
    tmp_path, monkeypatch, execution_mode, venue, profile_name
):
    from aletheia.trust.uta_qualification import uta_profile_qualified
    from aletheia.uta.models import UTAAccountIdentity

    identity = UTAAccountIdentity(
        account_id=execution_mode,
        execution_mode=execution_mode,
        venue=venue,
        environment="demo",
        profile=profile_name,
        account_fingerprint=f"sha256:{venue}",
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version="4.5",
    )
    path = tmp_path / "qualifications" / f"uta-{execution_mode}.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_artifact(identity, "head-1")), encoding="utf-8")
    monkeypatch.setattr("aletheia.trust.uta_qualification.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("aletheia.trust.uta_qualification.current_commit", lambda: "head-1")
    monkeypatch.setattr("aletheia.trust.uta_qualification.is_worktree_dirty", lambda: False)
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.create_uta_adapter", lambda *_: SimpleNamespace(identity=identity)
    )
    assert uta_profile_qualified(execution_mode, object())[1] == "qualification_execution_evidence_incomplete"


@pytest.mark.asyncio
async def test_uta_qualification_refuses_dirty_source_before_connect(monkeypatch):
    from scripts import qualify_uta

    monkeypatch.setattr(qualify_uta, "is_worktree_dirty", lambda: True)
    monkeypatch.setattr(
        qualify_uta,
        "load_config",
        lambda: pytest.fail("dirty qualification loaded runtime configuration"),
    )

    with pytest.raises(RuntimeError, match="qualification_worktree_dirty"):
        await qualify_uta._qualify(SimpleNamespace(execution_mode="binance_testnet", execution_probe=False))


@pytest.mark.asyncio
async def test_okx_execution_probe_requires_demo_specific_confirmation(monkeypatch):
    from scripts import qualify_uta

    monkeypatch.setattr(qualify_uta, "is_worktree_dirty", lambda: False)
    with pytest.raises(ValueError, match="explicit order confirmation"):
        await qualify_uta._qualify(SimpleNamespace(
            execution_mode="okx_demo",
            execution_probe=True,
            confirm_testnet_order=True,
            confirm_demo_order=False,
        ))


@pytest.mark.asyncio
async def test_bitget_qualification_is_supported_and_requires_demo_confirmation(monkeypatch):
    from scripts import qualify_uta

    monkeypatch.setattr(qualify_uta, "is_worktree_dirty", lambda: False)
    with pytest.raises(ValueError, match="explicit order confirmation"):
        await qualify_uta._qualify(SimpleNamespace(
            execution_mode="bitget_demo",
            execution_probe=True,
            confirm_testnet_order=False,
            confirm_demo_order=False,
        ))
