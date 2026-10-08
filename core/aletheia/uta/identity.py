"""Which account an identity names: the one relation every liveness gate shares.

``UTAAccountIdentity`` carries two kinds of fact. Some say *which account* it is
(``account_id``, ``execution_mode``, ``venue``, ``environment``, ``profile``,
``sub_accounts``) and one says *which credentials reached it*
(``credential_generation``). The rest (``adapter_implementation``,
``adapter_version``, ``ccxt_version``, ``account_fingerprint``) describe how the
account was reached; they are provenance, not identity.

Strict equality over the whole model made provenance part of the gate: upgrading
the CCXT library alone, with no credential change, turned every persisted
identity into a hard mismatch that no re-anchor path accepts.
"""

from __future__ import annotations

from enum import Enum

from aletheia.uta.models import UTAAccountIdentity


class IdentityRelation(str, Enum):
    SAME = "same"
    GENERATION_DRIFT = "generation_drift"
    DIFFERENT = "different"


def _account_facts(identity: UTAAccountIdentity) -> tuple:
    return (
        identity.account_id,
        identity.execution_mode,
        identity.venue,
        identity.environment,
        identity.profile,
        identity.sub_accounts,
    )


def identity_relation(persisted: UTAAccountIdentity, current: UTAAccountIdentity) -> IdentityRelation:
    """Relate a stored identity to the one the running adapter reports."""

    if _account_facts(persisted) != _account_facts(current):
        return IdentityRelation.DIFFERENT
    if persisted.credential_generation != current.credential_generation:
        return IdentityRelation.GENERATION_DRIFT
    return IdentityRelation.SAME
