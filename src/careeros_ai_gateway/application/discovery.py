"""Provider-neutral query expansion and validation of search evidence."""

import re
import unicodedata
from typing import Annotated, Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, StringConstraints, ValidationError, field_validator

from careeros_ai_gateway.application.capability import (
    CapabilityInputInvalid,
    CapabilityInvocation,
    CapabilityProvenance,
    ContractModel,
    ProviderOutput,
    ProviderResponseInvalid,
)

DISCOVER = "discover-job-links"
PROMPT_VERSION = "job-search-queries-v1"
Term = Annotated[str, StringConstraints(min_length=1, max_length=60)]


class SearchInput(ContractModel):
    target_roles: tuple[Term, ...] = Field(min_length=1, max_length=5)
    locations: tuple[Term, ...] = Field(min_length=1, max_length=5)
    remote_policy: Literal["any", "remote", "hybrid", "onsite"]
    contract_types: tuple[Term, ...] = Field(min_length=1, max_length=5)

    @field_validator("target_roles", "locations", "contract_types")
    @classmethod
    def normalize_terms(cls, terms: tuple[str, ...]) -> tuple[str, ...]:
        values = {
            " ".join(unicodedata.normalize("NFKC", term).casefold().split()) for term in terms
        }
        if any(
            not re.fullmatch(r"[\w .+#'’-]+", value)
            or not re.search(r"[^\W\d_]", value)
            or re.search(r"\d(?:[ .-]*\d){6}", value)
            for value in values
        ):
            raise ValueError("expected short search terms without contact details")
        return tuple(sorted(values))


class ExpandedQueries(ContractModel):
    queries: tuple[Annotated[str, StringConstraints(min_length=1, max_length=180)], ...] = Field(
        min_length=1, max_length=3
    )


class WebResult(ContractModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    snippet: str = Field(max_length=4000)
    score: float = Field(ge=0, le=1)
    source: str = Field(min_length=1, max_length=60)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
            raise ValueError("invalid result URL")
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme != "https" or url.username is not None or url.port not in (None, 443):
            raise ValueError("expected public HTTPS URL")
        if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*", host) or host.endswith(
            (".local", ".localhost", ".internal")
        ):
            raise ValueError("expected DNS hostname")
        params = [
            (k, v)
            for k, v in parse_qsl(url.query, keep_blank_values=True)
            if not k.casefold().startswith("utm_")
        ]
        return urlunsplit(("https", host, url.path or "/", urlencode(params), ""))


class WebSearchResult(ContractModel):
    results: tuple[WebResult, ...] = Field(max_length=15)
    search_requests: int = Field(ge=0, le=3)


class QueryExpander(Protocol):
    def expand(
        self, signature: SearchInput, key: str
    ) -> tuple[ExpandedQueries, CapabilityProvenance]: ...


class WebSearch(Protocol):
    def search(self, query: str) -> tuple[WebResult, ...]: ...


class DiscoverJobLinks:
    def __init__(self, expander: QueryExpander, search: WebSearch) -> None:
        self._expander = expander
        self._search = search

    def ready(self) -> bool:
        return True

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput:
        try:
            signature = SearchInput.model_validate(invocation.input)
        except ValidationError:
            raise CapabilityInputInvalid from None
        expansion, provenance = self._expander.expand(signature, idempotency_key)
        allowed = set(
            " ".join(
                (
                    *signature.target_roles,
                    *signature.locations,
                    *signature.contract_types,
                    signature.remote_policy,
                )
            ).split()
        )
        allowed.update(
            {"job", "jobs", "hiring", "career", "careers", "emploi", "recrutement", "in", "à"}
        )
        queries = {" ".join(query.casefold().split()) for query in expansion.queries}
        usable = sorted(
            query
            for query in queries
            if set(query.split()) <= allowed
            and any(role in query for role in signature.target_roles)
        )
        if not usable:
            raise ProviderResponseInvalid
        results: dict[str, WebResult] = {}
        for query in usable:
            for item in self._search.search(query):
                if item.url not in results or item.score > results[item.url].score:
                    results[item.url] = item
        output = WebSearchResult(results=tuple(results.values())[:15], search_requests=len(usable))
        return ProviderOutput(
            output_schema_version="1.0",
            output=output.model_dump(mode="json"),
            provenance=provenance,
        )
