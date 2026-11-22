# CareerOS AI Gateway

Centralize model-provider access, policy, redaction, quotas, fallbacks and audit metadata.

## Scope

- **Owns:** Provider adapters, prompt/model versions and non-sensitive inference metadata.
- **Inputs:** Validated, minimized inference requests from authorized services.
- **Outputs:** Schema-validated model responses with provenance.
- **Dependencies:** configured model providers through secret-backed adapters.

## Safety baseline

This public repository must never contain secrets, real resumes, real applications or personal data. External communication and application submission always require explicit user approval.

## Documentation

See [docs/overview.md](docs/overview.md) and the central [CareerOS workspace](https://github.com/FievezRomain/careeros-workspace).

## Status

Architecture and security baseline. Functional implementation will be added incrementally with tests and versioned contracts.

## License

Apache-2.0.