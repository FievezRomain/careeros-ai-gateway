"""Provider-neutral quota and fallback policy adapters."""

import hashlib
import json
import logging
import math
import time
from collections.abc import Callable, Sequence
from threading import Lock
from uuid import UUID

from careeros_ai_gateway.application.capability import (
    AccountContext,
    CapabilityGateway,
    CapabilityInvocation,
    CapabilityUnavailable,
    ProviderOutput,
    ProviderResponseInvalid,
)


class FixedWindowQuota:
    def __init__(
        self,
        limit: int,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._windows: dict[tuple[UUID, UUID, str], tuple[float, int]] = {}
        self._lock = Lock()

    def consume(self, account: AccountContext, capability_id: str) -> int | None:
        now = self._clock()
        key = (account.tenant_id, account.account_id, capability_id)
        with self._lock:
            started, count = self._windows.get(key, (now, 0))
            if now - started >= self._window_seconds:
                started, count = now, 0
            if count >= self._limit:
                return max(1, math.ceil(self._window_seconds - (now - started)))
            self._windows[key] = (started, count + 1)
        return None


class FallbackGateway:
    def __init__(
        self,
        providers: Sequence[CapabilityGateway],
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = tuple(providers)
        self._clock = clock
        self._logger = logger or logging.getLogger("careeros.ai_gateway.provider")

    def ready(self) -> bool:
        return any(provider.ready() for provider in self._providers)

    def invoke(
        self, capability_id: str, invocation: CapabilityInvocation, idempotency_key: str
    ) -> ProviderOutput:
        started = self._clock()
        for attempt, provider in enumerate(self._providers, start=1):
            try:
                provider_key = hashlib.sha256(
                    f"{idempotency_key}:route:{attempt}".encode()
                ).hexdigest()
                output = provider.invoke(capability_id, invocation, provider_key)
            except CapabilityUnavailable:
                self._log(capability_id, attempt, "unavailable", started)
                continue
            except ProviderResponseInvalid:
                self._log(capability_id, attempt, "invalid", started)
                raise
            latency_ms = max(0, round((self._clock() - started) * 1000))
            self._log(capability_id, attempt, "success", started)
            return output.model_copy(
                update={
                    "provenance": output.provenance.model_copy(
                        update={"provider_attempts": attempt, "latency_ms": latency_ms}
                    )
                }
            )
        raise CapabilityUnavailable

    def _log(self, capability_id: str, attempt: int, outcome: str, started: float) -> None:
        self._logger.info(
            json.dumps(
                {
                    "event": "provider_attempt",
                    "capability_id": capability_id,
                    "provider_slot": attempt,
                    "outcome": outcome,
                    "elapsed_ms": max(0, round((self._clock() - started) * 1000)),
                },
                separators=(",", ":"),
            )
        )
