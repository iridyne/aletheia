"""Committee stays advisory; unconfirmed skip does not freeze the funnel for 2h."""

from __future__ import annotations

from aletheia.agent_shell.skip_policy import (
    NEUTRAL_SKIP_DEFER_REASON,
    STALE_SNAPSHOT_DEFER_REASON,
    defer_window_minutes_for_reason,
    emit_suppression_applies_to_defer,
    skip_policy_context,
)




def test_b19_skip_policy_does_not_life_support_stale_or_shrug():
    from aletheia.agent_shell.skip_policy import defer_extends_carrier_valid_until

    values = skip_policy_context()
    assert values["version"] == "b1.9"
    assert values["direct_skip_confirmation_required"] is True
    assert values["technical_or_shrug_defer_extends_valid_until"] is False
    assert defer_window_minutes_for_reason(NEUTRAL_SKIP_DEFER_REASON, 120) == 30
    assert defer_window_minutes_for_reason(STALE_SNAPSHOT_DEFER_REASON, 120) == 20
    assert defer_extends_carrier_valid_until(STALE_SNAPSHOT_DEFER_REASON) is False
    assert defer_extends_carrier_valid_until(NEUTRAL_SKIP_DEFER_REASON) is False
    assert defer_extends_carrier_valid_until("operator_low_conviction") is True
    assert defer_extends_carrier_valid_until(
        "direct_skip_not_confirmed:unverified_adverse_reason:regime_mismatch"
    ) is True




def test_shrug_defer_does_not_suppress_a_fresh_emit():
    assert emit_suppression_applies_to_defer(None) is False
    assert emit_suppression_applies_to_defer(
        {"defer_reason": NEUTRAL_SKIP_DEFER_REASON, "material_fingerprint": "abc"}
    ) is False
    assert emit_suppression_applies_to_defer(
        {
            "defer_reason": "direct_skip_not_confirmed:unverified_adverse_reason:regime_mismatch",
            "material_fingerprint": "abc",
        }
    ) is True
