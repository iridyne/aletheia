# Domain 12: Engineering Assurance

This domain closes the primary research lanes by examining the mechanisms that
make an application diagnosable, bounded, secure, verifiable, distributable,
and safely upgradeable after its feature code has been written.

## Included

- liveness, readiness, domain health, freshness, logs, error semantics, and
  request-performance visibility;
- configuration validation, credential classification, authentication,
  network exposure, outbound HTTP, plugin installation, and user-authored code;
- cache, memory, rate, timeout, response-size, process, and frontend-bundle
  budgets;
- backend, frontend, contract, integration, end-to-end, static-analysis, CI,
  security-scan, and release-gate strategy;
- Docker, PyInstaller, Inno Setup, macOS DMG, version identity, dependency
  reproducibility, data placement, migration, backup, restore, update, signing,
  and rollback;
- a direct comparison with Aletheia's current assurance mechanisms and gaps.

## Excluded

- implementation changes in either repository;
- dynamic security probing, exploit execution, provider requests, package
  installation, server startup, browser automation, benchmarks, or fault
  injection;
- test execution, including `pytest`, Vitest, build workflows, installers, and
  packaged-runtime smoke tests;
- treating source presence or a workflow definition as evidence that it passed.

## Reports

- `source-map.md`
- `engineering-assurance-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

All findings are static and tied to the baseline recorded by this research
program. Test-file and test-function numbers are inventory counts, not pass
results. Workflow behavior is inferred from definitions, not from current CI
or release-run history. Runtime status, real package signatures, deployed
permissions, active reverse-proxy configuration, and backup recoverability are
`Not available` unless the source itself establishes the mechanism.
