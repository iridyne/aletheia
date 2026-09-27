"""The universe seam preserves import order and the facade's shared helpers."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.fast


def test_universe_reexport_and_shared_helpers():
    for first in ("repositories", "repositories_universe"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"from aletheia.storage import {first}\n"
                "from aletheia.storage import repositories as repo, repositories_universe as universe\n"
                "from unittest.mock import patch\n"
                "for name in universe.__all__:\n"
                "    assert getattr(repo, name) is getattr(universe, name), name\n"
                "with patch.object(repo, '_select_one', return_value={'id': 'asset'}) as select:\n"
                "    assert universe.get_universe_asset('asset') == {'id': 'asset'}\n"
                "    select.assert_called_once_with('universe_registry', 'asset')\n",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
