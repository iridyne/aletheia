"""Fresh configuration cannot authorize live trading."""

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_live_readiness"


@spec_claim(
    SpecClaim(
        "LR-S06", _CONTRACT, "§config — LiveConfig default is live_disabled, autonomous unlock impossible", "L2a",
        "LiveConfig.state defaults to 'live_disabled' and first_live_confirmation_done "
        "defaults to False — a fresh install or a re-init can NEVER autonomously "
        "enable live trading. Phase 5 does not change this; the structural live-prep "
        "is paper-only by construction",
    ),
)
def test_live_config_default_is_disabled():
    from aletheia.config.schema import LiveConfig

    cfg = LiveConfig()
    assert cfg.state == "live_disabled", (
        f"LiveConfig.state default is not 'live_disabled' (got {cfg.state!r}) — "
        f"a fresh config or reset would put the system into a live-eligible state "
        f"without explicit operator action (CLAUDE.md safety rule violation)"
    )
    assert cfg.first_live_confirmation_done is False, (
        f"LiveConfig.first_live_confirmation_done default is not False (got "
        f"{cfg.first_live_confirmation_done!r}) — the first-time acknowledgement "
        f"gate is structurally bypassed, the live unlock route's "
        f"acknowledge_first_unlock=true requirement does not apply"
    )
