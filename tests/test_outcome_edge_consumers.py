"""Profitability consumers must exclude outcomes without a valid safety edge."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from typer.testing import CliRunner


def _insert_outcome(
    outcome_id: str,
    edge: object,
    *,
    net_return_pct: object = 0.1,
    cost_model_version: object = "v1",
    market_type: object = "spot",
    directional_return_pct: object = 0.1,
    estimated_fee_pct: object = 0.0,
    estimated_spread_slippage_pct: object = 0.0,
    estimated_funding_pct: object = 0.0,
    direction: str = "long",
) -> None:
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    if isinstance(directional_return_pct, (int, float)):
        price_return_pct = (
            directional_return_pct if direction == "long" else -directional_return_pct
        )
        exit_price_usd = 100.0 * (1.0 + price_return_pct / 100.0)
    else:
        exit_price_usd = None
    get_db().execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, market_type, direction, "
        "horizon, entry_price_usd, exit_price_usd, entry_ts, due_at, settled_at, forward_return_pct, "
        "gross_return_pct, directional_return_pct, estimated_fee_pct, "
        "estimated_spread_slippage_pct, estimated_funding_pct, net_return_pct, "
        "min_tradable_edge_pct, cost_model_version, status) "
        "VALUES (?, ?, 'edge-snapshot', 'BTC/USDT', 'momentum', ?, ?, "
        "'24h', 100, ?, ?, ?, ?, 0.1, 0.1, ?, ?, ?, ?, ?, ?, ?, 'settled')",
        (
            outcome_id, f"member-{outcome_id}", market_type, direction,
            exit_price_usd, now, now, now,
            directional_return_pct, estimated_fee_pct,
            estimated_spread_slippage_pct, estimated_funding_pct,
            net_return_pct, edge, cost_model_version,
        ),
    )


def test_operator_diagnostics_exclude_incomplete_safety_edges():
    from aletheia.agent_shell.trading_context import _get_data_health_summary
    from aletheia.diagnostics import doctor
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    _insert_outcome("complete", 0.05)
    _insert_outcome("legacy-complete", 0.05, cost_model_version="legacy-v0")
    _insert_outcome("missing", None)
    _insert_outcome("negative", -0.01)
    _insert_outcome("infinite", float("inf"))
    _insert_outcome("malformed", "malformed")
    _insert_outcome("blob", b"0.05")
    _insert_outcome("max-finite", float.fromhex("0x1.fffffffffffffp+1023"))
    _insert_outcome("missing-cost-model", 0.05, cost_model_version=None)
    _insert_outcome("blank-cost-model", 0.05, cost_model_version=" ")
    _insert_outcome("tab-cost-model", 0.05, cost_model_version="\t")
    _insert_outcome("unicode-space-cost-model", 0.05, cost_model_version="\N{NO-BREAK SPACE}")
    _insert_outcome("blob-cost-model", 0.05, cost_model_version=b"v1")
    _insert_outcome("malformed-net", 0.05, net_return_pct="malformed")
    _insert_outcome("blob-net", 0.05, net_return_pct=b"0.1")
    _insert_outcome("missing-fee", 0.05, estimated_fee_pct=None)
    _insert_outcome("mismatched-net", 0.05, net_return_pct=0.2)
    _insert_outcome("unknown-market", 0.05, market_type="crypto")
    _insert_outcome("blob-market", 0.05, market_type=b"spot")
    _insert_outcome(
        "spot-funding", 0.05, directional_return_pct=0.2,
        estimated_funding_pct=0.1,
    )
    get_db().commit()

    summary = _get_data_health_summary()
    assert summary["signal_win_rate_24h"] == 50.0
    assert summary["cost_model_version"] == "v1"

    report = doctor.Report(generated_at="test")
    doctor._probe_signal_effectiveness(report)
    signal = next(check for check in report.checks if check.name == "data.signal")
    assert "(2 settled" in signal.detail
    assert "50.0%" in signal.detail
    assert "cost_model=v1" in signal.detail

    result = CliRunner().invoke(app, ["audit", "data", "--json"])
    assert result.exit_code == 0, result.output
    effectiveness = json.loads(result.output)["signal_effectiveness_7d"]
    assert effectiveness["settled_outcomes"] == 2
    assert effectiveness["win_rate_pct"] == 50.0
    assert effectiveness["cost_model_version"] == "v1"


def test_sql_cost_model_whitespace_matches_python_strip():
    from aletheia.storage.sqlite import SQLITE_PYTHON_STRIP_CHARS_SQL, get_db

    whitespace = "".join(chr(codepoint) for codepoint in range(0x110000) if chr(codepoint).isspace())
    stripped = get_db().execute(
        f"SELECT TRIM(?, {SQLITE_PYTHON_STRIP_CHARS_SQL})",
        (whitespace,),
    ).fetchone()[0]
    assert stripped == ""


def test_sql_and_python_accept_the_same_reconciled_large_returns():
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.storage.repositories import complete_outcome_economics_sql
    from aletheia.storage.sqlite import get_db

    values = (
        (100.0, "long"),
        (100.0001, "long"),
        (200.0, "long"),
        (-100.0, "short"),
        (-100.0001, "short"),
        (-250.0, "short"),
    )
    for index, (value, direction) in enumerate(values):
        _insert_outcome(
            f"large-{index}", 0.05,
            directional_return_pct=value,
            net_return_pct=value,
            direction=direction,
        )

    rows = get_db().execute(
        "SELECT *, "
        f"CASE WHEN {complete_outcome_economics_sql()} THEN 1 ELSE 0 END AS sql_eligible "
        "FROM universe_forward_outcomes WHERE id LIKE 'large-%' ORDER BY id"
    ).fetchall()

    assert len(rows) == len(values)
    for raw in rows:
        row = dict(raw)
        assert validated_outcome_economics(row) is not None
        assert row["sql_eligible"] == 1


def test_sql_and_python_reject_directional_returns_not_reconciled_to_prices():
    from aletheia.analytics.outcome_semantics import validated_outcome_economics
    from aletheia.storage.repositories import complete_outcome_economics_sql
    from aletheia.storage.sqlite import get_db

    base = {
        "direction": "long",
        "entry_price_usd": 100.0,
        "exit_price_usd": 100.1,
        "directional_return_pct": 0.1,
        "estimated_fee_pct": 0.0,
        "estimated_spread_slippage_pct": 0.0,
        "estimated_funding_pct": 0.0,
        "net_return_pct": 0.1,
        "min_tradable_edge_pct": 0.05,
        "market_type": "spot",
        "cost_model_version": "v1",
    }
    fields = tuple(base)
    aliases = ", ".join(f"? AS {field}" for field in fields)

    def eligibility(row: dict[str, object]) -> tuple[bool, int]:
        sql_eligible = get_db().execute(
            "SELECT CASE WHEN "
            f"{complete_outcome_economics_sql()} THEN 1 ELSE 0 END "
            f"FROM (SELECT {aliases})",
            tuple(row[field] for field in fields),
        ).fetchone()[0]
        return validated_outcome_economics(row) is not None, sql_eligible

    invalid_mutations = (
        ("forged", "exit_price_usd", 100.0),
        ("missing-exit", "exit_price_usd", None),
        ("text-exit", "exit_price_usd", "100.1"),
        ("blob-exit", "exit_price_usd", b"100.1"),
        ("infinite-exit", "exit_price_usd", float("inf")),
        ("zero-exit", "exit_price_usd", 0.0),
        ("negative-exit", "exit_price_usd", -1.0),
        ("missing-entry", "entry_price_usd", None),
        ("text-entry", "entry_price_usd", "100"),
        ("blob-entry", "entry_price_usd", b"100"),
        ("infinite-entry", "entry_price_usd", float("inf")),
        ("zero-entry", "entry_price_usd", 0.0),
        ("negative-entry", "entry_price_usd", -1.0),
        ("unknown-direction", "direction", "flat"),
    )
    for _, field, value in invalid_mutations:
        assert eligibility({**base, field: value}) == (False, 0)

    assert eligibility({**base, "exit_price_usd": 100.1 + 5e-9}) == (True, 1)
    assert eligibility({**base, "exit_price_usd": 100.1 + 2e-8}) == (False, 0)

    direct = dict(base)
    direct["entry_price_usd"] = True
    assert validated_outcome_economics(direct) is None


def test_persisted_outlier_policy_not_consumer_cap_controls_large_returns():
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    _insert_outcome(
        "large-valid", 0.05,
        directional_return_pct=200.0,
        net_return_pct=200.0,
    )
    _insert_outcome(
        "physical-outlier", 0.05,
        directional_return_pct=1_000_001.0,
        net_return_pct=1_000_001.0,
    )
    get_db().commit()

    assert repo.mark_outlier_outcomes_failed() == 1
    statuses = {
        row["id"]: row["status"]
        for row in get_db().execute(
            "SELECT id, status FROM universe_forward_outcomes"
        ).fetchall()
    }
    assert statuses == {
        "large-valid": "settled",
        "physical-outlier": "failed",
    }
    calibration = repo.calibration_stats(cost_model_version="v1")
    assert len(calibration) == 1
    assert calibration[0]["n"] == 1
    assert calibration[0]["avg_return"] == 200.0
