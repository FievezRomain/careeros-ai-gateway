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
and refuses readiness/invocation until V1-021 supplies an adapter. V1-022 owns
timeouts, quotas, retries, fallback selection, cost metadata and durable
idempotent replay. Consumers still validate generated output deterministically
against their own verified facts and rules.

## Example

The matching service calls a stable capability such as `extract-job-requirements`. The gateway may route it to an OpenAI model, Claude or a local model according to configuration, privacy, cost and availability, then normalize the validated response. Provider-specific features remain explicit; the gateway does not pretend all models are identical.

## Contract rules

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
configuration is validated through Pydantic settings; OIDC issuer and audience
are required and provider secrets remain outside the repository. Token
verification is an injected boundary adapter; the safe default rejects every
token until the deployment supplies its configured verifier.
