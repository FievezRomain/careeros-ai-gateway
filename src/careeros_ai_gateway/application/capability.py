"""Provider-neutral capability contract, port and invocation use case."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class AccountContext(ContractModel):
    account_id: UUID
    tenant_id: UUID
    scopes: tuple[str, ...] = ()


class CapabilityInvocation(ContractModel):
    capability_version: str = Field(pattern=r"^\d+\.\d+$")
    input_schema_version: str = Field(pattern=r"^\d+\.\d+$")
    input: dict[str, JsonValue] = Field(min_length=1)


class ExtractJobRequirementsInput(ContractModel):
    job_text: str = Field(min_length=1, max_length=30_000)
    source_language: str = Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")


class ExtractedRequirement(ContractModel):
    category: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=500)
    required: bool
    evidence: tuple[str, ...] = Field(max_length=5)


class ExtractJobRequirementsOutput(ContractModel):
    requirements: tuple[ExtractedRequirement, ...] = Field(max_length=100)


class CapabilityProvenance(ContractModel):
    model_version: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=100)


class ProviderOutput(ContractModel):
    output_schema_version: str = Field(pattern=r"^\d+\.\d+$")
    output: dict[str, JsonValue]
    provenance: CapabilityProvenance


class CapabilityResult(ProviderOutput):
    capability_id: str
    capability_version: str


class CapabilityGateway(Protocol):
    def ready(self) -> bool: ...

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput: ...


class PermissionDenied(Exception):
    pass


class UnsupportedCapability(Exception):
    pass


class CapabilityUnavailable(Exception):
    pass


class CapabilityInputInvalid(Exception):
    pass


class ProviderResponseInvalid(Exception):
    pass


class InvokeCapability:
    def __init__(self, gateway: CapabilityGateway, versions: Mapping[str, str]) -> None:
        self._gateway = gateway
        self._versions = versions

    def execute(
        self,
        account: AccountContext,
        capability_id: str,
        invocation: CapabilityInvocation,
        idempotency_key: str,
    ) -> CapabilityResult:
        if "ai:invoke" not in account.scopes:
            raise PermissionDenied
        version = self._versions.get(capability_id)
        if (
            version is None
            or invocation.capability_version != version
            or invocation.input_schema_version != version
        ):
            raise UnsupportedCapability
        output = self._gateway.invoke(capability_id, invocation, idempotency_key)
        if output.output_schema_version != version:
            raise CapabilityUnavailable
        return CapabilityResult(
            capability_id=capability_id,
            capability_version=version,
            **output.model_dump(),
        )
