"""Lab-owned readers and cycle guard for Main storage."""

import sqlite3
import time
from contextlib import contextmanager
from aletheia.storage.repositories import ExperimentStateReadError, get_universe_discovery_benchmark

def _canonical_lab_authority() -> bool:
    try:
        from aletheia.observation_lab.canonical import get_authority_source

        return get_authority_source().source == "lab"
    except sqlite3.OperationalError as exc:
        if "no such table: observation_authority_state" in str(exc):
            return False
        raise ExperimentStateReadError("experiment authority state unavailable") from exc
    except FileNotFoundError:
        return False
    except RuntimeError as exc:
        raise ExperimentStateReadError("experiment authority state invalid") from exc


def _observation_retention_floor(domain: str) -> str | None:
    try:
        from aletheia.observation_lab.kernel import active_retention_claims

        state = active_retention_claims()
    except Exception as exc:
        raise ExperimentStateReadError(f"Failed to query Observation retention: {exc}") from exc
    if not state["available"]:
        raise ExperimentStateReadError("Observation retention claims unavailable")
    return state["claims"].get(domain)


def _active_observation_claim_refs(domain: str) -> tuple[str, ...]:
    """Return exact protected source identities for Lab-authoritative cleanup."""
    from aletheia.observation_lab.db import readonly_lab_db
    from aletheia.observation_lab.canonical import _validate_retention_claim_source

    refs: list[str] = []
    try:
        with readonly_lab_db() as lab:
            aliases = {domain}
            if domain.endswith("outcomes"):
                aliases.update({"outcome", "outcomes"})
            aliases = tuple(sorted(aliases))
            placeholders = ",".join("?" for _ in aliases)
            rows = lab.execute(
                "SELECT * FROM observation_retention_claims WHERE status='active' "
                f"AND (claim_domain IN ({placeholders}) OR source_kind IN ({placeholders})) ORDER BY source_ref",
                (*aliases, *aliases),
            ).fetchall()
            for row in rows:
                try:
                    _validate_retention_claim_source(
                        lab,
                        observation_id=str(row["observation_id"]),
                        generation=int(row["generation"]),
                        source_kind=str(row["source_kind"]),
                        source_ref=str(row["source_ref"]),
                        source_hash=str(row["source_hash"]),
                    )
                except (ValueError, sqlite3.Error) as exc:
                    raise ExperimentStateReadError(
                        f"Observation retention claim unavailable: {exc}"
                    ) from exc
                if row["source_ref"]:
                    refs.append(str(row["source_ref"]))
    except Exception as exc:
        raise ExperimentStateReadError(
            f"Failed to query exact Observation retention claims: {exc}"
        ) from exc
    return tuple(refs)


def list_observation_filter_outcome_priority_keys(*, deadline: float) -> tuple[set[str], set[str]]:
    """Prioritize frozen active Lab evidence, then proven retained historical keys."""
    from aletheia.observation_lab.canonical import _validate_retention_claim_source
    from aletheia.observation_lab.db import readonly_lab_db

    current: set[str] = set()
    retained: set[str] = set()
    with readonly_lab_db() as lab:
        lab.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            for row in lab.execute(
                "SELECT DISTINCT e.outcome_candidate_key FROM observation_exposures e "
                "JOIN observation_windows w USING(observation_id,generation) "
                "JOIN observation_generation_contracts c USING(observation_id,generation) "
                "WHERE w.status='active' AND e.outcome_source='fixed_horizon_candidate' "
                "AND e.behavior_contract_hash=c.behavior_contract_hash "
                "AND e.outcome_candidate_key IS NOT NULL"
            ):
                if time.monotonic() >= deadline:
                    raise TimeoutError("observation_priority_deadline")
                current.add(str(row[0]))
            for row in lab.execute(
                "SELECT * FROM observation_retention_claims WHERE status='active' "
                "AND claim_domain='filter_candidate_outcomes' AND source_kind='outcome_candidate_key'"
            ):
                if time.monotonic() >= deadline:
                    raise TimeoutError("observation_priority_deadline")
                _validate_retention_claim_source(
                    lab, observation_id=row["observation_id"], generation=row["generation"],
                    source_kind=row["source_kind"], source_ref=row["source_ref"], source_hash=row["source_hash"],
                )
                retained.add(str(row["source_ref"]))
        finally:
            lab.set_progress_handler(None, 0)
    return current, retained - current


def active_benchmark():
    from aletheia.observation_lab.kernel import active_window
    return active_window(kind="universe_discovery")


@contextmanager
def outcome_write_guard(window_id: str):
    from aletheia.observation_lab.worker_lock import worker_cycle_exclusion
    with worker_cycle_exclusion():
        legacy = get_universe_discovery_benchmark(window_id)
        if legacy is not None:
            allowed = str(legacy["status"]) == "active"
        else:
            from aletheia.observation_lab.kernel import status_window
            try:
                allowed = str(status_window(window_id)["status"]) == "active"
            except KeyError:
                allowed = False
        yield allowed
