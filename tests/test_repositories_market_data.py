"""The market-data seam preserves exports, exclusions and shared ownership."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.fast


def test_market_data_reexport_and_shared_helpers():
    for first in ("repositories", "repositories_market_data"):
        subprocess.run(
            [
                sys.executable,
                "-c",
                f"from aletheia.storage import {first}\n"
                "from aletheia.storage import repositories as repo, repositories_market_data as market\n"
                "from unittest.mock import patch\n"
                "for name in market.__all__:\n"
                "    assert getattr(repo, name) is getattr(market, name), name\n"
                "    assert getattr(market, name).__module__ == market.__name__, name\n"
                "for name in ('_seed_entry_fill_outcome', 'latest_options_structure',\n"
                "             'latest_perp_structure', 'plan_market_ohlcv_tiering', 'batch_news', 'latest_orderbook'):\n"
                "    assert name not in market.__all__, name\n"
                "    assert getattr(repo, name).__module__ == repo.__name__, name\n"
                "with patch.object(repo, '_select_one', return_value={'id': 'event'}) as select:\n"
                "    assert market.get_prediction_event('event') == {'id': 'event'}\n"
                "    select.assert_called_once_with('prediction_events', 'event')\n"
                "with patch.object(repo, 'transaction') as transaction, \\\n"
                "     patch.object(repo, 'insert_orderbook_snapshot') as insert:\n"
                "    assert market.insert_collector_snapshot_batch(orderbooks=[{'id': 'book'}]) == {\n"
                "        'orderbooks': 1, 'funding': 0, 'open_interest': 0}\n"
                "    transaction.return_value.__enter__.assert_called_once_with()\n"
                "    transaction.return_value.__exit__.assert_called_once_with(None, None, None)\n"
                "    insert.assert_called_once_with({'id': 'book'})\n",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
