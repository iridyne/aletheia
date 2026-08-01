---
id: gateway-service-lifecycle
status: implemented
github_issue: 567
related_contracts: []
supersedes: []
---

# Gateway Service Lifecycle Proposal

## Summary

Aletheia currently has two related but different operating-system integrations:

- `gateway start|stop` creates and removes an on-demand, user-owned session
  service; and
- `gateway enable|disable` manages a historical autostart watchdog.

The CLI does not expose persistent service installation, and the word
"service" does not yet identify one consistent lifecycle model. This Proposal
defines a cross-platform model with three independent facts:

1. whether a service backend is installed;
2. whether automatic activation or recovery is enabled; and
3. whether the Gateway is running now.

The default remains a no-administrator session backend. An optional Windows
machine service may be added for reboot recovery and operation without an
interactive login, but only through explicit UAC elevation and a qualified
least-privilege service identity. Aletheia never asks for or stores an
administrator password.

Execution is tracked in GitHub Issue
[#567](https://github.com/iridite/aletheia/issues/567). This draft does not
claim that `gateway service install|uninstall|status` exists.

## Current fact

The current CLI exposes `start`, `stop`, `restart`, `status`, `logs`, `pause`,
`resume`, `enable`, `disable`, and `open-dashboard`.

`gateway start` runs the existing Gateway runner under an on-demand native
manager and verifies the readiness API and dashboard before claiming success.
The current platform mapping is:

| Platform | On-demand session owner |
| --- | --- |
| Windows | Task Scheduler task; S4U first, then InteractiveToken only after explicit S4U access denial |
| macOS | per-user launchd job |
| Linux | transient user systemd service |

`gateway stop` asks the same manager to stop the runner and removes the
transient definition. It does not kill a process identified only by its port.
An ownership or permission conflict is reported rather than bypassed.

`gateway enable|disable` controls the historical autostart/watchdog facility.
It is not a Windows Service installer. Windows service installation remains a
future packaging item in the implementation guide.

PR #566 completed the Windows Task Scheduler `S4U -> InteractiveToken`
compatibility fallback. That repair makes the default session backend usable
on a wider set of ordinary Windows hosts; it does not provide unattended
machine-service operation.

## Goals and non-goals

### Goals

- Give `install`, `uninstall`, `enable`, `disable`, `start`, `stop`, `restart`,
  and `status` stable, non-overlapping meanings.
- Preserve a zero-administrator default on generic desktop Windows, macOS, and
  Linux hosts.
- Add an explicit path to a durable Windows machine service when unattended
  reboot recovery is required.
- Make installation, enablement, runtime, ownership, configuration drift,
  identity, and privilege requirements independently observable.
- Keep the existing Gateway runner as the only supervisor of its frontdoor and
  backend children.
- Make every operation idempotent, ownership-safe, and recoverable after a
  partial failure.
- Preserve live-disabled defaults and all existing trading, risk, execution,
  audit, and secret boundaries.

### Non-goals

- No direct order placement or change to trading lifecycle semantics.
- No administrator password prompt implemented by Aletheia.
- No stored Windows account password.
- No automatic promotion from a session backend to a machine backend.
- No LocalSystem default.
- No generic process termination based only on a listening port.
- No second supervisor for Gateway child processes.
- No Docker requirement.
- No NSSM, WinSW, or other third-party dependency in the default session flow.
- No claim that every Windows security policy can be bypassed without an
  administrator. Permission failures remain explicit operator decisions.

## Proposed design

### 1. Terminology and state model

The CLI reports these facts separately:

| Field | Values | Meaning |
| --- | --- | --- |
| `scope` | `session`, `machine` | User-session or machine-wide ownership boundary |
| `backend` | `task_scheduler`, `launchd_user`, `systemd_user`, `windows_service` | Native manager in control |
| `registration` | `absent`, `transient`, `persistent`, `unknown` | Whether a manager definition exists and survives a normal stop |
| `enabled` | `true`, `false`, `unknown` | Whether login/boot/recovery activation is configured |
| `runtime` | `stopped`, `starting`, `running`, `stopping`, `degraded`, `unknown` | Current process state derived from the native manager and readiness checks |
| `owned` | `true`, `false`, `unknown` | Whether the definition and process are verified as this Aletheia runtime |
| `config_valid` | `true`, `false`, `unknown` | Whether executable, working directory, data directory, arguments, and identity match expected values |
| `elevation_required` | `true`, `false` | Whether the requested transition crosses an administrator boundary |
| `recovery_scope` | `none`, `login`, `failure`, `boot_and_failure` | The recovery guarantee actually configured |

No PID file or Aletheia metadata file may override native manager truth. Local
metadata may record the selected persistent backend and an in-progress
installation transaction, but `status` must reconcile those records with the
OS definition, process identity, port, and readiness endpoint.

Only one persistent backend may be selected for a data directory. Installing a
second backend returns `backend_conflict` unless an explicit, transactional
switch operation is added and qualified. This prevents two lifecycle owners
from racing over one Gateway and one SQLite database.

### 2. Command contract

| Command | Contract |
| --- | --- |
| `gateway start` | Start now through the selected persistent backend, or use the existing transient session backend when none is installed. Do not change `enabled`. Clear only manager-native intentional-stop suppression needed for this start. |
| `gateway stop` | Stop now through the verified owner. Preserve persistent registration and `enabled`; remove a transient definition. An enabled persistent backend may start again at its next boot/login trigger, but an explicit stop must not be treated as a crash and immediately resurrected. |
| `gateway restart` | Perform verified stop then start through the same backend, preserving installation and enablement. Abort if stop cannot prove ownership or completion. |
| `gateway enable` | Enable the selected persistent backend's boot/login/failure activation. If no persistent backend exists, install the platform session backend first. Do not claim the Gateway is running and do not start it as an undocumented side effect. |
| `gateway disable` | Disable automatic activation and failure recovery. Do not stop an already running Gateway and do not uninstall its definition. |
| `gateway service status` | Return the complete state model, identity, drift, last manager result, permission diagnosis, and safe recovery command. It must work when the Gateway is down. |
| `gateway service install` | Install or reconcile a persistent definition. Default to `--backend session`; require explicit `--backend windows-service` for machine scope. Do not enable or start unless separately requested by future compositional flags. |
| `gateway service uninstall` | Refuse with `service_running` while a verified Gateway is running. A future explicit `--stop` may compose a verified stop before removal. Remove only a verified Aletheia definition; preserve data, logs, config, and credentials. |

`gateway status` remains the concise daily command. It incorporates a summary
of service state and points to `gateway service status` when remediation detail
is needed.

The command contract follows conventional service semantics: installed,
enabled, and running are not aliases. In particular, `enabled + stopped` is a
valid state. It means the Gateway is stopped now and may start at the next
configured login or boot event.

### 3. Intentional stop versus failure recovery

An explicit `gateway stop` must not cause an enabled watchdog to immediately
run `gateway start` again. Every backend must distinguish an operator stop from
an unexpected exit:

- machine service backends use native stop and restart-on-failure semantics;
- systemd uses an on-failure policy that does not restart after an explicit
  `systemctl stop`;
- launchd installation must use unload/enable semantics that preserve the
  configured next-login policy without `KeepAlive` restart churn; and
- Task Scheduler must replace blind periodic resurrection with a qualified
  manager design that distinguishes `/End` from process failure. If native task
  settings cannot provide that distinction reliably, the session backend must
  report the narrower `login` recovery scope instead of inventing a stronger
  guarantee. A visible, bounded intentional-stop marker is acceptable only if
  it is reset deterministically at the next login/boot trigger and is surfaced
  by `service status`.

This behavior requires real-host qualification. Unit tests alone cannot prove
Task Scheduler or Windows Service Manager restart semantics.

### 4. Backend selection and migration

Backend selection order is:

1. an explicit CLI `--backend` for install/status operations;
2. the single verified persistent backend selected for the current absolute
   data directory; or
3. the platform's transient session backend for `gateway start`.

An environment variable must not silently switch the active service owner.
Changing the backend, executable path, project root, or data directory is a
reconciliation operation with drift reporting, not an implicit reinstall.

The existing watchdog registration is migrated as follows:

1. inspect current and legacy manager definitions without starting them;
2. identify only definitions with verified Aletheia labels, commands, paths,
   and ownership;
3. report foreign-owner or ambiguous definitions as conflicts;
4. install the new persistent session definition atomically;
5. disable and remove only verified legacy definitions; and
6. preserve the running Gateway until the new owner is ready, avoiding a
   split-brain interval.

During a compatibility window, `gateway enable|disable` retains its public
spelling but emits status using the new model. Any legacy aliases are hidden,
documented as deprecated, and eventually removed only after migration evidence
shows no current registrations depend on them.

### 5. Default session backends

Session installation is passwordless and user-scoped:

| Platform | Persistent definition | Enablement boundary | Important limitation |
| --- | --- | --- | --- |
| Windows | current-user Task Scheduler task | user login and qualified failure recovery | InteractiveToken needs a logged-in session; S4U can be denied by policy |
| macOS | user LaunchAgent | user login | no service before that user session exists |
| Linux | user systemd unit | user login, or lingering only when separately provisioned | user manager availability varies by host policy |

The installer probes capabilities before mutation and records the exact
backend and recovery scope actually achieved. It never translates a policy
denial into a detached `pythonw`, `nohup`, terminal multiplexer, or other
unowned process.

Windows continues to prefer S4U for non-interactive compatibility and retries
InteractiveToken only for an explicit S4U access-denied result. Other S4U
failures remain failures. InteractiveToken must be reported as session-bound;
it must not be advertised as reboot-unattended operation.

### 6. Optional Windows machine service

The machine backend is an explicit deployment product, not a fallback from
Task Scheduler:

```text
aletheia gateway service install --backend windows-service
```

Before implementation is accepted, an architecture spike must compare WinSW,
NSSM, and a small native service host against these criteria:

- maintained release and compatible license;
- pinned artifact version and SHA-256 verification;
- no network download inside the elevated transaction;
- deterministic install, start, stop, failure restart, and uninstall behavior;
- child-process tree termination;
- safe stdout/stderr capture and rotation;
- service identity and per-service SID support;
- configuration validation and upgrade behavior;
- Windows on ARM/x64 packaging requirements; and
- ability to run the existing Gateway runner without becoming a second child
  supervisor.

WinSW is the initial candidate because it offers an inspectable service
configuration and failure policy. The Proposal does not approve bundling it
until the supply-chain, packaging, and real-host qualification gates pass.
NSSM may remain an externally provisioned integration, but it does not remove
administrator, identity, data, or credential requirements.

The supported machine-service identity must be passwordless from Aletheia's
perspective and least privilege. The preferred direction is `LocalService`
combined with a per-service SID and explicit ACLs. LocalSystem is rejected as
the default. Running as the interactive operator account is also rejected when
it would require Aletheia to collect that account's password or grant broad
profile access.

Machine service data must use an explicit machine path such as
`%ProgramData%\Aletheia`, not the installing administrator's or interactive
user's `~/.aletheia`. The service SID receives only the filesystem and network
rights required by the Gateway; administrators retain recovery access; normal
users do not receive write access to service binaries or definitions.

The existing secret implementation is user-context oriented. A machine
service cannot become a supported secret-dependent runtime until a separate
change-controlled design proves secure provisioning, encryption scope,
rotation, backup exclusion, redaction, and ACL behavior for the service
identity. Installation may prepare directories and report
`credentials_not_provisioned`, but it must not copy user secrets, downgrade
encryption, or claim readiness. Live trading remains disabled throughout.

### 7. UAC and administrator interaction

Aletheia never implements an administrator password field. On an interactive
Windows desktop, a machine-scope mutation uses Windows `ShellExecuteEx` with
the `runas` verb so consent or credentials are handled by the Windows secure
desktop.

The non-elevated parent performs read-only preflight first, creates a
non-secret installation request with absolute canonical paths and a
single-use nonce, and then launches a narrow elevated helper from the same
verified artifact. The helper:

1. revalidates every path, owner, backend, nonce, and expected current state;
2. performs only the requested service transaction;
3. writes a structured, non-secret result to an ACL-restricted handoff file;
4. rolls back newly created definitions on partial failure; and
5. never launches the Gateway merely because installation succeeded.

The request and result contain no API key, token, password, decrypted secret,
or inherited environment dump. Temporary handoff files are bounded to the
current transaction and safely removed after consumption.

UAC cancellation maps to `elevation_cancelled`, Windows error 1223. A missing
interactive desktop maps to `elevation_unavailable`. Headless and enterprise
hosts receive a deterministic administrator command or signed provisioning
package that can be executed through their existing management channel. The
CLI does not emulate `sudo`, retry password guesses, or weaken policy.

### 8. Status and failure semantics

Every mutating command returns one structured result internally and a concise
operator message externally. At minimum, failures distinguish:

| Reason | Required response |
| --- | --- |
| `access_denied` | Name the denied manager operation and whether UAC can resolve it |
| `elevation_cancelled` | Preserve all prior state; provide the non-elevated alternative |
| `elevation_unavailable` | Provide an administrator provisioning path for headless operation |
| `ownership_conflict` | Report actual owner and refuse mutation |
| `backend_conflict` | Report both definitions and require explicit reconciliation |
| `config_drift` | Show drifted non-secret fields and the safe reconcile command |
| `service_running` | Refuse uninstall unless a separately qualified stop is requested |
| `partial_install` | Report completed/rolled-back steps and a repair command |
| `credentials_not_provisioned` | Keep the service stopped/not-ready without exposing secret details |
| `port_conflict` | Identify holders when possible; never kill an unverified holder |
| `manager_unavailable` | Report the missing/disabled OS capability and supported fallback |
| `unsupported_backend` | List only backends supported on this platform/build |

Human-readable Windows output uses the console decoding strategy already
qualified for `schtasks`. Structured results do not derive behavior by parsing
localized prose when a native status code or API is available.

`doctor` reports the same model and may repair non-destructive drift. It must
not cross an administrator boundary, uninstall a service, migrate credentials,
or change backend scope under `--fix` without a separate explicit operator
command.

### 9. Trust and audit boundaries

Service lifecycle operations remain operator-only bootstrap capabilities. They
must not be registered as LLM-callable tools. Dashboard or agent APIs may show
read-only status but cannot install, uninstall, enable, disable, start, stop,
or elevate an OS service.

Audit events record the real interactive operator, elevation boundary,
backend, scope, previous state, requested transition, result, and non-secret
drift. They never include passwords, tokens, secret values, full environments,
or raw service configuration containing credentials.

Machine-service credential provisioning touches a controlled trust boundary
and requires its own focused review and regression evidence. This Proposal does
not authorize edits to execution, risk, exchange authentication, live unlock,
or hard limits.

## Trust and complexity review

The session-only compatibility repair was deliberately small: it kept the
current user as owner and added a Task Scheduler logon fallback. A machine
service is materially different. It crosses user/session boundaries, executes
after reboot, needs administrator installation, changes filesystem ownership,
and may need access to secrets outside an interactive user's profile.

Treating these as another branch inside `gateway start` would hide the most
important security and operational differences. Treating WinSW or NSSM as the
solution would also be incomplete; a wrapper cannot decide Aletheia's service
identity, data ownership, credential scope, audit actor, or migration policy.

The added state model is therefore necessary. Complexity is bounded by keeping
one active backend, one existing Gateway runner, no new process supervisor, no
credential prompt, and no machine-service default.

## Verification contract

The capability cannot move into current Product Truth or Operations guidance
until focused automated evidence covers:

- every legal command transition and every rejected transition;
- idempotent install, enable, start, stop, restart, disable, and uninstall;
- independent installed/enabled/running status combinations;
- ownership and configuration-drift detection;
- migration from every verified legacy watchdog definition;
- S4U success, explicit access-denied fallback, non-access-denied failure, and
  InteractiveToken session limitations;
- UAC accepted, cancelled, timed out, and unavailable;
- non-administrator machine-service attempts;
- foreign-owner and ambiguous service definitions;
- stale executable, project, data, and wrapper paths;
- wrapper missing, corrupt, untrusted, or version-mismatched;
- partial installation rollback and interrupted reconciliation;
- service identity, filesystem ACLs, log ACLs, and dedicated data directory;
- absence of credentials and secrets from process command lines, task/service
  configuration, temporary requests, logs, audit events, and error output;
- explicit stop not triggering immediate resurrection;
- enabled service starting at the documented next login/boot boundary;
- stop and uninstall never terminating an unverified port holder;
- rollback from machine to session backend without deleting runtime data;
- agent tool registries remaining unable to mutate service state; and
- live trading remaining disabled with no risk or execution behavior change.

Real-host qualification is required on at least:

| Host | Qualification |
| --- | --- |
| Standard-user Windows desktop | session install/start/stop and InteractiveToken fallback |
| Elevated Windows desktop | UAC machine install/uninstall and cancellation |
| Windows after reboot without user login | machine service boot and failure recovery |
| Windows with restrictive Scheduler policy | truthful session limitation and machine provisioning path |
| macOS user session | install/enable/start/stop/disable/uninstall without launchd churn |
| Linux user systemd session | the same lifecycle matrix and manager-unavailable failure |

CI remains the durable unit/contract evidence. Real-host qualification records
the OS edition/build, account type, backend, recovery scope, and sanitized
result without storing local runtime facts in current product documentation.

## Rollout and rollback

Rollout is phased:

1. land the state/result model and read-only status without changing current
   lifecycle behavior;
2. add persistent session install/uninstall and migrate enable/disable behind
   compatibility tests;
3. qualify session behavior on Windows, macOS, and Linux;
4. build the Windows machine-service backend behind an explicit selector and
   keep it unsupported until identity, secret scope, UAC, rollback, packaging,
   and real-host gates pass; and
5. after acceptance evidence, move normative behavior into the Gateway
   contract and deployment operations documents.

Rollback always prefers disabling automatic recovery, stopping through the
verified manager, and uninstalling only the selected definition. It preserves
data, configuration, logs, and credentials. If machine-service rollback cannot
prove cleanup, it reports a repairable partial state and does not activate the
session backend, preventing dual ownership.

The current transient session `gateway start|stop` remains the compatibility
fallback until the persistent session backend is qualified. The machine backend
can be withdrawn without removing that fallback.

## Resolution

This Proposal is implemented. Normative CLI behavior now lives in the OS
Service-lifecycle section of the Agent Contracts, and platform operation lives
in the Gateway session-service runbook. GitHub Issue #567 and its implementation
PR preserve the execution and verification record.
