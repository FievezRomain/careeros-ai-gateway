from types import SimpleNamespace
from typing import cast

import pytest

from careeros_ai_gateway.adapters.openai import OpenAIResponsesGateway
from careeros_ai_gateway.application.capability import (
    CapabilityInputInvalid,
    CapabilityInvocation,
    ProviderResponseInvalid,
)


class FakeResponses:
    def __init__(self, output: object, status: str = "completed") -> None:
        self.output = output
        self.status = status
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            model="synthetic-model-v1", status=self.status, output_parsed=self.output
        )


def invocation(input_payload: dict[str, object]) -> CapabilityInvocation:
    return CapabilityInvocation(
        capability_version="1.0", input_schema_version="1.0", input=input_payload
    )


def test_validates_redacts_and_revalidates_structured_output() -> None:
    responses = FakeResponses(
        {
            "requirements": [
                {
                    "category": "skill",
                    "description": "Python",
                    "required": True,
                    "evidence": ["Strong Python skills"],
                }
            ]
        }
    )
    gateway = OpenAIResponsesGateway(responses, "synthetic-model")
    result = gateway.invoke(
        "extract-job-requirements",
        invocation(
            {
                "jobText": (
                    "Strong Python skills. Contact person@example.invalid, +1 202 555 0100, "
                    "or https://jobs.example.invalid/role. Salary 120000-150000."
                ),
                "sourceLanguage": "en",
            }
        ),
        "synthetic-request-001",
    )

    call = responses.calls[0]
    messages = cast(list[dict[str, str]], call["input"])
    sent_text = messages[1]["content"]
    assert "person@example.invalid" not in sent_text
    assert "202 555 0100" not in sent_text
    assert "https://" not in sent_text
    assert "120000-150000" in sent_text
    assert all(marker in sent_text for marker in ("[EMAIL]", "[PHONE]", "[URL]"))
    assert call["store"] is False
    assert call["extra_headers"] == {"Idempotency-Key": "synthetic-request-001"}
    assert result.provenance.model_version == "synthetic-model-v1"
    output = cast(dict[str, object], result.output)
    requirements = cast(list[dict[str, object]], output["requirements"])
    assert requirements[0]["description"] == "Python"


def test_rejects_invalid_capability_input_before_provider_call() -> None:
    responses = FakeResponses({"requirements": []})
    gateway = OpenAIResponsesGateway(responses, "synthetic-model")

    with pytest.raises(CapabilityInputInvalid):
        gateway.invoke(
            "extract-job-requirements",
            invocation({"jobText": "", "sourceLanguage": "invalid"}),
            "synthetic-request-002",
        )

    assert responses.calls == []


@pytest.mark.parametrize(
    "output",
    [None, {"requirements": [{"category": "skill", "description": "Python"}]}],
)
def test_rejects_missing_or_invalid_provider_output(output: object) -> None:
    gateway = OpenAIResponsesGateway(FakeResponses(output), "synthetic-model")

    with pytest.raises(ProviderResponseInvalid):
        gateway.invoke(
            "extract-job-requirements",
            invocation({"jobText": "Python required", "sourceLanguage": "en"}),
            "synthetic-request-003",
        )


def test_rejects_incomplete_provider_response() -> None:
    responses = FakeResponses({"requirements": []}, status="incomplete")
    gateway = OpenAIResponsesGateway(responses, "synthetic-model")

    with pytest.raises(ProviderResponseInvalid):
        gateway.invoke(
            "extract-job-requirements",
            invocation({"jobText": "Python required", "sourceLanguage": "en"}),
            "synthetic-request-004",
        )
