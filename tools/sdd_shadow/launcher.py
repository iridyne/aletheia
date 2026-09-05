"""Launch an isolated, advisory Spec Kit shadow workflow.

The launcher owns the accidental-mutation boundary.  It archives a clean HEAD
into a disposable project, gives Spec Kit only that project, validates every
bounded JSON stage, and copies the validated bundle to the caller-selected
external directory.  It never edits the source checkout or calls a shell.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

try:  # Support both ``python -m tools.sdd_shadow.launcher`` and direct execution.
    from .validate import ValidationError, stages_for_phase, validate_artifact, validate_artifacts
except ImportError:  # pragma: no cover - exercised by the direct CLI invocation.
    from validate import ValidationError, stages_for_phase, validate_artifact, validate_artifacts


SPEC_KIT_VERSION = "1.0.4"
ALLOWED_INTEGRATIONS = frozenset({"omp"})
PHASES = ("prechange", "convergence")
WORKFLOW_NAMES = {
    "prechange": "workflow-prechange.yml",
    "convergence": "workflow-convergence.yml",
}


class LauncherError(RuntimeError):
    """Base class for safe launcher failures."""


class DirtyCheckoutError(LauncherError):
    """Raised when source contains uncommitted tracked or untracked changes."""


@dataclass(frozen=True)
class Provenance:
    source_head: str
    source_base: str | None
    merge_base: str | None
    changed_files: tuple[str, ...]
    diff_sha256: str | None


@dataclass(frozen=True)
class PreparedRun:
    run_id: str
    scratch: Path
    snapshot: Path
    run_dir: Path
    artifact_dir: Path
    context_path: Path
    provenance: Provenance
    contract_sha256: str
    source_checkout_clean: bool


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_id() -> str:
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_path(path: Path) -> Path:
    """Resolve a user path without requiring it to exist yet."""

    return path.expanduser().resolve(strict=False)


def _run_git(repo: Path, args: Sequence[str]) -> bytes:
    """Run a fixed Git operation without invoking a shell."""

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise LauncherError(f"cannot execute git: {exc}") from exc
    return result.stdout


def _ensure_git_root(repo: Path) -> None:
    if not repo.is_dir():
        raise LauncherError(f"repository path is not a directory: {repo}")
    try:
        root = _run_git(repo, ["rev-parse", "--show-toplevel"]).decode().strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise LauncherError(f"not a Git checkout: {detail or repo}") from exc
    if _safe_path(Path(root)) != repo:
        raise LauncherError("--repo must name the Git worktree root")


def _assert_clean(repo: Path) -> None:
    try:
        status = _run_git(repo, ["status", "--porcelain=v1", "--untracked-files=all"]).decode()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise LauncherError(f"cannot inspect source checkout: {detail or repo}") from exc
    if status:
        raise DirtyCheckoutError(
            "source checkout is dirty; commit or stash tracked/untracked changes before running "
            "the shadow workflow"
        )


def _resolve_ref(repo: Path, ref: str) -> str:
    # ``--end-of-options`` prevents a ref-like input from being parsed as a Git option.
    try:
        value = _run_git(repo, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise LauncherError(f"cannot resolve Git ref {ref!r}: {detail}") from exc
    return value.decode().strip()


def _provenance(repo: Path, *, phase: str, head_ref: str, base_ref: str | None) -> Provenance:
    head = _resolve_ref(repo, head_ref)
    if phase != "convergence" or base_ref is None:
        return Provenance(head, None, None, (), None)
    base = _resolve_ref(repo, base_ref)
    try:
        merge = _run_git(repo, ["merge-base", base, head]).decode().strip()
        diff = _run_git(repo, ["diff", "--binary", f"{merge}...{head}"])
        changed_raw = _run_git(repo, ["diff", "--name-only", "-z", f"{merge}...{head}"])
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise LauncherError(f"cannot freeze convergence provenance: {detail}") from exc
    changed_files = tuple(item.decode("utf-8") for item in changed_raw.split(b"\0") if item)
    return Provenance(head, base, merge, changed_files, _sha256_bytes(diff))


def _safe_member_path(root: Path, member_name: str) -> Path:
    relative = PurePosixPath(member_name)
    if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise LauncherError(f"unsafe archive member: {member_name}")
    target = root.joinpath(*relative.parts)
    if not _safe_path(target).is_relative_to(_safe_path(root)):
        raise LauncherError(f"archive member escapes snapshot: {member_name}")
    return target


def _extract_archive(archive: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = _safe_path(destination)
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:")
    except (tarfile.TarError, OSError) as exc:
        raise LauncherError(f"Git archive could not be read: {exc}") from exc
    with tar:
        for member in tar:
            target = _safe_member_path(root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                source = tar.extractfile(member)
                if source is None:
                    raise LauncherError(f"archive member has no content: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                try:
                    target.chmod(member.mode & 0o777)
                except OSError:
                    pass
                continue
            if member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise LauncherError(f"unsafe archive symlink: {member.name}")
                resolved = _safe_path(target.parent / Path(*link.parts))
                if not resolved.is_relative_to(root):
                    raise LauncherError(f"archive symlink escapes snapshot: {member.name}")
                os.symlink(member.linkname, target)
                continue
            raise LauncherError(f"unsupported archive member: {member.name}")


def _archive_head(repo: Path, head: str) -> bytes:
    try:
        return _run_git(repo, ["archive", "--format=tar", head])
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise LauncherError(f"cannot archive source HEAD: {detail}") from exc


def _copy_runtime(scratch: Path) -> None:
    package = Path(__file__).resolve().parent
    runtime = scratch / ".sdd_shadow_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("prompts", "schemas", "fixtures"):
        source = package / name
        if source.is_dir():
            shutil.copytree(source, runtime / name, symlinks=False)
    shutil.copyfile(package / "validate.py", runtime / "validate.py")

    workflow_root = scratch / ".specify" / "workflows"
    workflow_root.mkdir(parents=True, exist_ok=True)
    for name in WORKFLOW_NAMES.values():
        shutil.copyfile(package / name, workflow_root / name)
    # Spec Kit owns this cursor only inside the disposable project.
    (workflow_root / "runs").mkdir(parents=True, exist_ok=True)


def _copy_input(source: Path, destination: Path) -> str:
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise LauncherError(f"cannot read frozen Change Contract: {exc}") from exc
    if not raw:
        raise LauncherError("frozen Change Contract is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return _sha256_bytes(raw)


def _copy_evidence(paths: Iterable[Path], destination: Path, *, label: str) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for index, source in enumerate(paths):
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise LauncherError(f"cannot read {label} evidence {source}: {exc}") from exc
        name = f"{label}-{index:02d}.bin"
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        copied.append({"name": name, "sha256": _sha256_bytes(raw)})
    return copied


def _prepare_run(
    *,
    repo: Path,
    phase: str,
    contract: Path,
    base_ref: str | None,
    head_ref: str,
    runtime_evidence: Sequence[Path],
    review_evidence: Sequence[Path],
) -> tuple[PreparedRun, dict[str, Any]]:
    _ensure_git_root(repo)
    _assert_clean(repo)
    provenance = _provenance(repo, phase=phase, head_ref=head_ref, base_ref=base_ref)
    contract_sha = _sha256_file(contract)
    scratch = Path(tempfile.mkdtemp(prefix="aletheia-sdd-shadow-"))
    snapshot = scratch / "repo"
    run_id = _run_id()
    run_dir = scratch / ".shadow" / run_id
    artifact_dir = run_dir / "artifacts"
    context_path = scratch / ".sdd_shadow_runtime" / "context.json"
    try:
        _extract_archive(_archive_head(repo, provenance.source_head), snapshot)
        _copy_runtime(scratch)
        input_root = scratch / ".sdd_shadow_runtime" / "input"
        contract_relative = input_root / "change-contract.md"
        _copy_input(contract, contract_relative)
        runtime_inputs = _copy_evidence(runtime_evidence, input_root / "runtime", label="runtime")
        review_inputs = _copy_evidence(review_evidence, input_root / "review", label="review")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (scratch / ".shadow" / "current-run-id").write_text(run_id + "\n", encoding="utf-8")
        context = {
            "phase": phase,
            "run_id": run_id,
            "integration": "omp",
            "spec_kit_version": SPEC_KIT_VERSION,
            "change_contract": "input/change-contract.md",
            "source_head": provenance.source_head,
            "source_base": provenance.source_base,
            "merge_base": provenance.merge_base,
            "changed_files": list(provenance.changed_files),
            "runtime_evidence": runtime_inputs,
            "review_evidence": review_inputs,
            "output_rule": f".shadow/{run_id}/artifacts/{{stage}}.json",
            "authority_statement": "Shadow findings are advisory and non-authoritative.",
        }
        _write_json(context_path, context)
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    prepared = PreparedRun(
        run_id,
        scratch,
        snapshot,
        run_dir,
        artifact_dir,
        context_path,
        provenance,
        contract_sha,
        True,
    )
    metadata = {
        "runtime_evidence": runtime_inputs,
        "review_evidence": review_inputs,
        "snapshot_has_git": (snapshot / ".git").exists(),
        "canonical_path_exposed": False,
    }
    return prepared, metadata


def _dispatch_spec_kit(run: PreparedRun, *, phase: str) -> None:
    """Dispatch only the checked-in workflow using a fixed argv and cwd."""

    workflow = f".specify/workflows/{WORKFLOW_NAMES[phase]}"
    command = ["specify", "workflow", "run", workflow, "--json"]
    try:
        result = subprocess.run(
            command,
            cwd=run.scratch,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        raise LauncherError(f"Spec Kit dispatch unavailable: {exc}") from exc
    (run.run_dir / "dispatch.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise LauncherError(f"Spec Kit dispatch failed with exit code {result.returncode}")


def _validate_only_artifact(run: PreparedRun, *, phase: str) -> dict[str, Any]:
    artifact = {
        "stage": "synthesis",
        "verdict": "ADVISORY",
        "findings": [],
        "blockers": [],
        "clarifications": [],
        "dependencies": [],
        "non_goals_at_risk": [],
        "verification_obligations": [
            f"Run the {phase} workflow with pinned Spec Kit {SPEC_KIT_VERSION} before interpreting findings."
        ],
        "unknowns": ["No Agent pass was run because --validate-only was selected."],
    }
    # A validate-only record is deliberately not presented as an Agent result.
    _write_json(run.artifact_dir / "validation.json", artifact)
    return validate_artifact(artifact, expected_stage="synthesis")


def _copy_validated_bundle(
    run: PreparedRun,
    *,
    output: Path,
    phase: str,
    integration: str,
    source_metadata: dict[str, Any],
    validate_only: bool,
) -> Path:
    bundle = output / run.run_id
    bundle.mkdir(parents=True, exist_ok=False)
    if validate_only:
        shutil.copyfile(run.artifact_dir / "validation.json", bundle / "validation.json")
        artifact_hashes = {"validation.json": _sha256_file(bundle / "validation.json")}
    else:
        validate_artifacts(run.artifact_dir, phase=phase)
        artifact_hashes: dict[str, str] = {}
        target_dir = bundle / "artifacts"
        target_dir.mkdir()
        for stage in stages_for_phase(phase):
            source = run.artifact_dir / f"{stage}.json"
            target = target_dir / source.name
            shutil.copyfile(source, target)
            artifact_hashes[f"artifacts/{source.name}"] = _sha256_file(target)

    provenance = run.provenance
    manifest = {
        "manifest_version": 1,
        "phase": phase,
        "run_id": run.run_id,
        "created_at": _utc_now().isoformat().replace("+00:00", "Z"),
        "source_head": provenance.source_head,
        "source_base": provenance.source_base,
        "merge_base": provenance.merge_base,
        "changed_files": list(provenance.changed_files),
        "diff_sha256": provenance.diff_sha256,
        "change_contract_sha256": run.contract_sha256,
        "integration": integration,
        "spec_kit_version": SPEC_KIT_VERSION,
        "source_checkout_clean": run.source_checkout_clean,
        "non_authoritative": True,
        "authority_statement": "This shadow result is advisory and non-authoritative; Aletheia's canonical Knowledge Plane and GitHub remain authoritative.",
        "validate_only": validate_only,
        "artifact_sha256": artifact_hashes,
        "isolation": {
            "snapshot_relative": "repo",
            "snapshot_has_git": source_metadata["snapshot_has_git"],
            "run_state_relative": ".specify/workflows/runs",
            "agent_output_relative": f".shadow/{run.run_id}",
            "canonical_path_exposed": source_metadata["canonical_path_exposed"],
            "canonical_checkout_mutated": False,
            "accidental_mutation_isolation": True,
            "hostile_process_sandbox": False,
            "disposable_project_removed_after_collection": True,
        },
        "runtime_evidence": source_metadata["runtime_evidence"],
        "review_evidence": source_metadata["review_evidence"],
    }
    manifest_path = bundle / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def run_shadow(
    *,
    repo: Path,
    phase: str,
    contract: Path,
    integration: str = "omp",
    output: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    runtime_evidence: Sequence[Path] = (),
    review_evidence: Sequence[Path] = (),
    validate_only: bool = False,
) -> Path:
    """Run one phase and return the external manifest path."""

    if phase not in PHASES:
        raise LauncherError(f"unsupported phase: {phase}")
    if integration not in ALLOWED_INTEGRATIONS:
        raise LauncherError(f"integration is not allowlisted: {integration}")
    repo = _safe_path(repo)
    contract = _safe_path(contract)
    output = _safe_path(output)
    if output == repo or output.is_relative_to(repo):
        raise LauncherError("--output must be external to --repo")
    if not contract.is_file():
        raise LauncherError(f"Change Contract path is not a file: {contract}")
    output.mkdir(parents=True, exist_ok=True)
    run, source_metadata = _prepare_run(
        repo=repo,
        phase=phase,
        contract=contract,
        base_ref=base_ref,
        head_ref=head_ref,
        runtime_evidence=runtime_evidence,
        review_evidence=review_evidence,
    )
    try:
        if validate_only:
            _validate_only_artifact(run, phase=phase)
        else:
            _dispatch_spec_kit(run, phase=phase)
        return _copy_validated_bundle(
            run,
            output=output,
            phase=phase,
            integration=integration,
            source_metadata=source_metadata,
            validate_only=validate_only,
        )
    finally:
        shutil.rmtree(run.scratch, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an isolated Aletheia Spec Kit shadow audit")
    parser.add_argument("--repo", type=Path, required=True, help="clean Git worktree root")
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--change-contract", type=Path, required=True, dest="contract")
    parser.add_argument("--integration", default="omp", choices=sorted(ALLOWED_INTEGRATIONS))
    parser.add_argument("--output", type=Path, required=True, help="external result directory")
    parser.add_argument("--base-ref", help="convergence base ref (optional)")
    parser.add_argument("--head-ref", default="HEAD", help="candidate ref, default HEAD")
    parser.add_argument(
        "--runtime-evidence",
        "--runtime-evidence-file",
        action="append",
        type=Path,
        default=[],
        dest="runtime_evidence",
    )
    parser.add_argument(
        "--review-evidence",
        "--review-evidence-file",
        action="append",
        type=Path,
        default=[],
        dest="review_evidence",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="prepare and validate isolation without invoking Spec Kit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = run_shadow(
            repo=args.repo,
            phase=args.phase,
            contract=args.contract,
            integration=args.integration,
            output=args.output,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            runtime_evidence=tuple(args.runtime_evidence),
            review_evidence=tuple(args.review_evidence),
            validate_only=args.validate_only,
        )
    except (LauncherError, ValidationError, OSError) as exc:
        print(f"sdd-shadow error: {exc}", file=sys.stderr)
        return 2
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
