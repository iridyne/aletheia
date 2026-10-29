"""Read-only exact-source probes; fake dependencies, no package startup/network/DB."""
import ast
import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path('/Users/yixian/Projects/aletheia-3134-pr66')

def extract(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), 'exec'), namespace)
    return namespace[name]

async def main():
    async def load():
        return None
    swap = dict(base='BTC', quote='USDT', symbol='BTC/USDT:USDT', id='BTCUSDT', swap=True, contractSize=1)
    future = dict(base='BTC', quote='USDT', symbol='BTC/USDT:USDT-261225', id='BTCUSDT_261225', future=True, contractSize=10)
    exchange = NS(markets={'swap': swap, 'dated': future}, load_markets=load)
    provider = NS(_primary_name='bitget', _ensure_primary=lambda *a: exchange, _market_type_context=lambda *a: nullcontext())
    read = extract('aletheia/data/ccxt_provider.py', 'get_market_instruments', {
        'MarketInstrument': NS, 'build_instrument_background': lambda **k: {},
        '_precision_to_decimals': lambda x: x, '_opt_float': lambda x: float(x) if x is not None else None,
    })
    instruments = await read(provider, 'perp')
    assert len(instruments) == 2
    assert not hasattr(exchange, '_candidate_markets_acquired_at')
    print('Batch65 acquisition timestamp: absent after successful actual get_market_instruments body')
    async def facts(**kw):
        return instruments
    feature_read = extract('aletheia/universe/features.py', '_read_market_metadata', {'to_ccxt_perp': lambda s: s + ':USDT'})
    metadata = await feature_read(NS(get_market_instruments=facts), 'perp')
    assert metadata['BTC/USDT:USDT']['contractSize'] == 10
    print('Batch65 dated contract collision: swap contractSize=1 overwritten with dated contractSize=10')

    # Use the real scheduler expression, with a config reflecting an explicit clear.
    tree = ast.parse((ROOT / 'aletheia/agent_runtime/scheduler.py').read_text())
    expression = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and ast.unparse(n.value) == 'cfg.market_data.observed_equities or cfg.market_data.observed_stocks')
    cfg = NS(market_data=NS(observed_equities=[], observed_stocks=['TSLA']))
    effective = eval(compile(ast.Expression(expression), '<scheduler actual expression>', 'eval'), {'cfg': cfg})
    assert effective == ['TSLA']
    print('Settings clear: canonical [] still schedules legacy TSLA')

    # Isolate the actual close predicate without performing any submission.
    tree = ast.parse((ROOT / 'aletheia/execution/coordinator.py').read_text())
    predicate = next(n.test for n in ast.walk(tree) if isinstance(n, ast.If)
                     and 'bitget_demo' in ast.unparse(n.test) and '_venue_match_cap_cancel_count' in ast.unparse(n.test))
    env = {'_venue_match_cap_cancel_count': lambda _: 1, 'position_id': 'fake'}
    decisions = {}
    for mode in ['bitget_demo', 'okx_demo']:
        decisions[mode] = eval(compile(ast.Expression(predicate), '<close actual predicate>', 'eval'), {**env, 'row': {'execution_mode': mode}})
    assert decisions == {'bitget_demo': 1, 'okx_demo': False}
    print('Close fallback: identical cancellation evidence is selected by product-name string:', decisions)

asyncio.run(main())
