"""Concrete exchange account construction; Core receives only UTA factories."""

from __future__ import annotations

from importlib.metadata import version
import logging

from aletheia.config.loader import load_config
from aletheia.config.secrets import get_credential_generation, get_secret
from aletheia.portfolio.disappearance_confirmation import logical_account_fingerprint
from aletheia.uta.ccxt_adapter import CCXTUTAAdapter
from aletheia.uta.manager import UTA_EXECUTION_MODES, configure_uta_provider
from aletheia.uta.models import UTAAccountIdentity
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry


logger = logging.getLogger(__name__)


def _profile_spec(cfg, execution_mode: str):
    if execution_mode == "binance_testnet":
        from aletheia.uta.profiles.binance import BinanceTestnetProfile

        return (
            "binance.testnet",
            "binance_testnet",
            "binance",
            "testnet",
            ("api_key", "api_secret"),
            BinanceTestnetProfile,
        )
    if execution_mode == "okx_demo":
        from aletheia.uta.profiles.okx import OKXDemoProfile

        return (
            "okx.demo",
            "okx_demo",
            "okx",
            "demo",
            ("api_key", "api_secret", "passphrase"),
            OKXDemoProfile,
        )
    if execution_mode == "binance_live_limited":
        from aletheia.uta.profiles.binance import BinanceLiveProfile

        return (
            "binance.live",
            "binance_live",
            "binance",
            "live",
            ("api_key", "api_secret"),
            BinanceLiveProfile,
        )
    if execution_mode == "okx_live_limited":
        from aletheia.uta.profiles.okx import OKXLiveProfile

        return (
            "okx.live",
            "okx_live",
            "okx",
            "live",
            ("api_key", "api_secret", "passphrase"),
            OKXLiveProfile,
        )
    if execution_mode == "bitget_demo":
        from aletheia.uta.profiles.bitget import BitgetClassicV2Profile, BitgetUTAV3Profile

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
        from aletheia.uta.profiles.bitget import BitgetClassicV2LiveProfile, BitgetUTAV3LiveProfile

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


def build_uta_registry(
    cfg=None, *, execution_modes: tuple[str, ...] = UTA_EXECUTION_MODES,
) -> UTARegistry:
    """Resolve only requested account scopes; no client/session is created."""
    cfg = cfg or load_config()
    registry = UTARegistry()
    for execution_mode in UTA_EXECUTION_MODES:
        if execution_mode not in execution_modes:
            continue
        try:
            scope, profile_name, venue, environment, fields, profile_type = _profile_spec(
                cfg, execution_mode
            )
        except ImportError:
            logger.warning("exchange_profile_unavailable execution_mode=%s", execution_mode)
            continue
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


def bind_uta_provider() -> None:
    """Bind factories only; resolve no credentials and create no clients here."""
    configure_uta_provider(
        registry_factory=build_uta_registry,
        retention_contract=funding_history_retention_contract,
    )
