"""Doctor consumes credential-presence facts without reading raw secrets."""

import pytest


@pytest.mark.parametrize("presence,severity", [
    ((True, True, True), "ok"),
    ((True, False, False), "warn"),
    ((False, False, False), "blocker"),
    (None, "blocker"),
    ((True,), "blocker"),
])
@pytest.mark.parametrize("configured", [True, False, None])
def test_doctor_reads_only_sanitized_status(monkeypatch, presence, severity, configured):
    from aletheia.config import loader, secrets
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.diagnostics import doctor
    from aletheia.model_provider import router
    from aletheia.uta import manager

    cfg = AletheiaConfig()
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.models.default_provider = "selected"
    cfg.models.providers["selected"] = ProviderConfig(type="openai_compatible", enabled=True, api_key_secret_ref="selected-reference")
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    def read_presence(mode, *, cfg):
        assert mode == "bitget_demo"
        return presence
    monkeypatch.setattr(manager, "read_credential_presence", read_presence)
    monkeypatch.setattr(router, "_provider_status_reader", lambda name, pcfg: {"configured": configured})
    def forbidden(*args, **kwargs):
        pytest.fail("Doctor read raw credentials")
    monkeypatch.setattr(secrets, "get_secret", forbidden)

    report = doctor.Report()
    doctor._probe_credentials(report)
    checks = {check.name: check for check in report.checks}
    assert checks["creds.bitget_demo"].severity == severity
    assert checks["creds.llm"].severity == ("ok" if configured is True else "warn")
    if presence is None or len(presence) != 3:
        assert "unavailable" in checks["creds.bitget_demo"].detail
        assert not checks["creds.bitget_demo"].fix_hint
    if configured is None:
        assert "unavailable" in checks["creds.llm"].detail
        assert "missing" not in checks["creds.llm"].detail
