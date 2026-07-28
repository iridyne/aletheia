# Operator Doctor Runtime Supervision - 2026-07-03

## Scope

Branch: `fix/operator-doctor-runtime-supervision`

This change closes the D10 runtime self-diagnosis gap without expanding agent
authority:

- System Operator diagnostic sessions now receive `doctor` context
- the context includes doctor `blocker`/`warn` checks and `fix_hint` values
- no automatic code edits, live unlocks, risk-limit changes, order placement,
  credential access, or execution-gateway behavior changes were added
- ordinary command sessions do not run full doctor by default

## Existing Runtime Facts

The codebase already had the core doctor surface:

- `GET /_infra/doctor`
- `aletheia doctor --json`
- `create_diagnostic_report`
- health-driven operator recommendations for system, worker, model/runtime, and
  data-source diagnostic reports

The missing link was the LLM repair context: diagnostic sessions were using
`repair_context`, but that context only exposed `compute_health()` issues and
failed tasks. It did not include doctor blocker/warn details or doctor fix hints.

## Change

`repair_context` now includes a `doctor` section. `build_app_context(["doctor"])`
uses `aletheia.diagnostics.doctor.build_report().to_dict()` and stores:

- `healthy`
- `generated_at`
- doctor `summary`
- up to 8 attention checks where severity is `warn` or `blocker`
- each attention check includes `name`, `severity`, compact `detail`, and
  compact `fix_hint`

`context_summary_text()` now summarizes doctor state as:

- blocker count
- warn count
- top attention check
- top fix hint

If doctor itself fails, the context reports a `doctor.error` warning with a shell
fallback hint.

## Quantified Outcome

- Diagnostic-session context gained 1 new section: `doctor`
- Doctor attention checks surfaced to the System Operator prompt: up to 8
- Existing live doctor endpoint remained reachable:
  - status: 200
  - elapsed: 1,571.1 ms
  - total checks: 50
  - blockers: 0
  - warns: 16
- Authority expansion: 0 new trading/execution/live/credential actions

## Verification

- `uv run pytest tests/test_agent_shell.py::test_repair_context_includes_doctor_section tests/test_agent_shell.py::test_build_context_doctor_section_surfaces_fix_hints -q`
  - `2 passed`
- `uv run pytest tests/test_agent_shell.py -q`
  - `77 passed`
- `uv run pytest tests/test_operator_routes.py::test_operator_command_preview_uses_selected_session_kind_context_profile tests/test_operator_routes.py::test_operator_internal_context_query_defaults_to_session_kind_profile -q`
  - `2 passed`
- `uv run pytest tests/test_operator_loop.py -q`
  - `38 passed`
