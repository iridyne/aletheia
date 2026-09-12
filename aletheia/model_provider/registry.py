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
}

API_PROVIDER_TYPES = frozenset(
    provider_type for provider_type, definition in PROVIDER_TYPES.items() if definition.category == "api"
)


def provider_kind(provider_type: str) -> str:
    definition = PROVIDER_TYPES.get(provider_type)
    return definition.category if definition else "unknown"


def default_protocol_for_provider_type(provider_type: str) -> str:
    definition = PROVIDER_TYPES.get(provider_type)
    return definition.protocols[0] if definition and definition.protocols else ""


def validate_provider_protocol(
    provider_type: str,
    protocol: str | None,
    *,
    explicit: bool,
) -> str:
    definition = PROVIDER_TYPES.get(provider_type)
    if definition is None or not definition.protocols:
        return str(protocol or "")

    value = str(protocol or "").strip()
    if not explicit:
        if value and value != "chat_completions" and value in definition.protocols:
            return value
        return default_protocol_for_provider_type(provider_type)
    if value in definition.protocols:
        return value
    allowed = ", ".join(definition.protocols)
    raise ValueError(
        f"Unsupported {provider_type} protocol: {value or '<empty>'}. Choose from: {allowed}"
    )


def provider_config_protocol(provider_type: str, pcfg) -> str:
    fields_set = getattr(pcfg, "model_fields_set", set())
    protocol = getattr(pcfg, "protocol", None)
    if provider_type == "anthropic_compatible" and protocol == "chat_completions":
        return default_protocol_for_provider_type(provider_type)
    return validate_provider_protocol(
        provider_type,
        protocol,
        explicit="protocol" in fields_set,
    )
