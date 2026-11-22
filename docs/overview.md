# CareerOS AI Gateway â€” service overview

## Responsibility

Centralize model-provider access, policy, redaction, quotas, fallbacks and audit metadata.

## Data ownership

Provider adapters, prompt/model versions and non-sensitive inference metadata.

## Interfaces

- Inputs: Validated, minimized inference requests from authorized services.
- Outputs: Schema-validated model responses with provenance.
- Dependencies: configured model providers through secret-backed adapters.

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
