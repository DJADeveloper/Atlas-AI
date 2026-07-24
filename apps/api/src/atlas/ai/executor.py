"""Resilient execution over the provider port (docs/20 §5).

Walks the route plan's chain: per rung, retry retryable errors with
full-jitter backoff (2 retries; MalformedResponse exactly 1), consult
the circuit breaker before dialing, then fall to the next rung. Chain
exhaustion raises ModelUnavailable. Any response served by a non-primary
rung is marked degraded — silent substitution would violate "no magic".

Local-only defense in depth (docs/20 §4.2): even if configuration drift
ever produced a cloud rung in local-only profile, the executor fails
closed before dispatch rather than leaking a prompt.

Streaming falls back only while the failure provably happened BEFORE
any response bytes (docs/20 §5.2): once deltas flowed, a retry could
tell a different story, so mid-stream failures surface as typed error
events and the caller decides.
"""

import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from atlas.ai.breaker import BreakerBoard
from atlas.ai.routing import ModelRef, Profile, RoutePlan
from atlas.domain.ai.errors import (
    MalformedResponse,
    ModelUnavailable,
    ProviderError,
    RateLimited,
)
from atlas.domain.ai.provider import ChatEvent, ChatRequest, ChatResponse, LLMProvider

MAX_RETRIES_PER_RUNG = 2
MALFORMED_RETRIES = 1
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    response: ChatResponse
    served_by: ModelRef
    degraded: bool  # a non-primary rung answered (docs/20 §5.4: visible)


class ResilientExecutor:
    def __init__(
        self,
        providers: dict[str, LLMProvider],
        breakers: BreakerBoard,
        *,
        sleep: Callable[[float], Awaitable[None]],
        rng: random.Random | None = None,
    ) -> None:
        self._providers = providers
        self._breakers = breakers
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()  # jitter, not secrets

    def _provider_for(self, rung: ModelRef, profile: Profile) -> LLMProvider:
        provider = self._providers.get(rung.provider)
        if provider is None:
            raise ModelUnavailable(f"no adapter registered for provider {rung.provider!r}")
        if profile == "local_only" and not provider.is_local:
            # Fail closed: configuration drift must not leak a prompt.
            raise ModelUnavailable(
                f"local-only profile refused cloud provider {rung.provider!r}",
                context={"model": rung.model},
            )
        return provider

    def _backoff_seconds(self, attempt: int, error: ProviderError) -> float:
        if isinstance(error, RateLimited) and error.retry_after_seconds is not None:
            return error.retry_after_seconds
        return self._rng.uniform(0.0, min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 4**attempt))

    def _retry_budget(self, error: ProviderError) -> int:
        if isinstance(error, MalformedResponse):
            return MALFORMED_RETRIES
        return MAX_RETRIES_PER_RUNG if error.retryable else 0

    async def complete(
        self, plan: RoutePlan, request: ChatRequest, *, profile: Profile
    ) -> ExecutionResult:
        last_error: ProviderError | None = None
        for index, rung in enumerate(plan.chain):
            breaker = self._breakers.for_model(rung.provider, rung.model)
            if not breaker.allows():
                continue
            provider = self._provider_for(rung, profile)
            attempt = 0
            while True:
                try:
                    response = await provider.complete(rung.model, request)
                except ProviderError as error:
                    breaker.record_failure()
                    last_error = error
                    if attempt >= self._retry_budget(error):
                        break  # rung exhausted → next rung
                    await self._sleep(self._backoff_seconds(attempt, error))
                    attempt += 1
                else:
                    breaker.record_success()
                    return ExecutionResult(response, served_by=rung, degraded=index > 0)
        raise ModelUnavailable(
            "every model in the route plan failed or is circuit-open",
            context={
                "role": plan.role,
                "last_error": type(last_error).__name__ if last_error else None,
            },
        )

    async def start_stream(
        self, plan: RoutePlan, request: ChatRequest, *, profile: Profile
    ) -> tuple[ModelRef, bool, AsyncIterator[ChatEvent]]:
        """Open a stream on the first rung that produces an event;
        returns (rung, degraded, events). Pre-first-byte failures walk
        the chain safely (docs/20 §5.2: no bytes = no decision was
        made); after that, failures belong to the returned iterator."""
        last_error: ProviderError | None = None
        for index, rung in enumerate(plan.chain):
            breaker = self._breakers.for_model(rung.provider, rung.model)
            if not breaker.allows():
                continue
            provider = self._provider_for(rung, profile)
            events = provider.stream(rung.model, request)
            try:
                first = await anext(events)
            except ProviderError as error:
                breaker.record_failure()
                last_error = error
                continue  # no bytes flowed: falling back is safe (§5.2)
            except StopAsyncIteration:
                breaker.record_failure()
                last_error = MalformedResponse("stream ended before any event")
                continue
            breaker.record_success()  # a first event = the rung is alive
            return rung, index > 0, _prepend(first, events)
        raise ModelUnavailable(
            "every model in the route plan failed or is circuit-open",
            context={
                "role": plan.role,
                "last_error": type(last_error).__name__ if last_error else None,
            },
        )


async def _prepend(first: ChatEvent, rest: AsyncIterator[ChatEvent]) -> AsyncIterator[ChatEvent]:
    yield first
    async for event in rest:
        yield event
