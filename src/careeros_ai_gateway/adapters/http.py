"""HTTP adapter and composition root for the AI gateway."""

import json
import logging
import re
from typing import Annotated, Literal, Protocol, cast
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import OpenIdConnect
from fastapi.security.utils import get_authorization_scheme_param
from openai import OpenAI
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.middleware.base import RequestResponseEndpoint

from careeros_ai_gateway.adapters.openai import OpenAIResponsesGateway, ResponsesClient
from careeros_ai_gateway.adapters.policy import FallbackGateway, FixedWindowQuota
from careeros_ai_gateway.application.capability import (
    AccountContext,
    CapabilityGateway,
    CapabilityInputInvalid,
    CapabilityInvocation,
    CapabilityResult,
    CapabilityUnavailable,
    InvokeCapability,
    PermissionDenied,
    ProviderOutput,
    ProviderResponseInvalid,
    QuotaExceeded,
    UnsupportedCapability,
)

_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_VERSIONS = {"extract-job-requirements": "1.0"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)
    app_env: Literal["development", "test", "production"]
    log_level: Literal["debug", "info", "warning", "error"]
    oidc_issuer: AnyHttpUrl
    oidc_audience: str = Field(min_length=1)
    openai_api_key: SecretStr | None = None
    openai_model: str | None = Field(default=None, min_length=1)
    openai_fallback_model: str | None = Field(default=None, min_length=1)
    ai_timeout_seconds: float = Field(default=20, gt=0, le=120)
    ai_quota_per_minute: int = Field(default=60, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> Settings:
        if (self.openai_api_key is None) != (self.openai_model is None):
            raise ValueError("OPENAI_API_KEY and OPENAI_MODEL must be configured together")
        if self.openai_fallback_model is not None and self.openai_model is None:
            raise ValueError("OPENAI_FALLBACK_MODEL requires OPENAI_MODEL")
        if self.openai_fallback_model == self.openai_model and self.openai_model is not None:
            raise ValueError("OPENAI_FALLBACK_MODEL must differ from OPENAI_MODEL")
        return self


class Problem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    correlation_id: str = Field(alias="correlationId")


class Health(BaseModel):
    service: Literal["careeros-ai-gateway"] = "careeros-ai-gateway"
    status: Literal["ok", "ready", "not_ready"]


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AccountContext: ...


class RejectingTokenVerifier:
    def verify(self, token: str) -> AccountContext:
        raise ValueError("token verifier is not configured")


class UnavailableGateway:
    def ready(self) -> bool:
        return False

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput:
        raise CapabilityUnavailable


def _error(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status, {"code": code, "detail": detail})


def create_app(
    settings: Settings | None = None,
    gateway: CapabilityGateway | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    configured = settings or Settings()
    provider: CapabilityGateway
    if gateway is not None:
        provider = FallbackGateway((gateway,))
    elif configured.openai_api_key is not None and configured.openai_model is not None:
        client = OpenAI(
            api_key=configured.openai_api_key.get_secret_value(),
            max_retries=0,
            timeout=configured.ai_timeout_seconds,
        )
        responses = cast(ResponsesClient, client.responses)
        routes: list[CapabilityGateway] = [
            OpenAIResponsesGateway(
                responses, configured.openai_model, configured.ai_timeout_seconds
            )
        ]
        if configured.openai_fallback_model is not None:
            routes.append(
                OpenAIResponsesGateway(
                    responses,
                    configured.openai_fallback_model,
                    configured.ai_timeout_seconds,
                )
            )
        provider = FallbackGateway(routes)
    else:
        provider = UnavailableGateway()
    verifier = token_verifier or RejectingTokenVerifier()
    invoke = InvokeCapability(provider, _VERSIONS, FixedWindowQuota(configured.ai_quota_per_minute))
    oidc = OpenIdConnect(
        openIdConnectUrl=f"{str(configured.oidc_issuer).rstrip('/')}/.well-known/openid-configuration",
        auto_error=False,
    )
    logger = logging.getLogger("careeros.ai_gateway.http")
    logger.setLevel(configured.log_level.upper())
    logging.getLogger("careeros.ai_gateway.provider").setLevel(configured.log_level.upper())
    app = FastAPI(title="CareerOS AI Gateway", version="1.0.0")

    @app.middleware("http")
    async def correlation(request: Request, call_next: RequestResponseEndpoint) -> Response:
        candidate = request.headers.get("X-Correlation-ID", "")
        correlation_id = (
            candidate if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", candidate) else uuid4().hex
        )
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        logger.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                },
                separators=(",", ":"),
            )
        )
        return response

    def account(
        token: Annotated[str | None, Security(oidc, scopes=["ai:invoke"])],
    ) -> AccountContext:
        scheme, credentials = get_authorization_scheme_param(token or "")
        if scheme.lower() != "bearer" or not credentials:
            raise _error(401, "UNAUTHENTICATED", "A valid bearer token is required.")
        try:
            return verifier.verify(credentials)
        except ValueError:
            raise _error(401, "UNAUTHENTICATED", "A valid bearer token is required.") from None

    @app.exception_handler(HTTPException)
    @app.exception_handler(RequestValidationError)
    async def problem(request: Request, error: Exception) -> JSONResponse:
        status = error.status_code if isinstance(error, HTTPException) else 422
        detail = error.detail if isinstance(error, HTTPException) else {}
        values = detail if isinstance(detail, dict) else {}
        code = str(values.get("code", "VALIDATION_FAILED"))
        model = Problem(
            type=f"urn:careeros:problem:{code.lower()}",
            title=code.replace("_", " ").title(),
            status=status,
            detail=str(values.get("detail", "Request validation failed.")),
            instance=f"urn:uuid:{uuid4()}",
            code=code,
            correlation_id=request.state.correlation_id,
        )
        headers = dict(error.headers or {}) if isinstance(error, HTTPException) else {}
        if status == 401:
            headers.setdefault("WWW-Authenticate", "Bearer")
        return JSONResponse(
            model.model_dump(by_alias=True),
            status_code=status,
            media_type="application/problem+json",
            headers=headers or None,
        )

    @app.get("/health/live", response_model=Health, tags=["operations"])
    def live() -> Health:
        return Health(status="ok")

    @app.get("/health/ready", response_model=Health, tags=["operations"])
    def ready(response: Response) -> Health:
        if provider.ready():
            return Health(status="ready")
        response.status_code = 503
        return Health(status="not_ready")

    @app.post(
        "/v1/capabilities/{capability_id}/invocations",
        response_model=CapabilityResult,
        response_model_by_alias=True,
        responses={429: {"model": Problem, "description": "Invocation quota exhausted."}},
        tags=["capabilities"],
    )
    def invoke_capability(
        capability_id: str,
        invocation: CapabilityInvocation,
        caller: Annotated[AccountContext, Security(account)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> CapabilityResult:
        if idempotency_key is None or not _IDEMPOTENCY.fullmatch(idempotency_key):
            raise _error(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid.")
        try:
            return invoke.execute(caller, capability_id, invocation, idempotency_key)
        except PermissionDenied:
            raise _error(403, "FORBIDDEN", "The ai:invoke scope is required.") from None
        except UnsupportedCapability:
            raise _error(
                422, "CAPABILITY_UNSUPPORTED", "Capability or version is unsupported."
            ) from None
        except CapabilityInputInvalid:
            raise _error(422, "CAPABILITY_INPUT_INVALID", "Capability input is invalid.") from None
        except ProviderResponseInvalid:
            raise _error(
                502, "PROVIDER_RESPONSE_INVALID", "Provider output failed validation."
            ) from None
        except QuotaExceeded as error:
            raise HTTPException(
                429,
                {"code": "QUOTA_EXCEEDED", "detail": "Invocation quota is exhausted."},
                headers={"Retry-After": str(error.retry_after_seconds)},
            ) from None
        except CapabilityUnavailable:
            raise _error(503, "CAPABILITY_UNAVAILABLE", "No provider route is available.") from None

    return app
