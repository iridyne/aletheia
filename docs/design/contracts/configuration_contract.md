# Configuration Loading Contract

The canonical persisted configuration is loaded by
`aletheia.config.loader.load_config()` into the `AletheiaConfig` model. Known
legacy migrations remain explicit and deterministic. Before a configuration is
cached, projected into runtime authority, or written back by a migration, the
canonical model is validated with unknown model fields forbidden recursively.

Unknown root or nested fields are a configuration blocker. The loader raises a
Pydantic validation errors retain the dotted rejected path, but unknown-key
failures never include the rejected input value in their string, JSON, or error
mapping projections. The source file remains untouched, so a later Settings
save cannot erase malformed input or silently persist a substituted default.
Doctor exposes this condition as the `config.validation` blocker with a repair
hint. Dynamic dictionary keys, including model-provider names and family quotas,
remain valid data because they are values of typed mappings rather than model
fields.

Valid configurations and supported legacy migrations retain their existing
round-trip and persistence behavior. This contract does not add migrations,
change defaults, alter authority ownership, or change strategy and execution
policy.

## Executable evidence

- `tests/test_config.py` covers unknown root/nested fields, safe error paths,
  no source rewrite, dynamic mappings, and the existing migration path.
- `tests/test_doctor.py` covers the Doctor blocker projection and redaction.
- `tests/specs/spec_configuration.py` registers the strict-load and dynamic-map
  claims in the executable conformance matrix.
