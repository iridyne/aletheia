"""Explicit, foreground-only development preview commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from aletheia.runtime_paths import preview_instance_dir
from aletheia.config.loader import _PROJECT_ROOT

dev_app = typer.Typer(no_args_is_help=True)
from aletheia.cli.dev_seed import dev_seed_app

dev_app.add_typer(dev_seed_app, name="seed-markets", help="Populate the isolated preview instance with observation fixture data.")
_RESERVED_PORTS = frozenset({8787, 8788})


@dev_app.callback()
def dev() -> None:
    """Development preview commands; none manage the stable gateway."""


def project_root() -> Path:
    return _PROJECT_ROOT


def _preview_data_dir(root: Path, port: int) -> Path:
    return preview_instance_dir(root, port)


@dev_app.command()
def api(
    port: int = typer.Option(..., "--port", min=1024, max=65535, help="Unused loopback port, for example 8791."),
):
    """Run an isolated reloadable API; never starts or controls the main gateway."""
    if port in _RESERVED_PORTS:
        raise typer.BadParameter(f"port {port} is reserved for the stable gateway", param_hint="--port")

    root = project_root()
    data_dir = _preview_data_dir(root, port)
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = os.pathsep.join((str(root / "core"), str(root)))
    env.update({
        "ALETHEIA_PREVIEW_MODE": "1",
        "ALETHEIA_PREVIEW_DATA_DIR": str(data_dir),
        "ALETHEIA_RUNTIME_MODE": "shadow",
        "ALETHEIA_RUNTIME_STATE": "shadow",
        "ALETHEIA_RUNTIME_ID": f"dev-preview-{port}",
        "ALETHEIA_RUNTIME_PORT": str(port),
    })
    command = [
        sys.executable, "-m", "uvicorn", "aletheia.server.app:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--reload", "--reload-dir", str(root / "core" / "aletheia"),
    ]
    if (root / "extensions").is_dir():
        command.extend(["--reload-dir", str(root / "extensions")])
    typer.echo(
        f"Starting isolated preview API at http://127.0.0.1:{port} "
        f"with isolated runtime state under {data_dir}"
    )
    result = subprocess.run(command, cwd=root, env=env, check=False)
    raise typer.Exit(getattr(result, "returncode", 0))


_TEST_ORDER_OK = frozenset({"executed", "submitted", "partially_filled"})


@dev_app.command("test-order")
def test_order(
    symbol: str = typer.Argument(..., help="Canonical symbol, for example BTC/USDT."),
    direction: str = typer.Option(..., "--direction", help="long or short."),
    quantity: float = typer.Option(..., "--quantity", min=0.0, help="Base-asset quantity."),
    price: float = typer.Option(..., "--price", min=0.0, help="Limit price; set it on the marketable side to fill at once."),
    stop: float = typer.Option(..., "--stop", min=0.0, help="Protective stop price."),
    take_profit: float = typer.Option(..., "--take-profit", min=0.0, help="Take-profit price."),
    leverage: int = typer.Option(1, "--leverage", min=1),
    horizon_hours: float = typer.Option(24.0, "--horizon-hours", min=0.0),
):
    """Debug-only: send one USDT-perp Proposal through Risk and the Gateway on a demo account."""
    from aletheia.config.exchanges import DEMO_EXECUTION_MODES
    from aletheia.config.loader import load_config
    from aletheia.data.symbols import to_canonical
    from aletheia.execution import gateway
    from aletheia.files.jsonl import write_audit
    from aletheia.storage.repositories import get_order
    from aletheia.trading.entry_plan import calculate_entry_plan_risk_reward

    mode = (load_config().exchange.paper_execution_mode or "").strip()
    if mode not in DEMO_EXECUTION_MODES:
        typer.echo(f"refused: execution mode {mode!r} is not a demo execution mode; no Proposal was created", err=True)
        raise typer.Exit(2)
    if direction not in {"long", "short"}:
        raise typer.BadParameter("must be long or short", param_hint="--direction")

    # Risk requires entry price, stop, take-profit and reward:risk on every entry (check 2b) and
    # compares reward:risk against its own rounded value, so use the same function.
    risk_reward = calculate_entry_plan_risk_reward(price, stop, take_profit)
    if risk_reward is None:
        raise typer.BadParameter("--stop must differ from --price", param_hint="--stop")

    result = gateway.submit_proposal(
        symbol=to_canonical(symbol),
        market_type="usdt_futures",
        direction=direction,
        order_type="limit",
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop,
        take_profit=take_profit,
        risk_reward=risk_reward,
        execution_mode=mode,
        agent_rationale="aletheia dev test-order (debug CLI)",
        trade_school="short_term",
        expected_horizon_hours=horizon_hours,
        stop_source="operator",
        source="native",
    )
    status = str(result.get("status"))
    order = get_order(result["order_id"]) if result.get("order_id") else None
    typer.echo(f"proposal_id: {result.get('proposal_id')}")
    if "risk_violations" in result:
        typer.echo(f"risk: rejected violations={result['risk_violations']}")
    else:
        typer.echo(f"risk: {'approved risk_decision_id=' + str(order.get('risk_decision_id')) if order else 'no order (see status)'}")
    typer.echo(f"status: {status} reason_code={result.get('reason_code')} message={result.get('message')}")
    if order:
        typer.echo(f"order: id={order['id']} status={order['status']} exchange_order_id={order.get('exchange_order_id')} error_reason={order.get('error_reason')}")
    typer.echo(f"position_id: {result.get('position_id') or (order or {}).get('position_id')}")
    write_audit({
        "actor": "human", "source": "cli",
        "event_type": "dev_test_order_submitted",
        "entity_type": "trade_proposal", "entity_id": result.get("proposal_id"),
        "after": {"status": status, "execution_mode": mode, "symbol": to_canonical(symbol), "direction": direction},
        "message": f"Debug test-order via CLI: {status}",
    })
    if status not in _TEST_ORDER_OK:
        raise typer.Exit(1)
