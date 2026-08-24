"""G1 tests: current UFC policy-epoch resolution (#1072 v1 design §5)."""

from __future__ import annotations

from unittest.mock import patch

_EXP_ROW = {
    "experiment_id": "exp-x",
    "hypothesis_id": "hyp-x",
    "stage": "shadow",
    "stage_generation": 2,
}


def test_resolve_policy_epoch_prefers_current_over_historical_verdict():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value={"policy_epoch": "verdict-stamped-epoch"},
    ) as verdict_lookup, patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="live-computed-epoch",
    ) as current_epoch:
        assert _resolve_policy_epoch(_EXP_ROW) == "live-computed-epoch"
    verdict_lookup.assert_not_called()
    current_epoch.assert_called_once_with(
        "universe_family_calibration", persist_config_migrations=False
    )


def test_resolve_policy_epoch_does_not_need_a_verdict():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=None,
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="live-computed-epoch",
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "live-computed-epoch"


def test_resolve_policy_epoch_never_falls_back_to_historical_verdict():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value={"policy_epoch": "historical-epoch"},
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value=None,
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "unknown"


def test_resolve_policy_epoch_sentinel_when_current_source_fails():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.experiments.current_policy_epoch",
        side_effect=RuntimeError("config unavailable"),
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "unknown"


def test_observation_id_uses_resolved_epoch_and_stage_generation():
    from aletheia.observation_lab.ufc_shadow import _observation_id

    with patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="resolved-epoch",
    ):
        obs_id = _observation_id(_EXP_ROW)
    assert obs_id == "ufc:exp-x:resolved-epoch:2"
