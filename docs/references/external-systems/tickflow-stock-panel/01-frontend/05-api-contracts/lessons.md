# Domain 5 Lessons and Reflections

## Reuse Directly

### One transport helper, structured domain clients

Keep one implementation for base URL, headers, timeout, cancellation, parsing,
and errors. Expose several small domain clients and named contract modules rather
than one multi-thousand-line object.

### Backend capability facts with limits

Represent optional analytical availability and RPM/batch/subscription limits as
backend facts. Let the frontend explain or avoid unavailable work, while the
backend repeats enforcement.

### Local-only first-time ownership setup

For a self-hosted password flow, prevent a public caller from claiming an
uninitialized instance. Distinguish bootstrap, authenticated, unauthenticated,
and not-initialized states explicitly.

### Masked credential status

Return presence, last-four/masked display, provider/profile identity, and test
status; never return the stored credential.

### Sanitized validation errors

Retain field location, stable type, and safe message without reflecting rejected
raw input. This Aletheia practice should remain.

## Reuse After Adaptation

### Stable API error envelope

Adopt a representation such as:

```text
status
code
message
details
field_errors
retry_after
correlation_id
```

The browser should throw a typed ApiError preserving those fields. Presentation
may derive a localized message without destroying machine identity.

### Critical response models and generated types

Start with status, risk, execution, portfolio, settings, and runtime control.
Once the Python response models are authoritative, generate or compare
TypeScript types in a bounded build step. Do not pretend generation is useful
while the backend still returns unconstrained dictionaries.

### Runtime decoding at trust-critical reads

Static types cannot validate a network response. Use targeted runtime schemas
for safety status, authority, positions, and execution results, with an honest
unavailable/degraded state on decode failure. Dynamic analytical previews can
remain open `unknown` data.

### Capability versus permission taxonomy

Use capability for “the system/provider can do this,” permission for “this actor
may do this,” configuration for “it is enabled,” and health for “it works now.”
Never collapse them into one boolean.

### Confirmation types

Represent destructive and authority-changing request bodies as named input
types. The UI confirmation component should construct the full required body,
and contract evidence should verify that every mandatory acknowledgement can be
sent.

## Study Further

- Trace and repair the Live first-unlock acknowledgement mismatch only after an
  implementation request; include inline error presentation and existing live
  safety specifications in that work.
- Inventory stable Aletheia `any` responses and rank them by safety/financial
  consequence before typing them.
- Compare SystemStatus and critical route response fields against their Python
  producers with a small schema audit rather than broad test execution.
- Decide whether the remote-control browser token needs session-scoped storage,
  a bootstrap exchange, stronger CSP, or another XSS mitigation.
- Define a stable error-code namespace and map existing structured detail
  dictionaries into it without losing backward-compatible messages.
- Determine how operator-shell schemas and dashboard contracts can share
  generated or checked definitions.
- Review HTTP 204, streaming, file download, and non-JSON response needs before
  generalizing the transport helper.

## Do Not Reuse

### Human-message routing

Do not redirect authentication, choose recovery, or identify permission failure
by matching translated message text when status and code can be preserved.

### Generic success casts as proof

`fetchJson<T>` and `request<T>` document developer expectation; they do not prove
the server returned T.

### Frontend-only capability or safety gates

Disabled controls improve UX but cannot authorize or protect a backend action.

### One giant generated migration

Do not response-model and generate hundreds of endpoints in one Alpha-detouring
rewrite. Establish the pattern on the small critical surface, then expand when
it removes real drift or risk.

### Stable control responses typed as `any`

Risk, permissions, execution, credentials status, runtime transitions, and
positions have stable meaning and should not use unrestricted dynamic types.

### Machine code hidden inside prose

Do not require callers to extract `scorecard_gate_failed` or
`agent_job_manager_unavailable` from a sentence. Preserve code and message as
separate fields.

### UI confirmation that cannot construct the backend contract

A warning wall is not evidence of confirmation if its API method omits a
required acknowledgement field.

## Durable Design Reflections

### Cross-language type safety begins at the producer

A precise TypeScript interface over an unconstrained Python dictionary is useful
documentation but weak proof. Output validation/modeling must begin where the
response is produced.

### Error identity is operational data

Status, code, retryability, field location, and correlation are needed for
recovery, telemetry, support, and safe UI behavior. Flattening them into a
sentence spends that information permanently.

### Capable, configured, permitted, and healthy are different axes

A provider may support a feature that is unconfigured; an actor may lack
permission to use a healthy feature; a configured feature may be temporarily
unavailable. UI architecture should keep all four axes visible.

### Confirmation is an end-to-end protocol

The backend requirement, request type, UI control, human text, audit event, state
transition, and failure presentation must agree. Inspecting only the dialog or
only the route cannot prove it.

### Authority reduction deserves asymmetric recovery

Aletheia's Live lock proceeds when audit writing fails, while unlock refuses to
proceed. This is a durable example of safety-oriented failure semantics that a
generic success/error envelope must preserve.

### Generated types are leverage, not governance

Generation removes transcription work. It does not choose truthful field
semantics, protect secrets, enforce backend authority, or decide recovery.

## Evidence Boundary

The comparison is static. The Live acknowledgement mismatch is proven from the
current frontend request body and backend request gate, but its browser-visible
failure was not exercised. No endpoint, compiler, generator, test, or security
probe was run.
