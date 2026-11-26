import json

import httpx
import pytest
from pydantic import ValidationError

from careeros_ai_gateway.adapters.discovery import TavilySearch
from careeros_ai_gateway.application.discovery import SearchInput


def test_tavily_uses_raw_results_and_caches_bounded_queries() -> None:
    calls: list[httpx.Request] = []

    def search(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "answer": "Discard generated answers",
                "results": [
                    {
                        "url": "https://jobs.example.invalid/1?utm_source=test&job=1",
                        "title": "Platform engineer",
                        "content": "Source wording",
                        "score": 0.7,
                    },
                    {
                        "url": "http://localhost/private",
                        "title": "Discard",
                        "content": "",
                        "score": 1,
                    },
                ],
            },
        )

    now = [0.0]
    with httpx.Client(transport=httpx.MockTransport(search)) as client:
        gateway = TavilySearch(client, "synthetic", lambda: now[0])
        first = gateway.search("platform engineer paris jobs")
        assert gateway.search("platform engineer paris jobs") == first
        assert len(calls) == 1
        now[0] = 301
        assert gateway.search("platform engineer paris jobs") == first
    assert len(calls) == 2
    assert [item.url for item in first] == ["https://jobs.example.invalid/1?job=1"]
    body = json.loads(calls[0].content)
    assert body["max_results"] == 5
    assert body["include_answer"] is False


@pytest.mark.parametrize(
    "bad_role", ["engineer test@example.invalid", "engineer +33 1 23 45 67 89"]
)
def test_signature_rejects_contact_details(bad_role: str) -> None:
    with pytest.raises(ValidationError):
        SearchInput(
            target_roles=(bad_role,),
            locations=("Paris",),
            remote_policy="any",
            contract_types=("CDI",),
        )
