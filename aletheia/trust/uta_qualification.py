"""Exact-code/account gate for activating a UTA execution profile."""

from __future__ import annotations

import json

from aletheia.config.loader import get_data_dir, load_config
from aletheia.update.git_ops import current_commit, is_worktree_dirty
from aletheia.uta.contract import UTA_CONTRACT_VERSION
from aletheia.uta.manager import create_uta_adapter


class UTAQualificationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__("UTA mutation requires current qualification")
        self.reason_code = reason_code


def uta_profile_qualified(execution_mode: str, cfg, *, adapter=None) -> tuple[bool, str]:
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
    if is_worktree_dirty():
        return False, "qualification_worktree_dirty"
    if artifact.get("uta_contract_version") != UTA_CONTRACT_VERSION:
        return False, "qualification_contract_mismatch"
    try:
        identity = (
            adapter if adapter is not None else create_uta_adapter(execution_mode, cfg)
        ).identity
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
    protection_incomplete = execution_mode in {"okx_demo", "bitget_demo"} and (
        probe.get("protection_submit") != "acknowledged" or probe.get("protection_cancel") != "acknowledged"
    )
    if (
        probe.get("modify") != "acknowledged"
        or probe.get("cancel") != "acknowledged"
        or probe.get("verified_exit") != "verified_fill_exit"
        or probe.get("reconciliation") != "exact_order_and_private_fills_complete"
        or not (probe.get("entry") or {}).get("fill_ids")
        or not (probe.get("close") or {}).get("fill_ids")
        or protection_incomplete
        or artifact.get("unresolved_items")
    ):
        return False, "qualification_execution_evidence_incomplete"
    return True, "qualified"


def require_uta_mutation_qualification(execution_mode: str, cfg=None, *, adapter=None) -> None:
    cfg = cfg or load_config()
    if cfg.exchange.uta_read_boundary_for(execution_mode) != "uta_v1":
        raise UTAQualificationError("uta_mutation_boundary_inactive")
    if adapter is None:
        qualified, reason = uta_profile_qualified(execution_mode, cfg)
    else:
        qualified, reason = uta_profile_qualified(execution_mode, cfg, adapter=adapter)
    if not qualified:
        raise UTAQualificationError(reason)
