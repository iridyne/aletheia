"""Architecture fitness functions — L2a structural invariants.

Checks dependency direction, symbol existence, and safety defaults
without executing business code. Based on Building Evolutionary Architectures.
"""

import ast
import inspect
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "core" / "aletheia"


def _get_imports(path: Path) -> set[str]:
    """Extract all import module names from a Python file via AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


# ---------------------------------------------------------------------------
# Dependency direction rules
# ---------------------------------------------------------------------------


def test_core_funding_has_no_retired_native_bill_parsers():
    from aletheia.execution import broker_funding

    for name in (
        "normalize_bitget_funding_rows",
        "normalize_okx_funding_rows",
        "normalize_binance_funding_rows",
        "require_retained_history",
    ):
        assert not hasattr(broker_funding, name), name


def test_core_startup_gate_has_no_concrete_exchange_profile_logic():
    source = (SRC / "agent_runtime" / "startup_behavior_gate.py").read_text(encoding="utf-8")
    assert "configuration_diagnostic" in source
    for retired in ("_check_bitget_profile", "KNOWN_BITGET_PROFILES", "classic_v2", "uta_v3"):
        assert retired not in source


def test_core_tpsl_reader_does_not_encode_venue_wire_symbols():
    from aletheia.agent_runtime import scheduler
    source = inspect.getsource(scheduler._tpsl_fill_check_body)
    for converter in ("to_bitget", "to_binance", "to_okx"):
        assert converter not in source


@pytest.mark.parametrize("package", ["exchanges", "model_endpoints", "defillama", "coingecko", "hyperliquid", "deribit", "volume_scanner", "coinalyze", "fred", "macro", "telegram"])
def test_collector_and_transport_extensions_keep_direct_storage_and_lifecycle_boundaries(package):
    """Direct import guard; not a claim that transitive helpers are write-free."""
    forbidden = (
        "aletheia.storage", "aletheia.agent_runtime.scheduler",
        "aletheia.agent_runtime.job_manager", "sqlite3", "apscheduler",
    )
    violations = []
    public_collector = package in {"defillama", "coingecko", "hyperliquid", "deribit", "volume_scanner", "coinalyze", "fred", "macro"}
    paths = list((PROJECT_ROOT / "extensions" / package).rglob("*.py"))
    assert paths, package
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Coinalyze compares only same-source historical OI. This read
            # grants no general repository access or mutation ownership.
            if (package == "coinalyze" and isinstance(node, ast.ImportFrom)
                    and node.module == "aletheia.storage.repositories"
                    and [alias.name for alias in node.names] == ["open_interest_near"]):
                continue
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [
                    f"{node.module}.{alias.name}" for alias in node.names
                ]
            else:
                continue
            if public_collector:
                # DTOs are shared; only package composition binds the Core
                # scheduler. Producer implementations own neither DB nor jobs.
                names = [name for name in names if not (
                    name == "aletheia.storage.models" or name.startswith("aletheia.storage.models.")
                    or (path == PROJECT_ROOT / "extensions" / package / "__init__.py"
                        and (name == "aletheia.agent_runtime.scheduler"
                             or name.startswith("aletheia.agent_runtime.scheduler.")))
                )]
            if any(name == prefix or name.startswith(prefix + ".")
                   for name in names for prefix in forbidden):
                violations.append((str(path.relative_to(PROJECT_ROOT)), node.lineno))
    assert not violations


@pytest.mark.parametrize("entrypoint", ["api", "cli", "collector"])
@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("component", ["market_data", "market_capabilities", "orderbook", "registry_catalog"])
def test_native_market_data_binding_is_optional_and_does_not_create_clients(tmp_path, entrypoint, missing, component):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys
missing = sys.argv[2] == 'True'
class MissingNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if missing and fullname == 'extensions.exchanges.ccxt.' + sys.argv[3]:
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, MissingNative())
from aletheia import net
def forbidden(*args, **kwargs):
    raise AssertionError('composition must not create clients')
net.make_ccxt = forbidden
from aletheia.data import ccxt_provider, venue_listing_cache
from aletheia.data.collectors import microstructure
from aletheia.data import market_catalog
def verify():
    native_missing = missing and sys.argv[3] == 'market_data'
    listing_missing = missing and sys.argv[3] == 'market_capabilities'
    orderbook_missing = missing and sys.argv[3] == 'orderbook'
    assert (market_catalog._catalog_fetch is None) == (missing and sys.argv[3] == 'registry_catalog')
    assert (microstructure._orderbook_collect is None) == orderbook_missing
    assert (ccxt_provider._native_provider_factory is None) == native_missing
    assert (venue_listing_cache._listing_fetch is None) == listing_missing
    assert not hasattr(ccxt_provider, 'CCXTProvider')
    if native_missing:
        try:
            ccxt_provider.get_market_data_provider('fixture', native_ccxt=True)
        except RuntimeError as exc:
            assert str(exc) == 'ccxt_market_data_extension_unavailable'
        else:
            raise AssertionError('absent adapter cannot construct a client')
    else:
        provider = ccxt_provider.get_market_data_provider('fixture', native_ccxt=True)
        assert provider._sessions == []
    assert ccxt_provider.timeframe_ms('1h') == 3600000
if sys.argv[1] == 'api':
    from aletheia.server.app import app
    assert '/api/health' in {getattr(r, 'path', '') for r in app.routes}
    verify()
elif sys.argv[1] == 'cli':
    from aletheia.main import app
    verify()
else:
    from aletheia.data import collector_runner
    from aletheia.agent_runtime import scheduler
    from aletheia.config import loader
    from aletheia import logging_config
    loader.bootstrap = lambda: None
    logging_config.configure_logging = lambda: None
    collector_runner.signal.signal = lambda *args: None
    def first_tick():
        verify()
        raise SystemExit(0)
    scheduler._run_data_collectors = first_tick
    collector_runner.main()
""", entrypoint, str(missing), component],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("entrypoint", ["aletheia.main", "aletheia.server.app"])
@pytest.mark.parametrize("missing", [False, True])
def test_learning_composition_is_absent_safe_in_fresh_process(tmp_path, entrypoint, missing):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib
import importlib.abc
import importlib.util
import sys

missing = sys.argv[2] == 'True'
class MissingLearning(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if missing and (fullname == 'extensions.learning' or fullname.startswith('extensions.learning.')):
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, MissingLearning())
entrypoint = importlib.import_module(sys.argv[1])
assert entrypoint.app
from aletheia.agents import _AGENTS
from aletheia.agent_runtime import scheduler
for name in ('lesson_reviewer', 'dreaming_agent', 'post_trade_reviewer'):
    assert (name in _AGENTS) == (not missing), name
    if not missing:
        assert _AGENTS[name].__module__ == 'extensions.learning.' + name
    assert importlib.util.find_spec('aletheia.agents.' + name) is None
assert (scheduler._lesson_extractor is None) == missing
assert (scheduler._memo_synthesizer is None) == missing
from aletheia.analytics import confidence_calibration
assert (confidence_calibration._calibration_builder is None) == missing
assert not hasattr(confidence_calibration, 'build_calibration_report')
from aletheia.analytics import reasoning_feedback
assert (reasoning_feedback._feedback_builder is None) == missing
assert not hasattr(reasoning_feedback, 'build_reasoning_feedback')
assert importlib.util.find_spec('aletheia.analytics.trade_case_builder') is None
if missing:
    from aletheia.server.routes.analytics import get_confidence_calibration, get_reasoning_feedback
    from aletheia.analytics.insight_synthesizer import get_latest_memo
    from aletheia.storage.sqlite import get_db
    db = get_db()
    before = db.total_changes
    assert get_confidence_calibration(execution_mode='bitget_demo')['status'] == 'no_data'
    assert get_reasoning_feedback(execution_mode='bitget_demo')['status'] == 'no_data'
    assert get_latest_memo(execution_mode='bitget_demo') is None
    assert db.total_changes == before
    assert not any(name == 'extensions.learning' or name.startswith('extensions.learning.') for name in sys.modules)
else:
    from extensions.learning.dreaming_agent import _extract_lessons
    assert scheduler._lesson_extractor is _extract_lessons
    from extensions.learning.insight_synthesizer import synthesize_trading_memo
    assert scheduler._memo_synthesizer is synthesize_trading_memo
    from extensions.learning.confidence_calibration import build_calibration_report
    assert confidence_calibration._calibration_builder is build_calibration_report
    from extensions.learning.reasoning_feedback import build_reasoning_feedback
    assert reasoning_feedback._feedback_builder is build_reasoning_feedback
""", entrypoint, str(missing)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_consumers_do_not_import_concrete_extensions():
    composition = {SRC / "main.py", SRC / "server" / "app.py"}
    forbidden = ("extensions", "openai", "anthropic")
    violations = []
    for path in SRC.rglob("*.py"):
        if path in composition:
            continue
        for module in _get_imports(path):
            # Existing standalone process composes only its public collector.
            if path == SRC / "data" / "collector_runner.py" and module in {
                "extensions.defillama", "extensions.coingecko", "extensions.hyperliquid",
                "extensions.deribit", "extensions.volume_scanner", "extensions.coinalyze",
                "extensions.market_data", "extensions.fred", "extensions.macro",
            }:
                continue
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                violations.append((str(path.relative_to(SRC)), module))
    assert not violations


@pytest.mark.parametrize("entrypoint", ["aletheia.main", "aletheia.server.app"])
@pytest.mark.parametrize("missing", [False, True])
def test_fast_stream_composition_is_absent_safe_without_frozen_venues(tmp_path, entrypoint, missing):
    result = subprocess.run([sys.executable, "-c", """
import importlib
import importlib.abc
import sys
import aiohttp
from aletheia.universe import fast_stream
assert fast_stream._FAST_STREAM_ADAPTERS == {}
assert not any(hasattr(fast_stream, name) for name in (
    'BitgetFastMarketStream', 'BinanceFastMarketStream', 'parse_bitget_message', 'parse_binance_message'))
def forbidden(*args, **kwargs):
    raise AssertionError('composition must not create a network client')
aiohttp.ClientSession = forbidden
missing = sys.argv[2] == 'True'
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if missing and fullname == 'extensions.exchanges.bitget.fast_stream':
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Block())
importlib.import_module(sys.argv[1])
from aletheia.config.schema import AletheiaConfig
cfg = AletheiaConfig()
cfg.exchange.paper_execution_mode = 'bitget_demo'
assert fast_stream.fast_market_stream_available_for_config(cfg) == (not missing)
assert fast_stream.fast_market_data_unavailability_reason(cfg, 'bitget', 'perp') == (
    'FAST_MARKET_DATA_STREAM_UNAVAILABLE' if missing else None)
if missing:
    try:
        fast_stream.fast_market_stream_for_config(cfg)
    except RuntimeError as exc:
        assert str(exc) == 'fast_trigger_stream_unavailable:bitget'
    else:
        raise AssertionError('missing adapter created a stream')
else:
    stream = fast_stream.fast_market_stream_for_config(cfg)
    assert type(stream).__module__ == 'extensions.exchanges.bitget.fast_stream'
    assert stream._session is None and stream._ping_task is None
assert not fast_stream.fast_market_stream_available('binance_testnet')
assert 'extensions.exchanges.binance.fast_stream' not in sys.modules
""", entrypoint, str(missing)], cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "PYTHONPATH": str(PROJECT_ROOT / "core") + os.pathsep + str(PROJECT_ROOT)},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_model_modules_and_doctor_do_not_import_raw_secret_reader():
    from aletheia.diagnostics import doctor
    assert "bitget.demo." not in inspect.getsource(doctor._probe_credentials)
    for path in [*(SRC / "model_provider").glob("*.py"), SRC / "diagnostics" / "doctor.py",
                 *(SRC / "telegram").glob("*.py"), SRC / "server" / "routes" / "telegram.py",
                 SRC / "gateway" / "frontdoor.py", SRC / "server" / "app.py",
                 PROJECT_ROOT / "extensions" / "macro" / "collector.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "aletheia.config.secrets":
                assert "get_secret" not in {alias.name for alias in node.names}, path


@pytest.mark.parametrize("entrypoint", ["aletheia.server.app", "aletheia.main"])
@pytest.mark.parametrize("missing", [False, True])
def test_telegram_composition_is_optional_and_does_not_resolve_token(tmp_path, entrypoint, missing):
    result = subprocess.run(
        [sys.executable, "-c", """
import asyncio
import importlib
import importlib.abc
import sys
import aletheia.config.secrets as secrets
def forbidden(*args, **kwargs):
    raise AssertionError('composition must not resolve credentials')
secrets.get_secret = forbidden
class MissingTelegram(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if sys.argv[2] == 'True' and (fullname == 'extensions.telegram' or fullname.startswith('extensions.telegram.')):
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, MissingTelegram())
importlib.import_module(sys.argv[1])
from aletheia.telegram import create_transport, transport_health
bot = create_transport()
assert (bot is None) == (sys.argv[2] == 'True')
if bot is None:
    assert asyncio.run(transport_health()) == {'reachable': False, 'error': 'adapter_unavailable'}
else:
    assert bot.token is None
    assert not bot._running
""", entrypoint, str(missing)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing_sdks", ["openai", "anthropic", "openai,anthropic"])
def test_core_api_imports_without_model_sdks(tmp_path, missing_sdks):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys

missing = set(sys.argv[1].split(','))

class MissingModelSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in missing:
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingModelSDK())
from aletheia.server.app import app
from aletheia.model_provider.router import PROVIDER_REGISTRY
assert set(PROVIDER_REGISTRY) == {
    sdk + '_compatible' for sdk in {'openai', 'anthropic'} - missing
}
assert app.routes
assert not any(name.split('.')[0] in missing for name in sys.modules)
""", missing_sdks],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing_routes", ["news", "prediction", "news,prediction"])
def test_core_api_registers_without_optional_route_modules(tmp_path, missing_routes):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys

modules = {'news': 'extensions.news.api', 'prediction': 'extensions.prediction.api'}
missing = {modules[name] for name in sys.argv[1].split(',')}

class MissingOptionalRoute(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in missing:
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingOptionalRoute())
from aletheia.server.app import app
paths = {getattr(route, 'path', '') for route in app.routes}
assert '/api/health' in paths
for name, route in [('news', '/api/news'), ('prediction', '/api/prediction/status')]:
    assert (route in paths) == (modules[name] not in missing)
assert not missing.intersection(sys.modules)
""", missing_routes],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing_news", ["collector", "analyzer", "collector,analyzer", "all"])
def test_news_producer_binding_is_independently_absent_safe(tmp_path, missing_news):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys

parts = set(sys.argv[1].split(','))
missing = {'extensions.news'} if 'all' in parts else {'extensions.news.' + p for p in parts}

class MissingNews(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == item or fullname.startswith(item + '.') for item in missing):
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingNews())
from aletheia.server.app import app
from aletheia.agent_runtime import scheduler
assert '/api/health' in {getattr(route, 'path', '') for route in app.routes}
assert (scheduler._news_fetch is None) == bool(parts & {'collector', 'all'})
assert (scheduler._news_prune is None) == bool(parts & {'collector', 'all'})
assert (scheduler._news_analyze is None) == bool(parts & {'analyzer', 'all'})
""", missing_news],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing_defillama", ["none", "collector", "all"])
@pytest.mark.parametrize("extension", ["defillama", "coingecko", "hyperliquid", "deribit", "volume_scanner", "coinalyze", "fred", "macro"])
@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_public_collector_binding_is_optional_at_composition(tmp_path, missing_defillama, extension, entrypoint):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys
package = 'extensions.' + sys.argv[2]
producer = {'coingecko': '.top', 'deribit': '.options_structure'}.get(sys.argv[2], '.collector')
missing = {'collector': package + producer, 'all': package}.get(sys.argv[1])
class MissingDefillama(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if missing and (fullname == missing or fullname.startswith(missing + '.')):
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, MissingDefillama())
if sys.argv[3] == 'api':
    from aletheia.server.app import app
    assert '/api/health' in {getattr(route, 'path', '') for route in app.routes}
else:
    from aletheia.main import app
    assert app is not None
from aletheia.agent_runtime import scheduler
name = {'coingecko': 'coingecko_top', 'deribit': 'options_structure'}.get(sys.argv[2], sys.argv[2])
if name == 'fred':
    from extensions.macro import collector as macro
    callback = macro._fred_research_fetch
    assert (macro._fred_macro_fetch is None) == bool(missing)
    assert (macro._fred_is_configured is None) == bool(missing)
else:
    callback = getattr(scheduler, '_' + name + '_collect')
assert (callback is None) == bool(missing)
""", missing_defillama, extension, entrypoint],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing", ["none", "defillama", "coingecko", "hyperliquid", "deribit", "volume_scanner", "coinalyze", "fred", "macro", "all"])
def test_collector_process_binds_public_collectors_before_first_tick(tmp_path, missing):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys
missing = {'defillama', 'coingecko', 'hyperliquid', 'deribit', 'volume_scanner', 'coinalyze', 'fred', 'macro'} if sys.argv[1] == 'all' else {sys.argv[1]}
class MissingDefillama(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == 'extensions.' + item or fullname.startswith('extensions.' + item + '.') for item in missing):
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, MissingDefillama())
from aletheia.data import collector_runner
from aletheia.data import ccxt_provider
try:
    from extensions.macro import collector as macro
except ModuleNotFoundError:
    macro = None
from aletheia.agent_runtime import scheduler
from aletheia.config import loader
from aletheia import logging_config
loader.bootstrap = lambda: None
logging_config.configure_logging = lambda: None
collector_runner.signal.signal = lambda *args: None
def first_tick():
    valid = ((scheduler._defillama_collect is None) == ('defillama' in missing)
             and callable(ccxt_provider._spot_fallback_factory)
             and (scheduler._coingecko_top_collect is None) == ('coingecko' in missing)
             and (scheduler._hyperliquid_collect is None) == ('hyperliquid' in missing)
             and (scheduler._options_structure_collect is None) == ('deribit' in missing)
             and (scheduler._volume_scanner_collect is None) == ('volume_scanner' in missing)
             and (scheduler._coinalyze_collect is None) == ('coinalyze' in missing)
             and (macro is None or macro._fred_research_fetch is None) == bool(missing & {'fred', 'macro'})
             and (scheduler._macro_collect is None) == ('macro' in missing)
             and 'aletheia.main' not in sys.modules
             and 'aletheia.server.app' not in sys.modules)
    raise SystemExit(0 if valid else 3)
scheduler._run_data_collectors = first_tick
collector_runner.main()
""", str(missing)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("missing_prediction", ["none", "observer", "all"])
def test_prediction_observer_uses_existing_registry_and_is_optional(tmp_path, missing_prediction):
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys

missing = {'observer': 'extensions.prediction.observer', 'all': 'extensions.prediction'}.get(sys.argv[1])

class MissingPrediction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if missing and (fullname == missing or fullname.startswith(missing + '.')):
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingPrediction())
from aletheia.server.app import app
from aletheia.agents import get_agent_fn
assert '/api/health' in {getattr(route, 'path', '') for route in app.routes}
if missing:
    try:
        get_agent_fn('prediction_observer')
    except ValueError:
        pass
    else:
        raise AssertionError('missing Prediction Extension retained a callable observer')
else:
    from extensions.prediction.observer import run_prediction_observer
    assert get_agent_fn('prediction_observer') is run_prediction_observer
""", missing_prediction],
        cwd=PROJECT_ROOT,
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_candidate_snapshot_creation_has_one_core_owner():
    import re
    from aletheia.storage import repositories as repo

    retired = {"insert_universe_snapshot", "insert_universe_member", "insert_universe_members_batch",
               "insert_candidate", "publish_serenity_candidates"}
    assert not retired.intersection(vars(repo))
    writes = set()
    for path in [
        *SRC.rglob("*.py"),
        *(PROJECT_ROOT / "extensions").rglob("*.py"),
        *(PROJECT_ROOT / "ops").rglob("*.py"),
    ]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(r"\b(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+(?:universe_members|universe_snapshots)\b", node.value, re.I), (path, node.lineno)
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            if not path.is_relative_to(SRC):
                assert name != "publish_candidate_snapshot", (path, node.lineno)
            if not node.args:
                continue
            table = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
            if name not in {"_insert", "_insert_many"} or table not in {"universe_members", "universe_snapshots"}:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents.get(parent)
            writes.add((str(path.relative_to(PROJECT_ROOT)), parent.name if parent else None, table))
    assert writes == {
        ("core/aletheia/storage/repositories.py", "publish_candidate_snapshot", "universe_snapshots"),
        ("core/aletheia/storage/repositories.py", "publish_candidate_snapshot", "universe_members"),
    }
    assert "aletheia.trading.serenity_publication" not in _get_imports(SRC / "storage" / "repositories.py")


def test_planning_and_entry_tables_have_one_storage_boundary():
    import re

    allowed = {
        SRC / "storage" / "repositories.py",
        SRC / "storage" / "migrations.py",
    }
    pattern = re.compile(
        r"\b(?:(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+"
        r"|UPDATE\s+|DELETE\s+FROM\s+)"
        r"(?:candidate_planning_generations|entry_plans)\b",
        re.I,
    )
    for sample in (
        "INSERT OR IGNORE INTO entry_plans (id) VALUES (?)",
        "UPDATE CANDIDATE_PLANNING_GENERATIONS SET status='planned'",
        "DELETE FROM entry_plans WHERE id=?",
    ):
        assert pattern.search(sample)
    for root in (SRC, PROJECT_ROOT / "extensions", PROJECT_ROOT / "ops"):
        for path in root.rglob("*.py"):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert not pattern.search(node.value), (path, node.lineno)


def test_risk_decision_mutation_has_one_core_owner():
    import re

    repository = SRC / "storage" / "repositories.py"
    engine = SRC / "risk" / "engine.py"
    migrations = SRC / "storage" / "migrations.py"
    writers = {"insert_risk_decision", "insert_authoritative_risk_decision"}
    sql = re.compile(
        r"\b(?:(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+"
        r"|UPDATE\s+|DELETE\s+FROM\s+)risk_decisions\b",
        re.I,
    )
    assert sql.search("INSERT OR IGNORE INTO risk_decisions (id) VALUES (?)")
    assert sql.search("UPDATE RISK_DECISIONS SET approved=0")
    assert sql.search("DELETE FROM risk_decisions WHERE id=?")
    for root in (SRC, PROJECT_ROOT / "extensions", PROJECT_ROOT / "ops"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "aletheia.storage.repositories":
                    if any(alias.name in writers for alias in node.names):
                        assert path in {repository, engine}, (path, node.lineno)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if path not in {repository, migrations}:
                        assert not sql.search(node.value), (path, node.lineno)
                if isinstance(node, ast.Call):
                    name = (
                        node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute)
                        else None
                    )
                    if name in writers:
                        assert path in {repository, engine}, (path, node.lineno, name)


def test_order_storage_and_submission_have_one_core_boundary():
    import re

    repository = SRC / "storage" / "repositories.py"
    migrations = SRC / "storage" / "migrations.py"
    coordinator = SRC / "execution" / "coordinator.py"
    gateway = SRC / "execution" / "gateway.py"
    sql = re.compile(
        r"\b(?:(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+"
        r"|UPDATE\s+|DELETE\s+FROM\s+)orders\b",
        re.I,
    )
    for sample in (
        "INSERT OR IGNORE INTO orders (id) VALUES (?)",
        "UPDATE ORDERS SET status='pending'",
        "DELETE FROM orders WHERE id=?",
    ):
        assert sql.search(sample)
    for root in (SRC, PROJECT_ROOT / "extensions", PROJECT_ROOT / "ops"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "aletheia.execution.coordinator":
                    if any(alias.name == "execute_order" for alias in node.names):
                        assert path == gateway, (path, node.lineno)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if path not in {repository, migrations}:
                        assert not sql.search(node.value), (path, node.lineno)
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else None
                )
                if name in {"_insert", "_insert_many"} and node.args:
                    table = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
                    if table == "orders":
                        assert path == repository, (path, node.lineno)
                if name == "execute_order":
                    assert path == gateway, (path, node.lineno)
                if name == "submit_order":
                    assert path == coordinator, (path, node.lineno)
                if (
                    name == "_call_and_close"
                    and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "submit_order"
                ):
                    assert path == coordinator, (path, node.lineno)


def test_fill_materialization_has_one_core_owner():
    import re

    repository = SRC / "storage" / "repositories.py"
    migrations = SRC / "storage" / "migrations.py"
    materialization = SRC / "execution" / "exchange_materialization.py"
    writers = {"insert_fill", "update_fill_fee_source"}
    sql = re.compile(
        r"\b(?:(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+"
        r"|UPDATE\s+|DELETE\s+FROM\s+)fills\b",
        re.I,
    )
    for sample in (
        "INSERT OR IGNORE INTO fills (id) VALUES (?)",
        "UPDATE FILLS SET fee_source='reconciled'",
        "DELETE FROM fills WHERE id=?",
    ):
        assert sql.search(sample)
    for root in (SRC, PROJECT_ROOT / "extensions", PROJECT_ROOT / "ops"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "aletheia.storage.repositories":
                    if any(alias.name in writers for alias in node.names):
                        assert path in {repository, materialization}, (path, node.lineno)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if path not in {repository, migrations}:
                        assert not sql.search(node.value), (path, node.lineno)
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else None
                )
                if name in {"_insert", "_insert_many", "_update"} and node.args:
                    table = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
                    if table == "fills":
                        assert path == repository, (path, node.lineno)
                if name in writers:
                    assert path == materialization, (path, node.lineno, name)


def test_core_connectivity_does_not_own_exchange_probe_protocol():
    path = SRC / "agent_runtime" / "connectivity.py"
    source = path.read_text(encoding="utf-8")
    assert not any(domain in source for domain in ("api.bitget.com", "www.okx.com", "api.binance.com"))
    tree = ast.parse(source)
    probe = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "probe_exchange")
    assert "httpx_client" not in ast.unparse(probe)


def test_gateway_startup_has_no_bulk_secret_migration():
    """Startup must not enumerate credentials or hide a historical repair."""
    app_tree = ast.parse((SRC / "server" / "app.py").read_text(encoding="utf-8"))
    lifespan = next(node for node in app_tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "aletheia.config.secrets"
        and any(alias.name in {"migrate_keyring_to_file", "get_secret", "set_secret", "_get_keyring"} for alias in node.names)
        for node in ast.walk(lifespan)
    )
    secrets_tree = ast.parse((SRC / "config" / "secrets.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "migrate_keyring_to_file"
        or isinstance(node, ast.Attribute) and node.attr == "CredEnumerate"
        for node in ast.walk(secrets_tree)
    )


def test_legacy_hook_cleanup_is_explicit_doctor_work_only():
    callers = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "remove_managed_git_hooks"
                or isinstance(node.func, ast.Attribute) and node.func.attr == "remove_managed_git_hooks"
            ):
                callers.add(str(path.relative_to(SRC)))
    assert callers == {"diagnostics/doctor.py"}


def test_risk_engine_does_not_import_agents():
    """Risk Engine must not depend on agents/ (unidirectional data flow)."""
    imports = _get_imports(SRC / "risk" / "engine.py")
    violations = [i for i in imports if i.startswith("aletheia.agents")]
    assert violations == [], f"ARCH VIOLATION: risk/engine.py imports agents/: {violations}"


def test_risk_engine_does_not_import_agent_shell():
    """Risk Engine must not depend on agent_shell/."""
    imports = _get_imports(SRC / "risk" / "engine.py")
    violations = [i for i in imports if "agent_shell" in i]
    assert violations == [], f"ARCH VIOLATION: risk/engine.py imports agent_shell/: {violations}"


def test_execution_gateway_does_not_import_agent_shell():
    """Execution Gateway must not depend on agent_shell/."""
    imports = _get_imports(SRC / "execution" / "gateway.py")
    violations = [i for i in imports if "agent_shell" in i]
    assert violations == [], f"ARCH VIOLATION: gateway.py imports agent_shell/: {violations}"


def test_core_account_environment_uses_registered_execution_identity():
    """Core safety/economic consumers must not infer account class from suffixes."""
    from aletheia.config.exchanges import (
        DEMO_EXECUTION_MODES,
        EXECUTION_MODE_ENVIRONMENTS,
        LIVE_EXECUTION_MODE_SET,
    )
    from aletheia.risk.policy import (
        DEMO_EXECUTION_MODES as RISK_DEMO_EXECUTION_MODES,
        LIVE_EXECUTION_MODES as RISK_LIVE_EXECUTION_MODES,
    )

    assert RISK_DEMO_EXECUTION_MODES is DEMO_EXECUTION_MODES
    assert RISK_LIVE_EXECUTION_MODES is LIVE_EXECUTION_MODE_SET
    assert set(EXECUTION_MODE_ENVIRONMENTS) >= {
        "paper_internal",
        "binance_testnet",
        "okx_demo",
        "bitget_demo",
        "binance_live_limited",
        "okx_live_limited",
        "bitget_live_limited",
    }

    core_paths = (
        SRC / "server" / "routes" / "data.py",
        SRC / "portfolio" / "position_manager.py",
        SRC / "portfolio" / "pnl.py",
        PROJECT_ROOT / "ops" / "maintenance" / "repair_administrative_close.py",
        SRC / "agent_runtime" / "scheduler.py",
        SRC / "risk" / "engine.py",
    )
    for path in core_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        suffix_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "endswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in {"_demo", "_live_limited"}
        ]
        assert suffix_calls == [], f"{path.relative_to(PROJECT_ROOT)} infers account class from a suffix"


def test_entry_route_gates_use_registered_capabilities():
    """Generic Entry consumers must not hard-code the current venue route."""
    route = "bitget_demo"
    consumers = (
        SRC / "trading" / "entry_plan_compiler.py",
        SRC / "trading" / "entry_authority.py",
        SRC / "execution" / "entry_plan_execution.py",
        SRC / "execution" / "gateway.py",
        SRC / "risk" / "engine.py",
    )
    for path in consumers:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hardcoded = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(value, ast.Constant) and value.value == route
                for value in ast.walk(node)
            )
        ]
        assert hardcoded == [], f"{path.relative_to(PROJECT_ROOT)} hard-codes {route} in a route gate"


def test_agent_shell_does_not_import_exchange_auth():
    """agent_shell cannot directly import exchange auth or secrets."""
    forbidden = {"extensions.exchanges.bitget.auth", "aletheia.config.secrets"}
    for name in ("permissions.py", "registry.py", "runtime.py"):
        path = SRC / "agent_shell" / name
        if not path.exists():
            continue
        imports = _get_imports(path)
        violations = [i for i in imports if any(i.startswith(f) for f in forbidden)]
        assert violations == [], (
            f"ARCH VIOLATION: agent_shell/{name} imports trust-boundary: {violations}"
        )


# ---------------------------------------------------------------------------
# Symbol existence & completeness
# ---------------------------------------------------------------------------


def test_forbidden_intents_complete():
    """FORBIDDEN_INTENTS contains the 7 core contractually required items.

    (Subset check — the set also includes the 2 OL-C03 double-lock additions
    deactivate_kill_switch/modify_permissions since 2026-06-23; this test only
    asserts the 7 core items are present, so additions never break it.)
    """
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    required = frozenset([
        "enable_live", "modify_credentials", "bypass_risk",
        "place_live_order", "modify_execution_gateway",
        "delete_knowledge", "change_risk_limits",
    ])
    missing = required - FORBIDDEN_INTENTS
    assert not missing, f"FORBIDDEN_INTENTS missing: {missing}"


def test_restricted_tools_complete():
    """RESTRICTED_TOOLS contains the 5 contractually required tools."""
    from aletheia.agent_runtime.tool_registry import RESTRICTED_TOOLS

    required = {
        "unlock_live", "place_live_order", "change_hard_risk_limits",
        "modify_credentials", "modify_execution_gateway",
    }
    missing = required - set(RESTRICTED_TOOLS)
    assert not missing, f"RESTRICTED_TOOLS missing: {missing}"


def test_risk_engine_eval_lock_exists():
    """_eval_lock threading.Lock exists in risk/engine (TOCTOU prevention)."""
    import threading
    import aletheia.risk.engine as eng

    assert hasattr(eng, "_eval_lock")
    assert isinstance(eng._eval_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# Data retention invariants
# ---------------------------------------------------------------------------


def test_causal_tables_not_in_prunable_whitelist():
    """Trust-bearing tables must NEVER appear in _PRUNABLE whitelist."""
    from aletheia.storage.repositories import _PRUNABLE

    causal = {
        "trade_proposals", "risk_decisions", "orders", "fills",
        "positions", "audit_events", "knowledge_items",
    }
    violations = causal & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: causal tables in _PRUNABLE: {violations}"
    )


def test_audit_chain_12_tables_not_in_prunable_whitelist():
    """AGENTS.md hard rule: the full audit-chain 12 must NEVER be prunable.

    Phase 3 retention pass added many new prune functions; this assertion
    guards the whitelist against accidentally including a trust-bearing table
    in a follow-up refactor.
    """
    from aletheia.storage.repositories import _PRUNABLE

    audit_chain_12 = {
        "audit_events",
        "fills",
        "capital_events",
        "orders",
        "positions",
        "proposals",
        "risk_decisions",
        "approvals",
        "knowledge_items",
        "trade_proposals",
        "ps_orders",
        "ps_fills",
        "ps_positions",
    }
    violations = audit_chain_12 & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: audit-chain-12 tables in _PRUNABLE: {violations}"
    )


def test_learning_9_tables_not_in_prunable_whitelist():
    """AGENTS.md hard rule: the 9 learning tables must NEVER be prunable.

    They retain every nomination, hypothesis, experiment, exposure, contribution
    verdict, fixed-look decision, lifecycle event, job-health record, and policy
    epoch. Losing rows breaks causal replay and generation-cost accounting.
    """
    from aletheia.storage.repositories import _PRUNABLE

    learning_tables = {
        "learning_experiments",
        "learning_exposures",
        "learning_hypotheses",
        "learning_hypothesis_nominations",
        "learning_policy_epochs",
        "learning_contribution_verdicts",
        "learning_inference_decisions",
        "learning_lifecycle_events",
        "learning_job_health",
    }
    violations = learning_tables & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: learning tables in _PRUNABLE: {violations}"
    )


def test_migration_schema_is_additive_only():
    """SCHEMA_SQL must not contain DROP TABLE / DROP COLUMN / RENAME TABLE."""
    from aletheia.storage.migrations import SCHEMA_SQL

    sql_upper = SCHEMA_SQL.upper()
    assert "DROP TABLE" not in sql_upper, "MIGRATION VIOLATION: DROP TABLE"
    assert "DROP COLUMN" not in sql_upper, "MIGRATION VIOLATION: DROP COLUMN"
    assert "RENAME TABLE" not in sql_upper, "MIGRATION VIOLATION: RENAME TABLE"


# ---------------------------------------------------------------------------
# Safety defaults
# ---------------------------------------------------------------------------


def test_live_config_default_is_disabled():
    """LiveConfig.state default must be 'live_disabled'."""
    from aletheia.config.schema import LiveConfig

    source = inspect.getsource(LiveConfig)
    assert "live_disabled" in source, (
        "CONTRACT VIOLATION: LiveConfig default state is not live_disabled"
    )


def test_kill_switch_module_has_required_interface():
    """kill_switch.py exposes is_active/activate/deactivate."""
    from aletheia.risk import kill_switch

    assert callable(getattr(kill_switch, "is_active", None))
    assert callable(getattr(kill_switch, "activate", None))
    assert callable(getattr(kill_switch, "deactivate", None))


# ---------------------------------------------------------------------------
# Trust-boundary coverage registry
#
# See docs/operations/development/TRUST_BOUNDARY_COVERAGE.md and
# scripts/coverage_floors.py. If a new module is created on one of the
# canonical trust-boundary paths (Profit Gradient, epoch/cutoff, artifact
# compiler, replay, cost reconciliation) it MUST be registered in
# .coveragerc's include= list, otherwise a PR could grow silently
# unmeasured code on a path whose wrong answer misleads a decision,
# corrupts state, or bypasses a check.
#
# Registration is the contract; the floor in coverage_floors.py can be
# added or raised in a follow-up.
# ---------------------------------------------------------------------------


def test_trust_boundary_files_are_registered_in_coveragerc():
    """Every file on a canonical trust-boundary path must be listed in
    ``.coveragerc``'s ``include=`` block. Missing the registration means
    a silent hole in the coverage gate.
    """
    import fnmatch
    import re

    repo_root = Path(__file__).resolve().parent.parent
    coveragerc = (repo_root / ".coveragerc").read_text()

    # Parse the ``include =`` block. coverage.py accepts either
    # ``aletheia/...`` or ``*/aletheia/...``; we accept both by stripping
    # a leading ``*/``.
    run_section = re.search(r"\[run\](.*?)(?:\n\[|\Z)", coveragerc, re.S)
    assert run_section is not None, ".coveragerc is missing a [run] section"
    include_match = re.search(
        r"^include\s*=\s*\n((?:[ \t]+\S.*\n?)+)",
        run_section.group(1),
        re.M,
    )
    assert include_match is not None, ".coveragerc [run] has no include= block"
    registered = {
        line.strip().removeprefix("*/")
        for line in include_match.group(1).splitlines()
        if line.strip()
    }

    # Canonical trust-boundary path patterns. Any *.py file inside
    # aletheia/ whose repo-relative path matches one of these globs is a
    # trust boundary and must be registered. Patterns are anchored on
    # ``aletheia/...``.
    patterns = (
        "core/aletheia/analytics/profit_gradient*.py",
        "core/aletheia/execution/reconciliation.py",
        "core/aletheia/economics/context_artifacts.py",
        "core/aletheia/learning/*epoch*.py",
        "core/aletheia/learning/policy_epochs.py",
        # Replay lives under two subpackages today; both count.
        "core/aletheia/analytics/*replay*.py",
        "core/aletheia/counterfactual/*replay*.py",
    )

    aletheia_root = repo_root / "core" / "aletheia"
    unregistered: list[str] = []
    for path in aletheia_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(repo_root).as_posix()
        if any(fnmatch.fnmatchcase(rel, pat) for pat in patterns):
            if rel not in registered:
                unregistered.append(rel)

    assert not unregistered, (
        "TRUST-BOUNDARY REGISTRATION MISSING:\n  "
        + "\n  ".join(unregistered)
        + "\n\nAdd each path to .coveragerc's include= block and set a floor "
        "in scripts/coverage_floors.py. See "
        "docs/operations/development/TRUST_BOUNDARY_COVERAGE.md."
    )


def test_coverage_floors_match_coveragerc_registry():
    """Every include= entry in .coveragerc must have a floor, and every
    floor must correspond to a registered path. Prevents drift between
    what's measured and what's gated.
    """
    import re
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    coveragerc = (repo_root / ".coveragerc").read_text()
    run_section = re.search(r"\[run\](.*?)(?:\n\[|\Z)", coveragerc, re.S)
    include_match = re.search(
        r"^include\s*=\s*\n((?:[ \t]+\S.*\n?)+)",
        run_section.group(1),
        re.M,
    )
    registered = {
        line.strip().removeprefix("*/")
        for line in include_match.group(1).splitlines()
        if line.strip()
    }

    sys.path.insert(0, str(repo_root / "scripts"))
    try:
        import coverage_floors  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    floors = set(coverage_floors.FLOORS.keys())
    missing_floor = registered - floors
    orphan_floor = floors - registered
    assert not missing_floor and not orphan_floor, (
        f"coverage registry drift: "
        f"registered-but-no-floor={sorted(missing_floor)}, "
        f"floor-but-not-registered={sorted(orphan_floor)}"
    )
