# Domain 5 Aletheia Comparison

## Summary

TickFlow has the stronger unified capability contract, substantially less
`any` in its frontend API facade, and a more complete first-use/login model.
Aletheia has the stronger request timeout/cancellation path, sanitized
validation errors, remote-access fail-closed policy, and backend-enforced
safety confirmation/audit semantics.

Both rely primarily on handwritten TypeScript response assertions over Python
dictionary responses. Both lose structured HTTP error identity in the browser.
Aletheia's larger route and `any` surface makes that contract debt more acute.

## Comparison Matrix

| Boundary | TickFlow | Aletheia | Assessment |
|---|---|---|---|
| browser request helper | fetch + JSON + plain Error + toast | fetch + JSON + 15-second timeout + merged abort + plain Error | Aletheia transport is stronger |
| error structure in caller | message only | message only after recursive formatting | shared gap; Aletheia formats more shapes but still loses identity |
| frontend facade size | 2,534 lines | 2,895 lines | both are domain monoliths |
| facade `any` signal | one `request<any>`, 44 total any tokens | roughly 56 generic any requests, 137 any tokens | TickFlow is materially more typed |
| backend input validation | broad Pydantic use | broad but mixed Pydantic/dict use | both have meaningful request validation |
| declared response models | zero in roughly 209 API routes | one in roughly 337 server routes | shared major gap |
| generated types | not found | not active; archived proposal only | shared manual-contract risk |
| runtime response validation | not found | not found in browser client | shared gap |
| capability model | centralized enum + limits + backend exception | capability/status facts distributed by subsystem | TickFlow is stronger for optional analytical capability |
| authentication | password/session bootstrap with local-only first setup | local-first; token required for tunnel or configured Remote Control | different threat models; both enforce private routes server-side when active |
| validation privacy | default FastAPI detail includes ordinary validation fields | custom handler strips rejected raw input | Aletheia is stronger |
| critical confirmation | confirm flags on destructive data operations | confirm/ack gates on live, runtime, kill switch, profile deletion, and other controls | Aletheia is stronger in safety depth |
| audit/state failure semantics | not central to inspected frontend contract | unlock fails closed if audit fails; lock proceeds safely with audit warning | Aletheia is materially stronger |
| secret presentation | masked keys and presence booleans | masked/status views; remote token storage helper does not return token | both demonstrate deliberate non-echo behavior |

## What Aletheia Already Does Better

### Bounded, cancellable requests

The client applies a 15-second timeout and threads caller cancellation into the
same AbortController. Combined with the QueryClient's no-retry rule for
AbortError, a cancelled or timed-out request does not silently double its wait.

### Sanitized validation responses

The global RequestValidationError handler removes FastAPI's raw `input` value
and returns only location, message, and type. This reduces reflection of
credentials or arbitrary rejected payloads while retaining actionable field
information.

TickFlow's client already knows how to flatten validation arrays, but its
backend does not apply this privacy reduction.

### Remote access protects reads as well as writes

When tunnel access or token-required Remote Control is enabled, all private API
and infrastructure paths require `X-Aletheia-Token`; only minimal liveness is
public. Missing configured server secret fails closed, and token comparison uses
constant-time comparison. Successful authentication is attached to request
state for audit attribution.

### Critical authority remains backend-enforced

Live unlock requires explicit confirmation, first-use acknowledgement, readiness
scorecard, green state, no blockers, and a successful audit write before config
authority changes. Live lock deliberately proceeds even if audit writing fails
because reducing authority is safer, but the response discloses the audit gap.

Runtime promotion/rollback, kill-switch release, profile deletion, and other
dangerous actions also require server-side confirmation. These semantics are
more important than matching the reference's UI polish.

### Worker and operator permissions are distinct

Backend comments and status surfaces distinguish agent permission
Observe/Paper/Full from operator permission ReadOnly/Research/PaperAuto/Auto.
This prevents one generic “permission” label from hiding different authorities.

### Some critical contracts have regression evidence

Existing tests inspect remote-token non-echo behavior and backend live gates.
They do not close the entire frontend/backend response contract, but they are
stronger evidence than the reference provides.

## Where Aletheia Can Learn

### A unified optional-capability contract

TickFlow expresses availability and limits through a backend capability map.
Aletheia should not copy its tier/product model, but an explicit capability fact
can help analytical UI distinguish:

- supported and available;
- supported but not configured;
- disabled by policy;
- temporarily unavailable;
- rate/batch limited;
- not implemented.

This complements, and never replaces, backend permission/risk enforcement.

### Narrow domain clients and contract modules

Split the 2,895-line facade by stable domain while retaining one transport:

```text
transport
  -> system/risk/execution client
  -> market/portfolio client
  -> research/learning client
  -> settings/providers client
  -> operator/runtime client
```

Request/response types should live with their domain contract, not as enormous
inline generics or duplicate feature-local mirrors.

### Reduce stable-control `any`

Dynamic research payloads may legitimately use unknown records. Stable risk,
credential status, runtime transitions, operator status, settings, and control
responses should use named types or validated schemas.

### Preserve structured errors

Backend routes already emit useful dictionaries such as scorecard failures,
audit-write failure, bulk partial failure, permission denial, job conflicts,
and invalid order type. The browser formatter often selects the human message
and discards code and remaining details. Keep both.

### Declare critical response models first

Do not response-model all 337 routes at once. Start with:

- `/api/status`, risk status and boundaries;
- kill switch, live unlock/lock, permission and exchange mode;
- positions, fills, portfolio and equity;
- proposals, decisions and execution results;
- runtime transition and operator command responses;
- settings/credential status representations.

These models can drive OpenAPI types or contract comparison after they become
authoritative.

## Aletheia Contract Defects Found During Comparison

### First Live unlock cannot satisfy its backend acknowledgement contract

Current backend source defaults `first_live_confirmation_done` to false and
rejects the first unlock unless both `confirm=true` and
`acknowledge_first_unlock=true` are received.

Current frontend source defines:

```text
liveUnlock() -> { confirm: true }
```

The warning wall invokes that zero-argument method and has no path to send the
acknowledgement. Therefore the default first-use browser flow cannot satisfy the
current backend contract. This is static end-to-end source evidence; no runtime
call was performed.

The component also does not render `unlock.error`, and its local warning state
is cleared but not populated from mutation failure. A rejected first unlock may
therefore lack an inline explanation.

### Structured API errors lose status and code

`extractApiError` returns only a string. For a detail object it prioritizes
message/reason/error/detail and selected failure collections. HTTP status,
machine code, headers, retry delay, and other structured fields do not survive
as programmatic data.

### Backend and frontend safety strings can drift

`setPermissionMode(mode: string)`, several `Record<string, any>` risk responses,
and generic post methods allow invalid or stale literals to compile. The backend
still rejects bad authority changes, but the frontend cannot exhaustively render
all states.

### Remote token lives in localStorage

The helper avoids echoing the token and handles unavailable storage safely, but
localStorage remains readable by same-origin JavaScript. This is an explicit XSS
threat boundary and should be documented wherever remote browser access is
enabled. No token value was read during this study.

### Contract types are split across several authorities

API-local interfaces, `frontend/src/types.ts`, feature-local types, operator-shell
schemas, and Python models can describe overlapping concepts. Without generation
or contract comparison, a rename can update one surface while leaving another
apparently type-safe.

## Invariants That Must Remain

- Backend risk, execution, permission, confirmation, and audit gates remain
  authoritative regardless of frontend types.
- Live unlock fails closed on missing confirmation, readiness, or required audit.
- Authority-reducing actions retain intentionally safer failure semantics.
- Secrets are never returned in configuration/status responses or error bodies.
- Validation responses do not echo rejected secret input.
- `not available`, `not configured`, `unsupported`, and `permission denied`
  remain distinct facts.
- Type generation or runtime validation must not broaden endpoint authority or
  make dynamic third-party payloads appear trustworthy.
