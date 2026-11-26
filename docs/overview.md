# CareerOS AI Gateway â€” service overview

## Responsibility

Centralize model-provider access, policy, redaction, quotas, fallbacks and audit metadata.

## Data ownership

Provider adapters, prompt/model versions and non-sensitive inference metadata.

## Interfaces

- `POST /v1/capabilities/{capabilityId}/invocations` accepts a versioned,
  provider-neutral envelope from an authenticated service with `ai:invoke`.
  `Idempotency-Key` is mandatory and contains no user or resource data.
- Inputs and outputs identify their capability schema version. Responses expose
  only an opaque model version and prompt version, never provider credentials or
  raw provider types.
- Dependencies: configured model providers through secret-backed adapters.

V1-020 defines the core-owned `CapabilityGateway` port and constructor-injects
it into the invocation use case. The default composition has no provider route
and refuses readiness/invocation until a provider adapter is configured.
Consumers still validate generated output deterministically against their own
verified facts and rules.

## Example

The matching service calls a stable capability such as `extract-job-requirements`. The gateway may route it to an OpenAI model, Claude or a local model according to configuration, privacy, cost and availability, then normalize the validated response. Provider-specific features remain explicit; the gateway does not pretend all models are identical.

## V1-021 OpenAI adapter

The first provider adapter implements `extract-job-requirements` through the
OpenAI Responses API while preserving the provider-neutral application port.

- Pydantic models reject malformed or unknown capability input before a call
  and revalidate the parsed provider output afterward.
- Email addresses, phone numbers and URLs are deterministically replaced before
  job text leaves CareerOS. Full profiles and contact data are never inputs.
- Job text is treated as untrusted data. The evidence-only prompt is recorded as
  `extract-job-requirements-v1` in response provenance.
- Structured Outputs use the CareerOS output model. `store=false` disables
  response storage, the idempotency key is forwarded and SDK retries are
  disabled until V1-022 defines policy.
- Missing credentials keep the gateway not ready. API keys use `SecretStr`, are
  never serialized and belong in an environment secret store.

Generated requirements are not authoritative. Consumers still apply
deterministic rules and verified evidence. CI uses an injected fake Responses
boundary and never contacts a live provider.

## V1-022 execution policy

- `AI_TIMEOUT_SECONDS` bounds provider calls above 0 and up to 120 seconds (20 by
  default). SDK retries stay disabled so fallback remains explicit and testable.
- `AI_QUOTA_PER_MINUTE` limits calls per tenant, account and capability. Exhaustion
  returns `429 QUOTA_EXCEEDED` with `Retry-After` without calling a provider.
- `OPENAI_FALLBACK_MODEL` selects one optional secondary route, used only after
  unavailability or timeout and never after invalid structured output.
- Provenance reports route attempts, total latency and provider usage units for
  cost attribution; these are not a monetary price or billing guarantee.
- Logs contain only capability, route slot, outcome and elapsed time, never payloads,
  identities, credentials or idempotency keys.

The V1 quota is process-local; horizontal scale requires a shared adapter. Routes use
stable derived idempotency keys. This stateless service does not claim durable replay.

## Contract rules

`discover-job-links` version `1.0` accepts up to five normalized values each for
`targetRoles`, `locations` and `contractTypes`, plus `remotePolicy`. The model
uses structured output and exact signature terms to compose up to three distinct
queries. `TAVILY_API_KEY` enables raw search. Requests use basic search with up to
five hits per query and generated answers disabled. The gateway validates result
URLs and metadata, deduplicates equivalent links and caches each successful query
for five minutes in memory. `searchRequests` reports logical queries, including
cache hits. Model token usage and query counts support cost attribution. Quotas and
logging follow V1-022, and retrieval expires after at most five seconds of HTTP
inactivity per query. Retry a failed discovery through the scheduled discovery command.

- APIs and events are versioned.
- Consumers must be idempotent where retries are possible.
- Correlation identifiers are propagated without logging sensitive payloads.
- Authorization and ownership are checked at the service boundary.

## Non-goals

This service does not absorb responsibilities owned by another CareerOS service. Cross-service workflows are coordinated through explicit contracts rather than shared databases.

## Security and privacy

Persist the minimum data required, record provenance, define retention, redact logs and use synthetic examples in this public repository.

The service is stateless in V1-020. Request and response bodies, bearer tokens,
tenant identifiers and account identifiers are excluded from logs. Startup
configuration is validated through Pydantic settings. `OIDC_JWKS_URL` enables
signature verification against the configured issuer and audience. The default
denies bearer tokens until a JWKS URL or a deployment verifier is configured.
Provider secrets remain outside the repository.
