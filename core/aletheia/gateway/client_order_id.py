"""Deterministic client_order_id generation for idempotent order submission."""

from hashlib import sha256


def make_client_order_id(
    env: str,
    proposal_id: str,
    leg_id: str,
    attempt: int,
    max_len: int = 64,
) -> str:
    """Generate a deterministic client_order_id.

    Format: aletheia_{env}_{proposal_id}_{leg_id}_{attempt:02d}
    If too long for exchange, truncates with a hash suffix.
    """
    raw = f"aletheia_{env}_{proposal_id}_{leg_id}_{attempt:02d}"
    if len(raw) <= max_len:
        return raw
    suffix = sha256(raw.encode()).hexdigest()[:8]
    prefix = raw[: max_len - 9]
    return f"{prefix}_{suffix}"


def make_idempotency_key(
    env: str,
    proposal_id: str,
    leg_id: str,
    attempt: int,
) -> str:
    """Generate a deterministic idempotency key for an order intent."""
    raw = f"idem_{env}_{proposal_id}_{leg_id}_{attempt:02d}"
    return sha256(raw.encode()).hexdigest()[:32]
