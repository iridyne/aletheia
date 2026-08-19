"""G1 tests: _resolve_policy_epoch sourcing order (#1072 v1 design §5)."""

from __future__ import annotations

from unittest.mock import patch


_EXP_ROW = {
    "experiment_id": "exp-x",
    "hypothesis_id": "hyp-x",
    "stage": "shadow",
    "stage_generation": 2,
}


def test_resolve_policy_epoch_prefers_latest_verdict_stamp():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value={"policy_epoch": "verdict-stamped-epoch"},
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="live-computed-epoch",
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "verdict-stamped-epoch"


def test_resolve_policy_epoch_falls_back_to_current_when_no_verdict():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=None,
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="live-computed-epoch",
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "live-computed-epoch"


def test_resolve_policy_epoch_falls_back_to_current_when_verdict_missing_epoch():
    """A verdict row without a policy_epoch column is treated as no signal."""
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value={"verdict": "positive"},  # no policy_epoch key
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        return_value="live-computed-epoch",
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "live-computed-epoch"


def test_resolve_policy_epoch_sentinel_when_all_sources_fail():
    from aletheia.observation_lab.ufc_shadow import _resolve_policy_epoch

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value=None,
    ), patch(
        "aletheia.learning.experiments.current_policy_epoch",
        side_effect=RuntimeError("config unavailable"),
    ):
        assert _resolve_policy_epoch(_EXP_ROW) == "unknown"


def test_observation_id_uses_resolved_epoch_and_stage_generation():
    from aletheia.observation_lab.ufc_shadow import _observation_id

    with patch(
        "aletheia.learning.ledger.get_latest_measurement_verdict",
        return_value={"policy_epoch": "resolved-epoch"},
    ):
        obs_id = _observation_id(_EXP_ROW)
    assert obs_id == "ufc:exp-x:resolved-epoch:2"
