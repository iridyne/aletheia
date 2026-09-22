"""Observation Lab v1 (Issue #1072).

An observe-only, no-execution-authority Evidence Sidecar. Its state root is
``~/.aletheia/observation-lab/lab.db`` — separate from ``app.db`` so unrelated
Production schema changes cannot retroactively contaminate a collecting
cohort's frozen first-exposure snapshots.

See ``docs/design/contracts/observation_lab_contract.md``.
"""

from extensions.observation_lab.db import (  # noqa: F401
    close_lab_db,
    get_lab_db,
    lab_db_path,
    reset_lab_db,
)
from extensions.observation_lab.units import (  # noqa: F401
    count_frozen_units,
    insert_frozen_units,
    list_frozen_units,
)
from extensions.observation_lab.ufc_shadow import (  # noqa: F401
    UFC_PATH_ID,
    latest_equivalence_record,
)
from extensions.observation_lab.runner import (  # noqa: F401
    LabWindow,
    clear_windows,
    register_window,
    registered_windows,
    run_lab_windows,
)
from extensions.observation_lab.evaluator import (  # noqa: F401
    evaluate_active_observations,
    evaluate_observation,
)
from extensions.observation_lab.manifest import (  # noqa: F401
    ExecutionManifest,
    ManifestGenerationConflict,
    ManifestResolution,
    build_manifest,
    load_active_manifests,
    publish_manifest,
    resolve_treatment,
)
from extensions.observation_lab.checkpoint import (  # noqa: F401
    CheckpointVerification,
    JointCheckpoint,
    compute_joint_checkpoint,
    latest_joint_checkpoint,
    save_joint_checkpoint,
    verify_joint_checkpoint,
)
from extensions.observation_lab.execution_owner import (  # noqa: F401
    ObservationOwnerError,
    get_execution_owner,
    is_main_owner,
    is_worker_owner,
    require_main_or_absent,
    require_worker_owner,
)
from extensions.observation_lab.worker_lock import (  # noqa: F401
    WorkerCycleLockError,
    worker_pass_lock,
)
from extensions.observation_lab.conflict import (  # noqa: F401
    ConflictResolution,
    resolve_manifest_conflict,
)
from extensions.observation_lab.exposure import (  # noqa: F401
    LabExposureFact,
    fact_from_conflict,
    list_lab_exposures,
    record_lab_exposure,
)
from extensions.observation_lab.canonical import (  # noqa: F401
    AuthoritySource,
    NumericTreatmentAdapter,
    ObservationTreatmentDecision,
    TreatmentAdapter,
    append_delivery_receipt,
    apply_evaluation_safety_disposition,
    append_lifecycle_event,
    build_authority_cutover_proof,
    compact_finalized_generations,
    consume_inference_plan,
    enqueue_lab_audit,
    freeze_inference_plan,
    get_authority_source,
    import_delivery_receipts,
    latest_observation_result,
    observation_bridge_health,
    prune_observation_delivery_outbox,
    publish_manifest_with_lifecycle,
    record_archive,
    record_authority_cutover_proof,
    record_generation_contract,
    record_observation_evaluation,
    record_observation_nomination,
    record_observation_result,
    register_observation_spec,
    record_owner_action,
    register_inference_plan,
    register_treatment_adapter,
    registered_treatment_adapters,
    release_retention_claim,
    revoke_manifest,
    resolve_observation_treatment,
    set_authority_source,
    upsert_retention_claim,
    verify_generation_archive,
    write_generation_archive,
)
from extensions.observation_lab.migration import (  # noqa: F401
    apply_legacy_learning_migration,
    plan_legacy_learning_migration,
)
