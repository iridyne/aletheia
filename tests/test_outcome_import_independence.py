"""Optional report modules must not gate Core accounting imports."""

from pathlib import Path
import subprocess
import sys


def test_core_outcomes_do_not_import_optional_reports(tmp_path):
    script = """
import os
import socket
import sys
from pathlib import Path

os.environ["ALETHEIA_DATA_DIR"] = sys.argv[1]

def forbidden_network(*args, **kwargs):
    raise AssertionError("Core import must not access the network")
socket.socket.connect = forbidden_network

reports = {
    "aletheia.analytics.attribution",
    "aletheia.analytics.performance_summary",
    "aletheia.analytics.streaks",
    "aletheia.analytics.temporal",
    "aletheia.analytics.position_lifecycle_story",
    "aletheia.analytics.opportunity_lifecycle_story",
}
class MissingReports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in reports:
            raise ModuleNotFoundError("optional report absent", name=fullname)
sys.meta_path.insert(0, MissingReports())

from aletheia.portfolio.pnl import fee_in_quote
from aletheia.analytics.outcome_integrity import validated_fee
from aletheia.analytics.outcome_semantics import validated_outcome_economics

assert fee_in_quote(fee=0.1, fee_currency="USDT", fill_price=100, symbol="BTC/USDT") == 0.1
assert validated_fee(None, field="entry_fee") is None
assert validated_outcome_economics({}) is None
assert not reports.intersection(sys.modules)
assert not list(Path(sys.argv[1]).iterdir()), "import must not create runtime state"
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
