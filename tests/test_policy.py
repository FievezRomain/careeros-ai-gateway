import logging
from uuid import UUID

import pytest

from careeros_ai_gateway.adapters.policy import FallbackGateway, FixedWindowQuota
from careeros_ai_gateway.application.capability import (
    AccountContext,
    CapabilityInvocation,
    CapabilityProvenance,
    CapabilityUnavailable,
    ProviderOutput,
    ProviderResponseInvalid,
)


class FakeProvider:
    def __init__(self, failure: type[Exception] | None = None) -> None:
        self.failure = failure
        self.calls = 0
        self.last_key: str | None = None

    def ready(self) -> bool:
        return True

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput:
        self.calls += 1
        self.last_key = idempotency_key
        if self.failure is not None:
            raise self.failure
        return ProviderOutput(
            output_schema_version="1.0",
            output={"requirements": []},
            provenance=CapabilityProvenance(
                model_version="fallback-v1", prompt_version="prompt-v1"
            ),
        )


def invocation() -> CapabilityInvocation:
    return CapabilityInvocation(
        capability_version="1.0",
        input_schema_version="1.0",
        input={"jobText": "Synthetic confidential text", "sourceLanguage": "en"},
    )


def test_falls_back_only_on_unavailability_and_logs_no_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = FakeProvider(CapabilityUnavailable)
    fallback = FakeProvider()
    gateway = FallbackGateway((primary, fallback))

    with caplog.at_level(logging.INFO, logger="careeros.ai_gateway.provider"):
        result = gateway.invoke(
            "extract-job-requirements", invocation(), "synthetic-request-private"
        )

    assert result.provenance.provider_attempts == 2
    assert primary.calls == fallback.calls == 1
    assert primary.last_key != fallback.last_key
    assert len(primary.last_key or "") == 64
    assert "Synthetic confidential text" not in caplog.text
    assert "synthetic-request-private" not in caplog.text
    assert '"outcome":"unavailable"' in caplog.text


def test_does_not_mask_invalid_provider_output_with_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fallback = FakeProvider()
    gateway = FallbackGateway((FakeProvider(ProviderResponseInvalid), fallback))

    with (
        caplog.at_level(logging.INFO, logger="careeros.ai_gateway.provider"),
        pytest.raises(ProviderResponseInvalid),
    ):
        gateway.invoke("extract-job-requirements", invocation(), "synthetic-request-201")

    assert fallback.calls == 0
    assert '"outcome":"invalid"' in caplog.text


def test_quota_is_isolated_and_resets_after_window() -> None:
    now = [10.0]
    quota = FixedWindowQuota(1, window_seconds=60, clock=lambda: now[0])
    first = AccountContext(account_id=UUID(int=1), tenant_id=UUID(int=2))
    second = AccountContext(account_id=UUID(int=3), tenant_id=UUID(int=2))

    assert quota.consume(first, "extract-job-requirements") is None
    assert quota.consume(first, "extract-job-requirements") == 60
    assert quota.consume(second, "extract-job-requirements") is None
    now[0] = 70.0
    assert quota.consume(first, "extract-job-requirements") is None
