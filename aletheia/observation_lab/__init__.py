"""Observation Lab v1 (Issue #1072).

An observe-only, no-execution-authority Evidence Sidecar. Its state root is
``~/.aletheia/observation-lab/lab.db`` — separate from ``app.db`` so unrelated
Production schema changes cannot retroactively contaminate a collecting
cohort's frozen first-exposure snapshots.

See ``docs/design/contracts/observation_lab_contract.md``.
"""

from aletheia.observation_lab.db import (  # noqa: F401
    close_lab_db,
    get_lab_db,
    lab_db_path,
    reset_lab_db,
)
from aletheia.observation_lab.units import (  # noqa: F401
    count_frozen_units,
    insert_frozen_units,
    list_frozen_units,
)
from aletheia.observation_lab.ufc_shadow import (  # noqa: F401
    UFC_PATH_ID,
    latest_equivalence_record,
    run_ufc_shadow_equivalence_pass,
)
from aletheia.observation_lab.runner import (  # noqa: F401
    LabWindow,
    clear_windows,
    register_window,
    registered_windows,
    run_lab_windows,
)
from aletheia.observation_lab.manifest import (  # noqa: F401
    ExecutionManifest,
    ManifestGenerationConflict,
    ManifestResolution,
    build_manifest,
    load_active_manifests,
    publish_manifest,
    resolve_treatment,
)
from aletheia.observation_lab.checkpoint import (  # noqa: F401
    CheckpointVerification,
    JointCheckpoint,
    compute_joint_checkpoint,
    latest_joint_checkpoint,
    save_joint_checkpoint,
    verify_joint_checkpoint,
)
from aletheia.observation_lab.conflict import (  # noqa: F401
    ConflictResolution,
    resolve_manifest_conflict,
)
from aletheia.observation_lab.exposure import (  # noqa: F401
    LabExposureFact,
    fact_from_conflict,
    list_lab_exposures,
    record_lab_exposure,
)
