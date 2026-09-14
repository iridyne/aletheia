"""Configured HTTP Model Endpoint implementations."""


def register_model_providers() -> None:
    from aletheia.model_provider.router import PROVIDER_REGISTRY
    try:
        from .openai_compatible import OpenAICompatibleProvider
    except ImportError:
        pass
    else:
        PROVIDER_REGISTRY[OpenAICompatibleProvider.name] = OpenAICompatibleProvider

    try:
        from .anthropic_compatible import AnthropicCompatibleProvider
    except ImportError:
        pass
    else:
        PROVIDER_REGISTRY[AnthropicCompatibleProvider.name] = AnthropicCompatibleProvider
