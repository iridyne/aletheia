"""Bind real account metadata with synthetic credentials for trust fixtures."""


def bind_live_identity(monkeypatch, credential_reader):
    from extensions.exchanges import composition
    from aletheia.uta import manager

    monkeypatch.setattr(composition, "get_secret", credential_reader)
    monkeypatch.setattr(composition, "get_credential_generation", lambda _scope: 0)
    monkeypatch.setattr(manager, "_registry_factory", composition.build_uta_registry)
    monkeypatch.setattr(manager, "_credential_revision_reader", composition.read_credential_revision)
