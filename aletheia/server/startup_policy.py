"""Read-only catalog of the startup failure behavior already enforced by lifespan."""

STARTUP_FAILURE_POLICY = (
    {"stage": "bootstrap", "on_failure": "fail_startup", "readiness_effect": "interface_not_ready"},
    {"stage": "secret_migration", "on_failure": "warn_continue", "readiness_effect": "none"},
    {"stage": "database_migration_verification", "on_failure": "fail_startup", "readiness_effect": "interface_not_ready"},
    {"stage": "runtime_reconciliation", "on_failure": "warn_continue", "readiness_effect": "none"},
    {"stage": "tool_registry_and_job_manager", "on_failure": "fail_startup", "readiness_effect": "interface_not_ready"},
    {"stage": "scheduler_initialization", "on_failure": "warn_continue", "readiness_effect": "operation_degraded"},
    {"stage": "llm_startup_gate", "on_failure": "pause_autonomy", "readiness_effect": "operation_blocked"},
    {"stage": "startup_exchange_sync", "on_failure": "warn_continue", "readiness_effect": "data_warming"},
    {"stage": "exchange_connectivity_gate", "on_failure": "pause_autonomy", "readiness_effect": "operation_blocked"},
    {"stage": "startup_behavior_gate", "on_failure": "pause_autonomy", "readiness_effect": "operation_blocked"},
    {"stage": "pi_daemon", "on_failure": "warn_continue", "readiness_effect": "operation_degraded"},
    {"stage": "telegram", "on_failure": "warn_continue", "readiness_effect": "integration_degraded"},
)


def startup_policy_snapshot() -> list[dict[str, str]]:
    return [dict(item) for item in STARTUP_FAILURE_POLICY]
