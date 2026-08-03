"""Metadata registry for user-configurable model-provider access methods."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderTypeDefinition:
    """Operator-facing capabilities for one concrete provider adapter type."""

    id: str
    label: str
    category: str
    fields: tuple[str, ...]
    singleton: bool
    protocols: tuple[str, ...] = ()

    def public(self) -> dict:
        result = {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "fields": list(self.fields),
            "singleton": self.singleton,
        }
        if self.protocols:
            result["protocols"] = list(self.protocols)
        return result


PROVIDER_TYPES: dict[str, ProviderTypeDefinition] = {
    "openai_compatible": ProviderTypeDefinition(
        id="openai_compatible",
        label="OpenAI Compatible API",
        category="api",
        fields=("base_url", "model", "protocol", "api_key"),
        singleton=False,
        protocols=("chat_completions", "responses"),
    ),
    "anthropic_compatible": ProviderTypeDefinition(
        id="anthropic_compatible",
        label="Anthropic Compatible API",
        category="api",
        fields=("base_url", "model", "auth_scheme", "api_key"),
        singleton=False,
        protocols=("anthropic_messages",),
    ),
    "databricks": ProviderTypeDefinition(
        id="databricks",
        label="Databricks",
        category="api",
        fields=("workspace_url", "model", "protocol", "api_key"),
        singleton=False,
        protocols=(
            "serving_endpoint",
            "chat_completions",
            "responses",
            "anthropic_messages",
        ),
    ),
    "codex_cli": ProviderTypeDefinition(
        id="codex_cli",
        label="Codex CLI",
        category="cli",
        fields=("codex_model",),
        singleton=True,
    ),
    "claude_cli": ProviderTypeDefinition(
        id="claude_cli",
        label="Claude Code CLI",
        category="cli",
        fields=("model",),
        singleton=True,
    ),
    "pi_cli": ProviderTypeDefinition(
        id="pi_cli",
        label="Pi Agent CLI",
        category="cli",
        fields=("model",),
        singleton=True,
    ),
    "anthropic_native": ProviderTypeDefinition(
        id="anthropic_native",
        label="Anthropic OAuth",
        category="oauth",
        fields=("model", "oauth"),
        singleton=True,
    ),
    "openai_native": ProviderTypeDefinition(
        id="openai_native",
        label="OpenAI OAuth",
        category="oauth",
        fields=("model", "oauth"),
        singleton=True,
    ),
}

API_PROVIDER_TYPES = frozenset(
    provider_type for provider_type, definition in PROVIDER_TYPES.items() if definition.category == "api"
)
CLI_PROVIDER_TYPES = frozenset(
    provider_type for provider_type, definition in PROVIDER_TYPES.items() if definition.category == "cli"
)
OAUTH_PROVIDER_TYPES = frozenset(
    provider_type for provider_type, definition in PROVIDER_TYPES.items() if definition.category == "oauth"
)


def provider_kind(provider_type: str) -> str:
    definition = PROVIDER_TYPES.get(provider_type)
    return definition.category if definition else "unknown"
