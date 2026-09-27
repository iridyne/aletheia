"""The repository facade keeps both import orders and shared helper ownership."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.fast


def test_positions_reexport_and_shared_helpers():
    for first in ("repositories", "repositories_positions"):
        subprocess.run(
            [
                sys.executable,
                "-c",
                f"from aletheia.storage import {first}\n"
                "from aletheia.storage import repositories as repo, repositories_positions as positions\n"
                "from unittest.mock import patch\n"
                "for name in positions.__all__:\n"
                "    assert getattr(repo, name) is getattr(positions, name), name\n"
                "with patch.object(repo, '_select_one', return_value={'id': 'position'}) as select:\n"
                "    assert repo.get_position('position') == {'id': 'position'}\n"
                "    select.assert_called_once_with('positions', 'position')\n",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
