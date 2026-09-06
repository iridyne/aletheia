"""Configuration/factory registry for loop-owned UTA adapter instances."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aletheia.uta.contract import UTAAdapter
from aletheia.uta.models import UTAAccountIdentity


@dataclass(frozen=True)
class UTAAdapterConfig:
    identity: UTAAccountIdentity
    boundary: str = "legacy"

    def __post_init__(self) -> None:
        if self.boundary not in {"legacy", "uta_v1", "shadow_read"}:
            raise ValueError(f"unsupported UTA boundary selector: {self.boundary}")

    @property
    def authoritative_read_owner(self) -> str:
        return "uta" if self.boundary == "uta_v1" else "legacy"

    @property
    def mutation_owner(self) -> str:
        return "uta" if self.boundary == "uta_v1" else "legacy"

    @property
    def shadow_read_enabled(self) -> bool:
        return self.boundary == "shadow_read"


UTAAdapterFactory = Callable[[UTAAdapterConfig], UTAAdapter]


class UTARegistry:
    """Stores immutable factories/configuration, never live clients or sessions."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[UTAAdapterConfig, UTAAdapterFactory]] = {}

    def register(self, config: UTAAdapterConfig, factory: UTAAdapterFactory) -> None:
        account_id = config.identity.account_id
        if account_id in self._entries:
            raise ValueError(f"UTA account already registered: {account_id}")
        self._entries[account_id] = (config, factory)

    def configured_identities(self) -> tuple[UTAAccountIdentity, ...]:
        return tuple(config.identity for config, _factory in self._entries.values())

    def config(self, account_id: str) -> UTAAdapterConfig:
        try:
            return self._entries[account_id][0]
        except KeyError as exc:
            raise KeyError(f"UTA account is not registered: {account_id}") from exc

    def create(self, account_id: str) -> UTAAdapter:
        try:
            config, factory = self._entries[account_id]
        except KeyError as exc:
            raise KeyError(f"UTA account is not registered: {account_id}") from exc
        adapter = factory(config)
        if adapter.identity != config.identity:
            raise ValueError("UTA factory returned an adapter for a different account identity")
        return adapter
