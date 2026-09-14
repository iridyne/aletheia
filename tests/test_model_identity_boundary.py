"""Model identity bytes remain stable while Core receives only opaque evidence."""

import pytest


@pytest.mark.parametrize("kind,protocol,stored,candidate", [
    ("openai_compatible", "chat_completions",
     "d92b9f54aee0595240e495b8bf806fa33da7616e4c2997709118e1fff5020b8a",
     "883c16d3862150044f7bd67aee84998e4eb5315616e17c635b57b9da5749621d"),
    ("openai_compatible", "responses",
     "6ff1abae867ea1ee7418364f6a9bd10b0e4d6c43b04c67c59c1e5ae60ac3abb6",
     "33ea81e3b3f35580ac85431bbf4986901d60c84c6df75279ea83bc24bf7ffab6"),
    ("anthropic_compatible", "anthropic_messages",
     "eba680e9df85e19ccc40d68c96772a714dbbf96fed3a2f123386a377030585da",
     "25641fc5724eea9892ccb79704cd7a0350a5837ec72c7be6b3b037183e98d297"),
])
def test_identity_matches_pre_migration_goldens(monkeypatch, kind, protocol, stored, candidate):
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider import http_runtime

    refs = []

    def secret(ref):
        refs.append(ref)
        return "synthetic:" + ref

    monkeypatch.setattr(http_runtime, "get_secret", secret)
    cfg = ProviderConfig(
        type=kind, protocol=protocol, base_url="https://endpoint.test/v1",
        model="chosen-model", api_key_secret_ref="model.profile.selected.api_key",
        auth_scheme="bearer",
    )
    assert http_runtime.http_configuration_identity("selected", cfg) == stored
    assert http_runtime.http_configuration_identity(
        "selected", cfg, credential_overrides={cfg.api_key_secret_ref: "synthetic-candidate"},
    ) == candidate
    assert refs == [cfg.api_key_secret_ref]


def test_core_identity_port_never_accepts_raw_overrides_and_fails_closed_when_unbound(monkeypatch):
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import router

    cfg = AletheiaConfig()
    profile = ProviderConfig(type="openai_compatible", base_url="https://endpoint.test")
    cfg.models.providers["selected"] = profile
    profile.verification_identity = "opaque-identity"
    profile.verification_verified = True
    calls = []

    def identity(name, pcfg):
        calls.append((name, pcfg))
        return "opaque-identity"

    monkeypatch.setattr(router, "_provider_identity_reader", identity)
    monkeypatch.setattr(router, "mutate_config", lambda apply: apply(cfg))
    assert router.provider_verification_snapshot("selected", profile)["verified"] is True
    with pytest.raises(TypeError):
        router.provider_configuration_identity("selected", profile, credential_overrides={})
    assert calls == [("selected", profile)]
    monkeypatch.setattr(router, "_provider_identity_reader", None)
    with pytest.raises(ValueError, match="model_adapter_unavailable"):
        router.provider_configuration_identity("selected", profile)
    snapshot = router.provider_verification_snapshot("selected", profile)
    assert snapshot["verified"] is None
    assert snapshot["verification_identity"] is None
    assert snapshot["verification_detail"] == "model_adapter_unavailable"
    assert snapshot["verification_invalidated"] is False  # Unavailable does not prove a mismatch.
    assert router.persist_provider_verification(
        "selected", identity="opaque-identity", verified=True,
        source="explicit_probe", reason_code="verified",
    ) is False
    assert profile.verification_identity == "opaque-identity"  # No history rewrite.
