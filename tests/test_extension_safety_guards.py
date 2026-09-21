"""Moved production implementations remain covered by existing safety specs."""

import pytest

from tests.specs import spec_network_egress, spec_uta_boundary


@pytest.mark.parametrize("relative", [
    "extensions/exchanges/ccxt/adapter.py", "extensions/net.py",
])
def test_network_guard_rejects_extension_constructor(tmp_path, monkeypatch, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("import ccxt\nclient = ccxt.bitget()\n")
    monkeypatch.setattr(spec_network_egress, "_PKG", tmp_path / "aletheia")
    assert spec_network_egress._find_bare_ccxt_constructors() == [f"{relative}:2"]


@pytest.mark.parametrize("relative", [
    "extensions/exchanges/other.py", "aletheia/uta/other.py",
])
@pytest.mark.parametrize("source", [
    "from aletheia.uta.models import SubmitOrderCommand as Command\n",
    "command = models.ModifyOrderCommand()\n",
    "command = CancelOrderCommand()\n",
])
def test_mutation_guard_rejects_non_owner(tmp_path, relative, source):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(source)
    assert spec_uta_boundary._mutation_command_importers(tmp_path) == [relative]


def test_mutation_guard_keeps_exact_owners_and_ignores_prose(tmp_path):
    for relative in (
        "aletheia/uta/contract.py", "aletheia/uta/models.py",
        "aletheia/execution/coordinator.py", "aletheia/execution/gateway.py",
        "extensions/exchanges/ccxt/adapter.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from aletheia.uta.models import SubmitOrderCommand\n")
    (tmp_path / "extensions/documented.py").write_text('"""SubmitOrderCommand is not constructed here."""\n')
    assert spec_uta_boundary._mutation_command_importers(tmp_path) == []
