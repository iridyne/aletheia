"""Isolated allocator replay for non-authoritative Universe Twin evidence."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Any

from aletheia.config.schema import UniverseConfig
from aletheia.universe.allocator import allocate_universe
from aletheia.universe.filters import apply_fine_tradability_filters
from aletheia.universe.twin_scoring import (
    TwinShadowInput,
    TwinShadowRecord,
    annotate_shadow_selection,
    build_shadow_allocation_candidates,
)


TWIN_ALLOCATION_REPLAY_CONTRACT = "universe-twin-allocation-diagnostic-v1"


def _allocate_arm(
    candidates: list[dict[str, object]],
    *,
    universe_config: UniverseConfig,
    app_config: Any,
    family_tolerances: dict[str, float],
    regime_state: str | None,
    allocator_random_state: object,
    dedupe: Callable[[list[dict], Any], list[dict]],
) -> list[dict]:
    fine_filtered = apply_fine_tradability_filters(
        candidates,
        universe_config,
        family_tolerances=family_tolerances,
        scanner_run_id=None,
    )
    unique = dedupe(fine_filtered, app_config)
    replay_rng = random.Random()
    replay_rng.setstate(allocator_random_state)
    return allocate_universe(
        unique,
        universe_config,
        regime_state=regime_state,
        explore_sampler=replay_rng.sample,
        decorate_selected=False,
        log_result=False,
    )


def replay_twin_shadow_allocations(
    records: Sequence[TwinShadowRecord],
    inputs: Sequence[TwinShadowInput],
    *,
    scanner_run_id: str,
    universe_config: UniverseConfig,
    app_config: Any,
    family_tolerances: dict[str, float] | None,
    regime_state: str | None,
    allocator_random_state: object,
    dedupe: Callable[[list[dict], Any], list[dict]],
) -> tuple[TwinShadowRecord, ...]:
    """Replay both continuous arms from a best-effort pre-authority RNG snapshot.

    The replay copies candidates, suppresses filter evidence writes, skips
    execution-eligibility decoration, and never returns data to the authority
    allocation collection. The process-global authority draw is not isolated,
    so this is diagnostic parity rather than verified random-assignment lineage.
    """

    point_candidates = build_shadow_allocation_candidates(
        records,
        inputs,
        scanner_run_id=scanner_run_id,
        score_kind="point",
    )
    pessimistic_candidates = build_shadow_allocation_candidates(
        records,
        inputs,
        scanner_run_id=scanner_run_id,
        score_kind="pessimistic",
    )
    kwargs = {
        "universe_config": universe_config,
        "app_config": app_config,
        "family_tolerances": dict(family_tolerances or {}),
        "regime_state": regime_state,
        "allocator_random_state": allocator_random_state,
        "dedupe": dedupe,
    }
    point_allocated = _allocate_arm(point_candidates, **kwargs)
    pessimistic_allocated = _allocate_arm(pessimistic_candidates, **kwargs)
    return annotate_shadow_selection(
        records,
        point_allocated=point_allocated,
        pessimistic_allocated=pessimistic_allocated,
    )


__all__ = [
    "TWIN_ALLOCATION_REPLAY_CONTRACT",
    "replay_twin_shadow_allocations",
]
