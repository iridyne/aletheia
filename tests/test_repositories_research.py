"""The research seam preserves import order and the facade's shared helpers."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.fast


def test_research_reexport_and_shared_helpers():
    for first in ("repositories", "repositories_research"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"from aletheia.storage import {first}\n"
                "from aletheia.storage import repositories as repo, repositories_research as research\n"
                "from unittest.mock import patch\n"
                "for name in research.__all__:\n"
                "    assert getattr(repo, name) is getattr(research, name), name\n"
                "with patch.object(repo, '_select_one', return_value={'id': 'run'}) as select:\n"
                "    assert research.get_agent_run('run') == {'id': 'run'}\n"
                "    select.assert_called_once_with('agent_runs', 'run')\n",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
