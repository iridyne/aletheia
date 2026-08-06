from types import SimpleNamespace


def test_gateway_restart_reports_stop_and_start_durations(monkeypatch):
    from typer.testing import CliRunner
    from aletheia.cli import gateway as gateway_cli
    from aletheia.gateway import lifecycle

    monkeypatch.setattr(gateway_cli, "_maybe_configure_windows_lan_firewall", lambda **_kwargs: None)
    monkeypatch.setattr(lifecycle, "do_stop", lambda: None)
    monkeypatch.setattr(lifecycle, "do_start", lambda **_kwargs: None)
    ticks = iter([10.0, 12.5, 20.0])
    monkeypatch.setattr(gateway_cli.time, "monotonic", lambda: next(ticks))

    result = CliRunner().invoke(gateway_cli.gateway_app, ["restart"])

    assert result.exit_code == 0
    assert "Gateway restart completed in 10.0s (stop 2.5s; start/readiness 7.5s)." in result.output


def test_gateway_restart_reports_pending_readiness_after_timeout(monkeypatch):
    from typer.testing import CliRunner
    from aletheia.cli import gateway as gateway_cli
    from aletheia.gateway import lifecycle

    monkeypatch.setattr(gateway_cli, "_maybe_configure_windows_lan_firewall", lambda **_kwargs: None)
    monkeypatch.setattr(lifecycle, "do_stop", lambda: None)
    monkeypatch.setattr(lifecycle, "do_start", lambda **_kwargs: False)
    ticks = iter([10.0, 12.5, 27.5])
    monkeypatch.setattr(gateway_cli.time, "monotonic", lambda: next(ticks))

    result = CliRunner().invoke(gateway_cli.gateway_app, ["restart"])

    assert result.exit_code == 0
    assert "Gateway restart command returned in 17.5s" in result.output
    assert "Readiness is still pending." in result.output


def test_interactive_gateway_start_onboarding_requests_uac(monkeypatch):
    from aletheia.cli import gateway as gateway_cli
    from aletheia.config import loader
    from aletheia.gateway import windows_firewall, windows_machine_service

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(gateway_cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gateway_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(app=SimpleNamespace(bind_host="0.0.0.0", dashboard_port=8787)),
    )
    monkeypatch.setattr(windows_firewall, "status", lambda port: {"configured": False, "port": port})
    monkeypatch.setattr(
        windows_machine_service,
        "firewall_enable_lan",
        lambda port, elevate=True: calls.append((port, elevate)) or {"ok": True, "configured": True},
    )
    monkeypatch.setattr(gateway_cli, "_audit_firewall_change", lambda *args, **kwargs: None)

    gateway_cli._maybe_configure_windows_lan_firewall(enabled=True)

    assert calls == [(8787, True)]


def test_noninteractive_gateway_start_never_attempts_hidden_uac(monkeypatch):
    from aletheia.cli import gateway as gateway_cli
    from aletheia.config import loader
    from aletheia.gateway import windows_firewall, windows_machine_service

    monkeypatch.setattr(gateway_cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gateway_cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(app=SimpleNamespace(bind_host="0.0.0.0", dashboard_port=8787)),
    )
    monkeypatch.setattr(windows_firewall, "status", lambda port: {"configured": False, "port": port})
    monkeypatch.setattr(
        windows_machine_service,
        "firewall_enable_lan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not elevate")),
    )

    gateway_cli._maybe_configure_windows_lan_firewall(enabled=True)


def test_loopback_binding_never_requests_firewall_change(monkeypatch):
    from aletheia.cli import gateway as gateway_cli
    from aletheia.config import loader
    from aletheia.gateway import windows_machine_service

    monkeypatch.setattr(gateway_cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gateway_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(app=SimpleNamespace(bind_host="127.0.0.1", dashboard_port=8787)),
    )
    monkeypatch.setattr(
        windows_machine_service,
        "firewall_enable_lan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not elevate")),
    )

    gateway_cli._maybe_configure_windows_lan_firewall(enabled=True)


def test_doctor_warns_when_wildcard_binding_lacks_windows_firewall_rule(monkeypatch):
    from aletheia.config import loader
    from aletheia.diagnostics import doctor
    from aletheia.gateway import windows_firewall

    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(app=SimpleNamespace(bind_host="0.0.0.0", dashboard_port=8787)),
    )
    monkeypatch.setattr(
        windows_firewall,
        "status",
        lambda port: {
            "supported": True,
            "configured": False,
            "rule_present": False,
            "conflicting_block_rules": ["PythonBlock"],
            "port": port,
        },
    )
    report = doctor.Report(generated_at="now")

    doctor._probe_windows_lan_firewall(report)

    finding = next(item for item in report.checks if item.name == "network.windows_firewall")
    assert finding.severity == "warn"
    assert "gateway firewall enable-lan" in finding.fix_hint
