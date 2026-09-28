"""Test-only escape hatch for seeding states production owners must reject."""

from aletheia.storage import repositories as repo


def force_update_position(position_id: str, **fields) -> None:
    repo._update("positions", position_id, fields)
