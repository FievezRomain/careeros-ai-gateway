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
    def ready(self) -> bool:
        return True

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput:
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
        "input": {"jobText": "Synthetic role"},
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


def test_provider_neutral_invocation_and_redacted_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="careeros.ai_gateway.http"):
        response = request()
    assert response.status_code == 200
    assert response.json()["output"] == {"requirements": ["Python"]}
    assert response.json()["provenance"] == {
        "modelVersion": "fake-v1",
        "promptVersion": "extract-v1",
    }
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
