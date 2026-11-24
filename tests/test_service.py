import json
import logging
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import ValidationError

from careeros_ai_gateway.adapters.http import Settings, create_app
from careeros_ai_gateway.application.capability import (
    AccountContext,
    CapabilityInvocation,
    CapabilityProvenance,
    ProviderOutput,
)


class FakeGateway:
    def __init__(self) -> None:
        self.calls = 0

    def ready(self) -> bool:
        return True

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput:
        self.calls += 1
        return ProviderOutput(
            output_schema_version="1.0",
            output={"requirements": ["Python"]},
            provenance=CapabilityProvenance(model_version="fake-v1", prompt_version="extract-v1"),
        )


class FakeVerifier:
    def verify(self, token: str) -> AccountContext:
        if token not in {"allowed", "forbidden"}:
            raise ValueError
        return AccountContext(
            account_id=UUID(int=1),
            tenant_id=UUID(int=2),
            scopes=("ai:invoke",) if token == "allowed" else (),
        )


def client() -> TestClient:
    settings = Settings(
        app_env="test",
        log_level="info",
        oidc_issuer="https://identity.example.invalid/realms/careeros",
        oidc_audience="careeros-api",
    )
    return TestClient(create_app(settings, FakeGateway(), FakeVerifier()))


def request(token: str = "allowed", **body: object) -> Response:
    payload: dict[str, object] = {
        "capabilityVersion": "1.0",
        "inputSchemaVersion": "1.0",
        "input": {"jobText": "Synthetic role", "sourceLanguage": "en"},
    }
    payload.update(body)
    return client().post(
        "/v1/capabilities/extract-job-requirements/invocations",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "synthetic-request-001"},
        json=payload,
    )


def test_health_and_openapi_contract() -> None:
    with client() as api:
        assert api.get("/health/live").json()["status"] == "ok"
        assert api.get("/health/ready").json()["status"] == "ready"
        operation = api.get("/openapi.json").json()["paths"][
            "/v1/capabilities/{capability_id}/invocations"
        ]["post"]
    assert operation["security"]
    assert "429" in operation["responses"]


def test_provider_neutral_invocation_and_redacted_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="careeros.ai_gateway.http"):
        response = request()
    assert response.status_code == 200
    assert response.json()["output"] == {"requirements": ["Python"]}
    provenance = response.json()["provenance"]
    assert provenance == {
        "modelVersion": "fake-v1",
        "promptVersion": "extract-v1",
        "providerAttempts": 1,
        "latencyMs": provenance["latencyMs"],
        "inputUnits": None,
        "outputUnits": None,
    }
    assert provenance["latencyMs"] >= 0
    assert "Synthetic role" not in caplog.text
    assert json.loads(caplog.records[-1].message)["path"].endswith("/invocations")


@pytest.mark.parametrize(("token", "status"), [("missing", 401), ("forbidden", 403)])
def test_denies_untrusted_or_unauthorized_callers(token: str, status: int) -> None:
    response = request(token)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")


def test_rejects_invalid_boundary_input_and_configuration() -> None:
    response = request(extra="rejected")
    assert response.status_code == 422
    missing_key = client().post(
        "/v1/capabilities/extract-job-requirements/invocations",
        headers={"Authorization": "Bearer allowed"},
        json={"capabilityVersion": "1.0", "inputSchemaVersion": "1.0", "input": {"x": 1}},
    )
    assert missing_key.status_code == 400
    with pytest.raises(ValidationError):
        Settings.model_validate({"app_env": "production", "log_level": "verbose"})
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            log_level="info",
            oidc_issuer="https://identity.example.invalid/realms/careeros",
            oidc_audience="careeros-api",
            openai_api_key="synthetic-provider-key",
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            log_level="info",
            oidc_issuer="https://identity.example.invalid/realms/careeros",
            oidc_audience="careeros-api",
            openai_fallback_model="synthetic-fallback",
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            log_level="info",
            oidc_issuer="https://identity.example.invalid/realms/careeros",
            oidc_audience="careeros-api",
            ai_timeout_seconds=0,
        )
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            log_level="info",
            oidc_issuer="https://identity.example.invalid/realms/careeros",
            oidc_audience="careeros-api",
            openai_api_key="synthetic-provider-key",
            openai_model="same-model",
            openai_fallback_model="same-model",
        )


def test_configured_provider_is_ready_without_exposing_secret() -> None:
    settings = Settings(
        app_env="test",
        log_level="error",
        oidc_issuer="https://identity.example.invalid/realms/careeros",
        oidc_audience="careeros-api",
        openai_api_key="synthetic-provider-key",
        openai_model="synthetic-model",
    )

    with TestClient(create_app(settings, token_verifier=FakeVerifier())) as api:
        response = api.get("/health/ready")

    assert response.json()["status"] == "ready"
    assert "synthetic-provider-key" not in repr(settings)


def test_quota_returns_retry_after_without_reinvoking_provider() -> None:
    settings = Settings(
        app_env="test",
        log_level="error",
        oidc_issuer="https://identity.example.invalid/realms/careeros",
        oidc_audience="careeros-api",
        ai_quota_per_minute=1,
    )
    gateway = FakeGateway()
    payload = {
        "capabilityVersion": "1.0",
        "inputSchemaVersion": "1.0",
        "input": {"jobText": "Synthetic role", "sourceLanguage": "en"},
    }

    with TestClient(create_app(settings, gateway, FakeVerifier())) as api:
        first = api.post(
            "/v1/capabilities/extract-job-requirements/invocations",
            headers={"Authorization": "Bearer allowed", "Idempotency-Key": "synthetic-request-101"},
            json=payload,
        )
        limited = api.post(
            "/v1/capabilities/extract-job-requirements/invocations",
            headers={"Authorization": "Bearer allowed", "Idempotency-Key": "synthetic-request-102"},
            json=payload,
        )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.json()["code"] == "QUOTA_EXCEEDED"
    assert gateway.calls == 1
