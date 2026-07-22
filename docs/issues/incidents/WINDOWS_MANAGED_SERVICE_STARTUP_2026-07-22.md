# Windows managed gateway startup failure — 2026-07-22

## Classification

**A — repair now.** The failure exits before the gateway is created and does not
change model input, strategy logic, risk evaluation, execution, or live state.

## Symptom

`aletheia gateway start` on Windows exited while creating the on-demand Task
Scheduler service with:

```text
AttributeError: 'PureWindowsPath' object has no attribute 'exists'
```

## Root cause and repair

`_windows_program_and_arguments()` intentionally used `PureWindowsPath` when
forming Windows command-line paths, but then attempted to call its unavailable
filesystem method. The venv configuration probe now converts that path to
`pathlib.Path` before calling `exists()`.

## Verification

`uv run pytest tests/test_managed_service.py::test_windows_program_arguments_checks_windows_venv_config_as_filesystem_path -q`

The wider managed-service test file has a pre-existing macOS session-service
failure on this Windows host; it is unrelated to this Windows command-path
regression and remains out of scope for this repair.
