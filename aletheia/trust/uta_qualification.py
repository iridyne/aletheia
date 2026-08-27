"""Exact-code/account gate for activating a UTA execution profile."""

from __future__ import annotations

import json

from aletheia.config.loader import get_data_dir
from aletheia.update.git_ops import current_commit
from aletheia.uta.contract import UTA_CONTRACT_VERSION
from aletheia.uta.manager import create_uta_adapter


def uta_profile_qualified(execution_mode: str, cfg) -> tuple[bool, str]:
    path = get_data_dir() / "qualifications" / f"uta-{execution_mode}.json"
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False, "qualification_artifact_unavailable"
    if artifact.get("qualified") is not True:
        return False, "qualification_not_passed"
    if artifact.get("execution_mode") != execution_mode:
        return False, "qualification_mode_mismatch"
    code_sha = current_commit()
    if artifact.get("code_sha") != code_sha or not code_sha:
        return False, "qualification_code_mismatch"
    if artifact.get("uta_contract_version") != UTA_CONTRACT_VERSION:
        return False, "qualification_contract_mismatch"
    try:
        identity = create_uta_adapter(execution_mode, cfg).identity
    except Exception:
        return False, "qualification_account_unavailable"
    expected = {
        "profile": identity.profile,
        "adapter": identity.adapter_implementation,
        "adapter_version": identity.adapter_version,
        "driver_version": identity.ccxt_version,
        "account_fingerprint": identity.account_fingerprint,
        "credential_generation": identity.credential_generation,
        "environment": identity.environment,
    }
    if any(artifact.get(key) != value for key, value in expected.items()):
        return False, "qualification_identity_mismatch"
    probe = artifact.get("execution_probe") or {}
    if (
        probe.get("modify") != "acknowledged"
        or probe.get("cancel") != "acknowledged"
        or probe.get("verified_exit") != "verified_fill_exit"
        or probe.get("reconciliation") != "exact_order_and_private_fills_complete"
        or not (probe.get("entry") or {}).get("fill_ids")
        or not (probe.get("close") or {}).get("fill_ids")
        or artifact.get("unresolved_items")
    ):
        return False, "qualification_execution_evidence_incomplete"
    return True, "qualified"
