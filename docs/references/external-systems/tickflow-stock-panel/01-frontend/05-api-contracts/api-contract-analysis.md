# TickFlow API and Frontend-Contract Analysis

## Finding

TickFlow has a coherent single request path, strong backend capability authority,
and a thoughtful self-hosted authentication state machine. Its frontend types
are unusually broad for a manually maintained client and use little `any`.

However, the apparent type coverage is compile-time assertion rather than a
closed contract: the backend declares almost no response models, TypeScript is
not generated, success JSON is not validated, and every error is reduced to a
plain message. Machine status, error code, recovery metadata, and capability
suggestions are therefore lost at the most important boundary.

## 1. One Request Helper Standardizes Basic Transport

`request<T>` consistently:

- prefixes the API base;
- sets JSON content type unless the body is FormData;
- merges caller headers instead of replacing them;
- extracts FastAPI `detail` or a fallback `message`;
- flattens 422 validation arrays into readable messages;
- suppresses 401 toasts so the global auth redirect owns that experience;
- supports a `quiet` escape for call sites that handle errors locally.

This prevents every page from inventing fetch and error boilerplate.

### Missing transport semantics

- There is no request timeout or caller cancellation integration.
- The success path assumes every response has a JSON body.
- The generic `T` is a compile-time cast; no runtime decoder verifies it.
- The thrown Error does not carry HTTP status, headers, body, code, retry delay,
  field errors, or structured details.
- Error toast presentation is coupled to the transport layer.

## 2. The API Facade Is Typed but Monolithic

The 2,534-line file contains domain interfaces and endpoint methods for auth,
settings, providers, data, K-lines, watchlists, screeners, backtests, pipelines,
extensions, financials, AI reports, strategies, monitoring, and alerts.

Using named interfaces and exact inline response shapes gives page authors
useful editor feedback. Only one explicit `request<any>` appears. This is much
better than passing untyped JSON through every component.

The cost is ownership concentration:

- unrelated domains change the same file;
- large inline types are hard to compare with Python producers;
- frontend modules cannot version or test their contracts independently;
- the 77 CodeGraph consumers depend on one object;
- backend route modularity is not reflected at the client boundary.

## 3. Input Validation Is Stronger Than Output Declaration

Backend routes frequently use Pydantic BaseModel and Field constraints for
passwords, settings, monitor rules, backtests, signals, and other mutation
inputs. FastAPI therefore rejects many malformed requests before domain logic.

But bounded inventory found no `response_model=` declarations across roughly
209 API route decorators. Most functions annotate `dict` or rely on inferred
serialization. As a result:

- OpenAPI cannot describe the real output shape precisely;
- extra/private fields are not centrally filtered by an output model;
- missing or renamed response fields do not fail at the backend boundary;
- handwritten TypeScript interfaces have no mechanical source of truth.

## 4. Capabilities Are Authoritative Backend Facts

`CapabilitySet` is a backend domain object keyed by capability enum and carrying
RPM, batch, and subscription limits. Business code calls `require`; missing
capability raises `CapabilityDenied`; one global handler converts that to 403.
Custom data sources can grant a supported capability without pretending the
user purchased a TickFlow tier.

The frontend receives a flexible map and uses it to hide/disable expensive
features and apply batch limits. This is a good separation:

- the UI can explain availability and cost;
- the backend still enforces access;
- provider substitution is expressed as capability, not branding;
- limits are data, not hardcoded only in components.

### Contract loss

The denial handler returns both `detail` and `suggestion`, but the request helper
reads only detail/message. The structured suggestion never reaches the caller.
The frontend capability keys are generic strings rather than a shared/generated
union, so spelling drift remains possible.

## 5. Authentication Models Initialization Explicitly

The middleware distinguishes uninitialized local access, uninitialized public
access, configured authenticated access, and configured unauthenticated access.
This prevents a public caller from claiming the first password while allowing a
self-hosted owner to bootstrap locally.

Other valuable behavior includes:

- bounded password length validation;
- login failure rate limiting;
- HttpOnly, SameSite=Lax session cookie;
- revocation of all sessions after password change;
- a public health/auth bootstrap surface;
- redirect preservation after session expiry.

### Weak points

- The cookie has `secure=False` to support non-HTTPS self-hosting; confidentiality
  therefore depends on deployment transport.
- The frontend auth-status request is page-local rather than a typed shared
  query and catches every failure as `{configured:false}`. Network or server
  failure can be misrepresented as first-time setup.
- The global redirect recognizes authentication by matching message strings.
- Public initialization returns a machine code, but the request helper discards
  that code.

## 6. Machine Errors Collapse Into Human Text

The uninitialized middleware returns:

```text
status: 403
detail: human guidance
code: NOT_INITIALIZED
```

The capability handler returns:

```text
status: 403
detail: denial text
suggestion: upgrade or configuration guidance
```

Both become `new Error(detail)`. The status, code, and suggestion disappear.
The global auth redirect contains a check for `NOT_INITIALIZED`, but that token
is not included in the generated message unless the human detail happens to
contain it. The wording check currently provides the effective behavior.

This makes localization, retry behavior, telemetry grouping, field placement,
and future wording changes unnecessarily risky.

## 7. Settings Responses Preserve Some Trust Boundaries

Settings use masked keys and boolean presence flags rather than returning raw
provider credentials. Capability probe logs, missing/extra capabilities, tier,
endpoint source, plugin load errors, and data-source test previews are explicit
fields. This supports useful configuration diagnosis without exposing secrets.

The flexible extension/data-source models use `Record<string, unknown>` in
preview data where schemas are intentionally dynamic. That is an appropriate
use of open data, unlike using `any` for stable control responses.

## 8. Confirmation Is Mostly Backend-Enforced

Examples such as clearing minute data require `confirm:true` at the backend.
Authentication setup is constrained by network origin, and capability checks
are repeated server-side. This avoids trusting a disabled button as authority.

The error response remains a human string, so the UI cannot reliably distinguish
missing confirmation from invalid input without endpoint-specific message
knowledge.

## 9. No Generated or Runtime-Validated Contract Was Found

The project currently does not use OpenAPI TypeScript generation, a response
schema library, or response-model-driven output for the main API. This is not
automatically wrong for a local MVP, but manual surface area has grown large
enough that compiler confidence exceeds actual cross-language proof.

No frontend tests were found for the request helper, Auth, capability response,
or representative endpoint decoding.
