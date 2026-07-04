# Security Policy

Aletheia controls trading workflows, credentials, and audit records. Treat all
security issues as high impact until proven otherwise.

## Supported Scope

Security-sensitive areas include:

- Exchange credentials and signing.
- Model provider credentials.
- Secrets storage and redaction.
- Risk Engine, kill switch, and live-trading gates.
- Execution Gateway and broker adapters.
- Audit log integrity and causal IDs.
- API routes that mutate trading, risk, config, or permission state.
- Agent permissions and restricted tool enforcement.

## Reporting

Do not open a public issue that includes secrets, exploit steps against a live
account, private logs, or account-specific trading data.

Report privately through the maintainer channel already established for the
project. If the repository has private GitHub security advisories enabled, use
that path. Include:

- Affected file or component.
- Reproduction steps.
- Expected impact.
- Whether credentials, live trading, or order placement are involved.
- Any safe proof-of-concept output with secrets removed.

## Secret Handling

Never commit or paste:

- `.env`
- API keys, secrets, passphrases, tokens, or encrypted secret blobs
- local databases
- account balances, private fills, or broker responses containing identifiers
- raw logs that may contain credentials or personal data

If a secret is exposed, assume it is compromised. Rotate it outside the repo and
remove the leaked material from future commits and artifacts.

## Security Fix Expectations

Security fixes should:

- Preserve live trading disabled-by-default behavior.
- Keep all orders routed through the Execution Gateway.
- Keep Risk Engine approval mandatory.
- Keep credentials write-only and redacted.
- Add focused regression tests when behavior changes.
- Run `uv run aletheia doctor` after implementation when local runtime state is relevant.

Do not combine security fixes with broad refactors or unrelated feature work.

## Responsible Testing

Use demo/paper modes for verification. Do not test against live funds unless the
owner explicitly authorizes a specific live test. Do not run destructive database
or git commands as part of a security report.
