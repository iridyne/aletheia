"""Generic CCXT Exchange Extension composition and account factory."""

from __future__ import annotations

from importlib.metadata import version
import time

from aletheia.config.loader import load_config
from aletheia.config.secrets import get_credential_generation, get_secret
from aletheia.portfolio.disappearance_confirmation import logical_account_fingerprint
from aletheia.uta.manager import UTA_EXECUTION_MODES
from aletheia.uta.models import UTAAccountIdentity
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry
from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter


_EXCHANGE_ENDPOINTS: dict[str, str] = {
    "okx": "https://www.okx.com/api/v5/public/time",
    "bitget": "https://api.bitget.com/api/v2/public/time",
    "binance": "https://api.binance.com/api/v3/ping",
}


async def probe_public_connectivity(exchange_name: str) -> dict:
    """Bounded public venue probe; no credentials, account state or lifecycle."""
    url = _EXCHANGE_ENDPOINTS.get(exchange_name)
    if not url:
        return {
            "reachable": False, "latency_ms": None,
            "error": "exchange_probe_unavailable", "exchange": exchange_name,
        }

    from aletheia.net import httpx_client
    t0 = time.monotonic()
    async with httpx_client(timeout=8) as client:
        r = await client.get(url)
        latency = (time.monotonic() - t0) * 1000
        reachable = r.status_code == 200
        return {
            "reachable": reachable,
            "latency_ms": round(latency, 1),
            "error": None if reachable else f"HTTP {r.status_code}",
            "exchange": exchange_name,
        }


def _profile_spec(cfg, execution_mode: str):
    if execution_mode == "binance_testnet":
        from extensions.exchanges.binance.profile import BinanceTestnetProfile
        return (
            "binance.testnet",
            "binance_testnet",
            "binance",
            "testnet",
            ("api_key", "api_secret"),
            BinanceTestnetProfile,
        )
    if execution_mode == "okx_demo":
        from extensions.exchanges.okx.profile import OKXDemoProfile
        return (
            "okx.demo",
            "okx_demo",
            "okx",
            "demo",
            ("api_key", "api_secret", "passphrase"),
            OKXDemoProfile,
        )
    if execution_mode == "binance_live_limited":
        from extensions.exchanges.binance.profile import BinanceLiveProfile
        return (
            "binance.live",
            "binance_live",
            "binance",
            "live",
            ("api_key", "api_secret"),
            BinanceLiveProfile,
        )
    if execution_mode == "okx_live_limited":
        from extensions.exchanges.okx.profile import OKXLiveProfile
        return (
            "okx.live",
            "okx_live",
            "okx",
            "live",
            ("api_key", "api_secret", "passphrase"),
            OKXLiveProfile,
        )
    if execution_mode == "bitget_demo":
        from extensions.exchanges.bitget.profile import BitgetClassicV2Profile, BitgetUTAV3Profile
        profile = cfg.exchange.bitget_profile
        profile_type = BitgetUTAV3Profile if profile == "uta_v3" else BitgetClassicV2Profile
        return (
            "bitget.demo",
            profile,
            "bitget",
            "demo",
            ("api_key", "api_secret", "passphrase"),
            profile_type,
        )
    if execution_mode == "bitget_live_limited":
        from extensions.exchanges.bitget.profile import BitgetClassicV2LiveProfile, BitgetUTAV3LiveProfile
        profile = cfg.exchange.bitget_profile
        profile_type = (
            BitgetUTAV3LiveProfile
            if profile == "uta_v3"
            else BitgetClassicV2LiveProfile
        )
        return (
            "bitget.live",
            profile,
            "bitget",
            "live",
            ("api_key", "api_secret", "passphrase"),
            profile_type,
        )
    raise KeyError(execution_mode)


def cached_market_restrictions(execution_mode: str) -> dict | None:
    if execution_mode != "okx_demo":
        return None
    from extensions.exchanges.okx.market_capabilities import _get_demo_symbols

    return {
        "usdt_futures": _get_demo_symbols("usdt_futures"),
        "spot": _get_demo_symbols("spot"),
    }


async def fetch_market_restrictions(execution_mode: str) -> dict | None:
    if execution_mode != "okx_demo":
        return None
    from extensions.exchanges.ccxt.market_capabilities import load_public_market_capabilities

    result = await load_public_market_capabilities("okx", sandbox=True)
    return {
        "usdt_futures": result.get("swap", frozenset()),
        "spot": result.get("spot", frozenset()),
    }


def funding_history_retention_contract(
    execution_mode: str,
    *,
    cfg=None,
) -> tuple[int, str]:
    """Return the active profile's credential-free funding retention contract."""
    cfg = cfg or load_config()
    profile_type = _profile_spec(cfg, execution_mode)[-1]
    return (
        int(profile_type.private_history_retention_days),
        str(profile_type.profile_version),
    )


def _account_generation(cfg, execution_mode: str, scope: str, profile: str) -> int:
    secret_generation = get_credential_generation(scope)
    config_generation = cfg.exchange.uta_account_config_generation_for(execution_mode)
    profile_generation = {"classic_v2": 1, "uta_v3": 2}.get(profile, 0)
    secret_and_config = (
        (secret_generation + config_generation)
        * (secret_generation + config_generation + 1)
        // 2
        + config_generation
    )
    return (
        (secret_and_config + profile_generation)
        * (secret_and_config + profile_generation + 1)
        // 2
        + profile_generation
    )


def credential_configuration(cfg, execution_mode: str) -> tuple[int, int]:
    """Inspect only one configured account scope without creating a client."""
    scope, _profile, _venue, _environment, fields, _profile_type = _profile_spec(cfg, execution_mode)
    return sum(bool(get_secret(f"{scope}.{field}")) for field in fields), len(fields)


def resolve_uta_identity(cfg=None, execution_mode: str = "") -> UTAAccountIdentity:
    """Resolve account identity metadata without requiring complete credentials."""
    cfg = cfg or load_config()
    scope, profile_name, venue, environment, _fields, _profile_type = _profile_spec(
        cfg, execution_mode
    )
    return UTAAccountIdentity(
        account_id=execution_mode,
        execution_mode=execution_mode,
        venue=venue,
        environment=environment,
        profile=profile_name,
        account_fingerprint=logical_account_fingerprint(
            broker_id=execution_mode,
            account_environment=environment,
            execution_mode=execution_mode,
        ),
        credential_generation=_account_generation(
            cfg, execution_mode, scope, profile_name
        ),
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version=version("ccxt"),
    )


def build_uta_registry(
    cfg=None, *, execution_modes: tuple[str, ...] = UTA_EXECUTION_MODES,
) -> UTARegistry:
    """Resolve only requested account scopes; no client/session is created."""
    cfg = cfg or load_config()
    registry = UTARegistry()
    for execution_mode in UTA_EXECUTION_MODES:
        if execution_mode not in execution_modes:
            continue
        scope, profile_name, venue, environment, fields, profile_type = _profile_spec(
            cfg, execution_mode
        )
        credentials = {field: get_secret(f"{scope}.{field}") for field in fields}
        if not all(credentials.values()):
            continue
        generation = _account_generation(cfg, execution_mode, scope, profile_name)
        identity = UTAAccountIdentity(
            account_id=execution_mode,
            execution_mode=execution_mode,
            venue=venue,
            environment=environment,
            profile=profile_name,
            account_fingerprint=logical_account_fingerprint(
                broker_id=execution_mode,
                account_environment=environment,
                execution_mode=execution_mode,
            ),
            credential_generation=generation,
            adapter_implementation="CCXTUTAAdapter",
            adapter_version="1",
            ccxt_version=version("ccxt"),
        )
        config = UTAAdapterConfig(identity=identity)

        def factory(adapter_config, profile_type=profile_type, credentials=credentials):
            profile_kwargs = {
                "api_key": credentials["api_key"],
                "secret": credentials["api_secret"],
            }
            if "passphrase" in credentials:
                profile_kwargs["passphrase"] = credentials["passphrase"]
            return CCXTUTAAdapter(
                adapter_config.identity,
                profile_type(**profile_kwargs),
            )

        registry.register(config, factory)
    return registry
