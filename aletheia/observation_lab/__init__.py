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
