from aletheia.server.startup_policy import startup_policy_snapshot


def test_startup_policy_exposes_fail_warn_and_pause_boundaries() -> None:
    policy = {item["stage"]: item for item in startup_policy_snapshot()}

    assert policy["database_migration_verification"]["on_failure"] == "fail_startup"
    assert policy["scheduler_initialization"]["on_failure"] == "warn_continue"
    assert policy["startup_behavior_gate"] == {
        "stage": "startup_behavior_gate",
        "on_failure": "pause_autonomy",
        "readiness_effect": "operation_blocked",
    }
