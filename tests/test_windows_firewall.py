from pathlib import Path


def _rule(
    *,
    name: str,
    action: str,
    program: Path,
    local_port: str = "Any",
    enabled: bool = True,
) -> dict:
    return {
        "name": name,
        "display_name": name,
        "action": action,
        "direction": "Inbound",
        "enabled": enabled,
        "profile": "Public",
        "protocol": "TCP",
        "local_port": [local_port],
        "remote_address": ["Any"],
        "program": str(program),
    }


def test_status_requires_local_subnet_allow_and_reports_python_block(monkeypatch, tmp_path):
    from aletheia.gateway import windows_firewall as firewall

    program = tmp_path / "pythonw.exe"
    monkeypatch.setattr(firewall.platform, "system", lambda: "Windows")
    monkeypatch.setattr(firewall, "listener_program", lambda: program)
    monkeypatch.setattr(
        firewall,
        "_query_rules",
        lambda: [
            _rule(name=firewall.RULE_NAME, action="Allow", program=program, local_port="8787"),
            _rule(name="PythonBlock", action="Block", program=program),
        ],
    )

    result = firewall.status(8787)

    assert result["configured"] is False
    assert result["rule_present"] is True
    assert result["conflicting_block_rules"] == ["PythonBlock"]


def test_status_accepts_owned_rule_and_ignores_block_excluding_dashboard_port(monkeypatch, tmp_path):
    from aletheia.gateway import windows_firewall as firewall

    program = tmp_path / "pythonw.exe"
    allow = _rule(name=firewall.RULE_NAME, action="Allow", program=program, local_port="8787")
    allow["remote_address"] = ["LocalSubnet"]
    allow["profile"] = "Private, Public"
    block = _rule(
        name="PythonBlock",
        action="Block",
        program=program,
        local_port="1-8786,8788-65535",
    )
    monkeypatch.setattr(firewall.platform, "system", lambda: "Windows")
    monkeypatch.setattr(firewall, "listener_program", lambda: program)
    monkeypatch.setattr(firewall, "_query_rules", lambda: [allow, block])

    result = firewall.status(8787)

    assert result["configured"] is True
    assert result["conflicting_block_rules"] == []


def test_enable_lan_narrows_generic_block_and_never_opens_backend(monkeypatch, tmp_path):
    from aletheia.gateway import windows_firewall as firewall

    program = tmp_path / "pythonw.exe"
    block = _rule(name="PythonBlock", action="Block", program=program)
    states = [
        {
            "supported": True,
            "configured": False,
            "rule_present": False,
            "conflicting_block_rules": ["PythonBlock"],
            "rules": [block],
            "port": 8787,
            "program": str(program),
        },
        {
            "supported": True,
            "configured": True,
            "rule_present": True,
            "conflicting_block_rules": [],
            "rules": [],
            "port": 8787,
            "program": str(program),
        },
    ]
    scripts: list[str] = []
    monkeypatch.setattr(firewall.platform, "system", lambda: "Windows")
    monkeypatch.setattr(firewall, "listener_program", lambda: program)
    monkeypatch.setattr(firewall, "status", lambda port: states.pop(0))
    monkeypatch.setattr(firewall, "_run_powershell", lambda script: scripts.append(script))
    monkeypatch.setattr(firewall, "_state_path", lambda: tmp_path / "firewall-state.json")

    result = firewall.apply_enable_lan(8787)

    assert result["ok"] is True
    assert "LocalSubnet" in scripts[0]
    assert "'0-8786','8788-65535'" in scripts[0]
    assert "LocalPort 8787" in scripts[0]
    assert "LocalPort 8788" not in scripts[0]


def test_enable_lan_refuses_non_generic_conflicting_block(monkeypatch, tmp_path):
    from aletheia.gateway import windows_firewall as firewall

    program = tmp_path / "pythonw.exe"
    block = _rule(name="ManagedBlock", action="Block", program=program, local_port="8000-9000")
    monkeypatch.setattr(firewall.platform, "system", lambda: "Windows")
    monkeypatch.setattr(firewall, "listener_program", lambda: program)
    monkeypatch.setattr(
        firewall,
        "status",
        lambda port: {
            "supported": True,
            "configured": False,
            "rule_present": False,
            "conflicting_block_rules": ["ManagedBlock"],
            "rules": [block],
            "port": port,
            "program": str(program),
        },
    )

    result = firewall.apply_enable_lan(8787)

    assert result["ok"] is False
    assert result["reason"] == "conflicting_block_rule_requires_manual_action"


def test_disable_lan_removes_owned_rule_and_restores_block_filter(monkeypatch, tmp_path):
    import json

    from aletheia.gateway import windows_firewall as firewall

    state_path = tmp_path / "firewall-state.json"
    state_path.write_text(
        json.dumps({"version": 1, "port": 8787, "narrowed_rules": [{"name": "PythonBlock", "local_port": ["Any"]}]}),
        encoding="utf-8",
    )
    scripts: list[str] = []
    monkeypatch.setattr(firewall.platform, "system", lambda: "Windows")
    monkeypatch.setattr(firewall, "_state_path", lambda: state_path)
    monkeypatch.setattr(firewall, "_run_powershell", lambda script: scripts.append(script))

    result = firewall.apply_disable_lan(8787)

    assert result["ok"] is True
    assert "Remove-NetFirewallRule" in scripts[0]
    assert "LocalPort @('Any')" in scripts[0]
    assert not state_path.exists()
