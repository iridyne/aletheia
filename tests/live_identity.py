"""Bind real account metadata with synthetic credentials for trust fixtures."""


def bind_live_identity(monkeypatch, trust):
    from aletheia.exchanges import composition
    from aletheia.uta import manager

    monkeypatch.setattr(composition, "get_secret", lambda ref: trust.get_secret(ref))
    monkeypatch.setattr(composition, "get_credential_generation", lambda _scope: 0)
    monkeypatch.setattr(manager, "_registry_factory", composition.build_uta_registry)
