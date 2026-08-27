from __future__ import annotations

import json
from types import SimpleNamespace


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
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.get_data_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.current_commit", lambda: "head-1"
    )
    monkeypatch.setattr(
        "aletheia.trust.uta_qualification.create_uta_adapter",
        lambda _mode, _cfg: SimpleNamespace(identity=identity),
    )
    from aletheia.trust.uta_qualification import uta_profile_qualified

    assert uta_profile_qualified("binance_testnet", object()) == (True, "qualified")

    path.write_text(
        json.dumps(_artifact(identity, "head-1", credential_generation=2)),
        encoding="utf-8",
    )
    assert uta_profile_qualified("binance_testnet", object()) == (
        False, "qualification_identity_mismatch"
    )
