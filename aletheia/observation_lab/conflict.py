"""Manifest-set conflict resolution (Issue #1072 Cluster 4 / §"Treatment-bearing overlap").

New #1072 §"Treatment-bearing overlap" (verbatim):

> Multiple treatment-bearing windows may be active at the same time only when
> one of these is preregistered and executable:
> - their eligible populations are disjoint;
> - they affect distinct behavior dimensions under an explicit composability
>   contract;
> - they use an explicit factorial/interference design that owns the combined
>   assignment and estimand.
>
> Otherwise, the default rule is:
> **At most one authority-bearing treatment manifest may affect the same
> population and behavior dimension at one time.**
>
> Main Runtime must inspect the complete active manifest set for an eligible
> unit. It must never resolve an undeclared conflict through arbitrary
> priority, registration order or last-write-wins. An undeclared conflict
> fails closed to the specified baseline/unavailable behavior and persists
> a typed conflict exposure fact for every affected window.

This module is that inspector, as a pure function. The caller is the
Main Runtime seam (post-Packet-2); the function is Lab-side because Lab
is where manifest identity lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aletheia.observation_lab.manifest import ExecutionManifest


# Composability-contract enum values that survive an audit — MUST match the
# CHECK constraint in ``execution_manifests.composability_contract`` and the
# validator in ``manifest.build_manifest``. Encoded here rather than imported
# to prevent circular imports and to make the conflict-rule surface explicit.
_EXCLUSIVE = "exclusive"
_DISJOINT_POPULATION = "disjoint_population"
_FACTORIAL = "factorial"


@dataclass(frozen=True)
class ConflictResolution:
    """Output of ``resolve_manifest_conflict`` — bounded, typed."""

    outcome: str
    # 'no_manifest' — nothing active; caller applies baseline.
    # 'single_manifest' — exactly one; caller applies its resolver.
    # 'disjoint_populations_ok' — >1 all disjoint; caller applies its own eligible one.
    # 'factorial_ok' — >1 all factorial; caller consults factorial mapping.
    # 'undeclared_conflict:exclusive' — ≥2 with any exclusive claim; fail closed.
    # 'undeclared_conflict:mixed_composability' — heterogeneous contracts; fail closed.
    # 'undeclared_conflict:overlapping_population' — disjoint claimed but populations overlap.

    admitted: tuple[ExecutionManifest, ...] = field(default_factory=tuple)
    # Manifests the caller MAY apply (post-conflict-check). Empty on fail-closed
    # or no-manifest outcomes. For 'single_manifest', exactly one. For disjoint
    # or factorial outcomes, the whole admitted set.

    rejected: tuple[ExecutionManifest, ...] = field(default_factory=tuple)
    # Manifests present but rejected by the conflict rule. Non-empty only on
    # fail-closed outcomes — the caller MUST persist an exposure fact for each,
    # naming the conflict result.

    reason: str = ""
    # Human-readable rationale; short enough to fit in ``lab_exposures.reason``.


def resolve_manifest_conflict(
    active_manifests: list[ExecutionManifest] | tuple[ExecutionManifest, ...],
) -> ConflictResolution:
    """Decide whether a set of active manifests may coexist on one unit.

    ``active_manifests`` are the manifests already filtered by interference
    key + effective-window (see ``manifest.load_active_manifests``). This
    function does NOT re-filter by time; the caller must pass only manifests
    it already accepted as active at the target ``now``.

    Rules:
    - 0 manifests → ``no_manifest``. Baseline behavior; nothing to record.
    - 1 manifest → ``single_manifest``. Trivial admission.
    - ≥2 manifests:
        - Any manifest claims ``exclusive`` → ``undeclared_conflict:exclusive``.
        - Heterogeneous composability (mix of disjoint + factorial +
          exclusive) → ``undeclared_conflict:mixed_composability``.
        - All ``disjoint_population`` AND ``eligible_population["population_key"]``
          values are pairwise distinct → ``disjoint_populations_ok``.
        - All ``disjoint_population`` but ``population_key`` collides or is
          missing → ``undeclared_conflict:overlapping_population``.
        - All ``factorial`` → ``factorial_ok`` (caller applies factorial
          combinator per its own contract).
    """
    if not active_manifests:
        return ConflictResolution(outcome="no_manifest", reason="no_active_manifest")

    if len(active_manifests) == 1:
        return ConflictResolution(
            outcome="single_manifest",
            admitted=tuple(active_manifests),
            reason=f"single_manifest:{active_manifests[0].manifest_id[:12]}",
        )

    # ≥2 manifests — inspect the whole set.
    contracts = {m.composability_contract for m in active_manifests}

    if _EXCLUSIVE in contracts:
        return ConflictResolution(
            outcome="undeclared_conflict:exclusive",
            rejected=tuple(active_manifests),
            reason=(
                f"exclusive_manifest_conflict:{len(active_manifests)}_active_at_same_"
                f"interference_key"
            ),
        )

    if len(contracts) > 1:
        return ConflictResolution(
            outcome="undeclared_conflict:mixed_composability",
            rejected=tuple(active_manifests),
            reason=f"mixed_composability:{sorted(contracts)}",
        )

    # All manifests share one non-exclusive contract.
    only = next(iter(contracts))
    if only == _DISJOINT_POPULATION:
        population_keys = []
        for m in active_manifests:
            key = m.eligible_population.get("population_key")
            if not key:
                return ConflictResolution(
                    outcome="undeclared_conflict:overlapping_population",
                    rejected=tuple(active_manifests),
                    reason="disjoint_population_without_population_key",
                )
            population_keys.append(key)
        if len(set(population_keys)) != len(population_keys):
            return ConflictResolution(
                outcome="undeclared_conflict:overlapping_population",
                rejected=tuple(active_manifests),
                reason=f"population_key_collision:{sorted(population_keys)}",
            )
        return ConflictResolution(
            outcome="disjoint_populations_ok",
            admitted=tuple(active_manifests),
            reason=f"disjoint_populations:{sorted(population_keys)}",
        )

    if only == _FACTORIAL:
        return ConflictResolution(
            outcome="factorial_ok",
            admitted=tuple(active_manifests),
            reason=f"factorial:{len(active_manifests)}_manifests",
        )

    # Defensive: unknown composability that somehow passed manifest validation.
    return ConflictResolution(
        outcome="undeclared_conflict:mixed_composability",
        rejected=tuple(active_manifests),
        reason=f"unknown_composability:{sorted(contracts)}",
    )
