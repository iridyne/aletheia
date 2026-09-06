from pathlib import Path

import pytest

from aletheia.observation_lab.experiment_owner import (
    ExperimentMutationAuthorityError,
    require_experiment_mutation_owner,
    select_experiment_mutation_owner,
)
from aletheia.storage.sqlite import get_db


def test_missing_and_invalid_experiment_authority_fail_closed(monkeypatch):
    db = get_db()
    db.execute(
        "DELETE FROM observation_authority_state WHERE state_key='experiment_authority'"
    )
    with pytest.raises(
        ExperimentMutationAuthorityError,
        match="experiment_mutation_authority_unavailable",
    ):
        select_experiment_mutation_owner()

    from aletheia.observation_lab import canonical

    monkeypatch.setattr(
        canonical,
        "get_authority_source",
        lambda **_kwargs: canonical.AuthoritySource(
            "invalid", "invalid:epoch", "test", "test", "now"
        ),
    )
    with pytest.raises(
        ExperimentMutationAuthorityError,
        match="experiment_mutation_authority_invalid",
    ):
        select_experiment_mutation_owner()


def test_epoch_change_revokes_previously_selected_owner():
    owner = select_experiment_mutation_owner()
    assert owner.source == "legacy"
    get_db().execute(
        "UPDATE observation_authority_state SET authority_epoch=? "
        "WHERE state_key='experiment_authority'",
        ("legacy:replacement",),
    )

    with pytest.raises(
        ExperimentMutationAuthorityError,
        match="experiment_mutation_authority_changed",
    ):
        require_experiment_mutation_owner("legacy", owner.epoch, db=get_db())


def test_production_experiment_mutations_route_through_selected_owner():
    root = Path(__file__).parents[1] / "aletheia"
    sources = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    }
    owner = "observation_lab/experiment_owner.py"
    repository = "storage/repositories.py"
    canonical = "observation_lab/canonical.py"

    for symbol, allowed in {
        "_record_owner_action(": {owner, canonical},
        "_insert_filter_shadow_experiment(": {owner, repository},
        "_update_filter_shadow_experiment(": {owner, repository},
    }.items():
        assert {
            name for name, source in sources.items() if symbol in source
        } <= allowed

    assert "run_lifecycle_controller" not in sources["agent_runtime/scheduler.py"]
    assert "_execute_trial_admission" not in sources["server/routes/settings.py"]
    assert "record_owner_action" not in sources["server/routes/settings.py"]
