"""Executable SDD claims for Opportunity generation/carrier continuity.

The implementation regressions live beside the affected read/write paths.  This
module promotes those behaviors into the Conformance Matrix with stable, unique
claim identities so the contract cannot silently drift behind green unit tests.
"""

from tests import test_defer_reopen_wake as defer_regressions
from tests import test_opportunity_flow_ledger as flow_regressions
from tests import test_opportunity_funnel_integrity as lifecycle_regressions
from tests import test_pi_daemon as daemon_regressions
from tests.specs.spec_meta import SpecClaim, spec_claim


_DL = "decision_lifecycle_contract"
_FE = "filter_evidence_contract"
_API = "api_reference"


def _case_dir(tmp_path, name: str):
    path = tmp_path / name
    path.mkdir()
    return path


@spec_claim(
    SpecClaim(
        "DL-S52",
        _DL,
        "§2.1 generation/carrier separation",
        "L2b",
        "Universe snapshot supersede atomically terminalizes only open analysis "
        "generations; queued Operator carriers survive, and evidence failure rolls "
        "the open lifecycle transition back.",
        status="implemented",
    )
)
def test_snapshot_supersede_preserves_claimed_operator_carriers(tmp_path, monkeypatch):
    lifecycle_regressions.test_superseded_cascade_expires_only_unclaimed_generations_with_atomic_evidence(
        _case_dir(tmp_path, "success"), monkeypatch
    )
    lifecycle_regressions.test_superseded_cascade_rolls_back_status_when_evidence_fails(
        _case_dir(tmp_path, "rollback"), monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S53",
        _DL,
        "§2.3 authoritative queued validity",
        "L2b",
        "A market carrier older than 30 minutes remains visible, counted and claimable "
        "while its Opportunity valid_until is future; PL-PT-09 separately locks the "
        "missing-deadline legacy fallback.",
        status="implemented",
    )
)
def test_queued_carrier_uses_source_valid_until_across_consumers(tmp_path, monkeypatch):
    lifecycle_regressions.test_queued_market_carrier_uses_opportunity_valid_until_not_fixed_30_minutes(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S54",
        _DL,
        "§2.3 active carrier admission identity",
        "L2b",
        "An unresolved symbol+direction+scanner-family carrier owns its setup beyond "
        "the 30-minute signal cooldown and records active_operator_carrier instead of "
        "enqueuing a duplicate generation.",
        status="implemented",
    )
)
def test_active_carrier_remains_distinct_from_signal_cooldown(tmp_path, monkeypatch):
    lifecycle_regressions.test_active_queued_setup_blocks_duplicate_signal_after_cooldown_window(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S55",
        _DL,
        "§2.3 defer authoritatively extends Opportunity lifecycle",
        "L2b",
        "Operator defer is the authoritative signal that a candidate remains worth a "
        "second look at defer_until; the underlying Opportunity's valid_until is "
        "extended forward-only (never shortened) so the promised re-evaluation is "
        "guaranteed inside the Opportunity's lifecycle. Defer never silently loses "
        "its window to a shorter emission-time TTL.",
        status="implemented",
    )
)
def test_operator_defer_extends_carrier_lifecycle(tmp_path, monkeypatch):
    lifecycle_regressions.test_defer_extends_opportunity_valid_until_forward_only(
        _case_dir(tmp_path, "extend"), monkeypatch
    )
    lifecycle_regressions.test_defer_valid_until_extension_is_monotonic_forward_only(
        _case_dir(tmp_path, "monotonic"), monkeypatch
    )
    lifecycle_regressions.test_opportunity_funnel_explains_current_operator_defer(
        _case_dir(tmp_path, "read"), monkeypatch
    )


@spec_claim(
    SpecClaim(
        "DL-S74",
        _DL,
        "§2.3 durable defer deadline wake",
        "L2b",
        "A still-owed defer remains discoverable across process restart and wakes the "
        "PI daemon at defer_until independently of heartbeat phase; terminal, expired "
        "or newly observed carriers do not re-arm.",
        status="implemented",
    )
)
async def test_deferred_operator_promise_has_durable_deadline_wake(
    tmp_path, monkeypatch
):
    cases = defer_regressions.TestDeferredDeadlineRecovery()
    cases.test_due_defer_is_returned_until_a_new_operator_observation(
        _case_dir(tmp_path, "deadline"), monkeypatch
    )
    cases.test_terminal_or_expired_candidate_is_not_rearmed(
        _case_dir(tmp_path, "terminal"), monkeypatch
    )
    await daemon_regressions.test_defer_deadline_wakes_before_heartbeat_without_early_evaluation()


@spec_claim(
    SpecClaim(
        "DL-S56",
        _DL,
        "§2.1 Ranker soft-TTL authority",
        "L2b",
        "Global retention does not expire an open generation merely because valid_until "
        "elapsed; Ranker family soft/hard TTL remains the owner of that analytical exit.",
        status="implemented",
    )
)
def test_global_cleanup_does_not_preempt_ranker_soft_ttl(tmp_path, monkeypatch):
    lifecycle_regressions.test_open_generation_past_valid_until_remains_for_ranker_soft_ttl_window(
        tmp_path, monkeypatch
    )


@spec_claim(
    SpecClaim(
        "FE-S21",
        _FE,
        "§3 typed Opportunity terminal evidence",
        "L2b",
        "Opportunity Trace derives snapshot_superseded and ttl_expired only from typed "
        "append-only facts; a terminal lifecycle event outranks historical signal_pending.",
        status="implemented",
    )
)
def test_trace_uses_typed_terminal_fact_instead_of_raw_expired_status():
    flow_regressions.test_opportunity_flow_does_not_mislabel_snapshot_supersede_as_ttl_expiry()


@spec_claim(
    SpecClaim(
        "API-S09",
        _API,
        "§Analytics Opportunity lifecycle projection",
        "L2b",
        "The one-hour funnel distinguishes generated rows, unique/active setups and "
        "evidence-backed terminal exits, and its current projection exposes the same "
        "truth used by Overview and CLI.",
        status="implemented",
    )
)
def test_funnel_separates_generations_setups_and_terminal_reasons(tmp_path, monkeypatch):
    lifecycle_regressions.test_opportunity_funnel_separates_generations_setups_and_terminal_exits(
        tmp_path, monkeypatch
    )
