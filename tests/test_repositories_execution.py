"""The execution seam preserves exports and shared writer ownership."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.fast


def test_execution_reexport_and_shared_helpers():
    for first in ("repositories", "repositories_execution"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"from aletheia.storage import {first}\n"
                "from aletheia.storage import repositories as repo, repositories_execution as execution\n"
                "from unittest.mock import patch\n"
                "for name in execution.__all__:\n"
                "    assert getattr(repo, name) is getattr(execution, name), name\n"
                "    assert getattr(execution, name).__module__ == execution.__name__, name\n"
                "for name in ('_stamp_provenance', '_insert', '_update', '_select_all', '_select_one',\n"
                "             '_update_pending', 'transaction', 'insert_trade_proposal', 'insert_order',\n"
                "             'insert_fill', 'insert_position', 'transition_order', 'transition_entry_plan',\n"
                "             'finalize_exchange_acknowledgement', '_seed_entry_fill_outcome',\n"
                "             'ensure_candidate_planning_generation'):\n"
                "    assert name not in execution.__all__, name\n"
                "    assert getattr(repo, name).__module__ == repo.__name__, name\n"
                "with patch.object(repo, '_select_one', return_value={'id': 'order'}) as select:\n"
                "    assert repo.get_order('order') == {'id': 'order'}\n"
                "    select.assert_called_once_with('orders', 'order')\n"
                "with patch.object(repo, '_select_all', return_value=[]) as select:\n"
                "    assert execution.list_trade_proposals(status='approved') == []\n"
                "    select.assert_called_once_with('trade_proposals', where='status = ?',\n"
                "                                   params=('approved',), limit=50)\n"
                "with patch.object(repo, 'get_order_for_entry_plan', return_value={'id': 'order'}):\n"
                "    assert not execution.entry_plan_activation_can_refresh('plan')\n"
                "with patch.object(repo, 'transaction') as transaction, patch.object(repo, 'get_db'):\n"
                "    assert execution.get_entry_plan_execution_lineage([]) == {}\n"
                "    transaction.return_value.__enter__.assert_called_once_with()\n"
                "    transaction.return_value.__exit__.assert_called_once_with(None, None, None)\n",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
