"""atlas/ai runtime: routing table per profile, breaker state machine,
cost arithmetic, and the resilient executor (M07; docs/20 §4-5, §8)."""

import random
from collections.abc import AsyncIterator

import pytest

from atlas.ai import (
    BreakerBoard,
    CircuitBreaker,
    CostMeter,
    ModelRates,
    ModelRouter,
    ResilientExecutor,
)
from atlas.ai.breaker import OPEN_SECONDS, BreakerState
from atlas.ai.routing import Role, RoutePlan
from atlas.domain.ai import (
    AuthFailed,
    ChatEvent,
    ChatRequest,
    ChatResponse,
    ContentRefused,
    LLMProvider,
    ModelUnavailable,
    ProviderChatMessage,
    ProviderUnavailable,
    RateLimited,
    Usage,
)

REQUEST = ChatRequest(messages=[ProviderChatMessage(role="user", content="hello")], max_tokens=64)


def _response(model: str, provider: str) -> ChatResponse:
    return ChatResponse(
        message=ProviderChatMessage(role="assistant", content="hi"),
        usage=Usage(input_tokens=10, output_tokens=5),
        stop_reason="end_turn",
        model=model,
        provider=provider,
    )


class ScriptedProvider:
    """Fails N times, then answers; records call counts."""

    def __init__(self, name: str, *, is_local: bool, failures: list[Exception] | None = None):
        self._name = name
        self._is_local = is_local
        self.failures = list(failures or [])
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._is_local

    async def complete(self, model: str, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return _response(model, self._name)

    def stream(self, model: str, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        raise NotImplementedError  # complete-path tests only


class ManualClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _no_sleep(_seconds: float) -> None:
    return None


def _state(breaker: CircuitBreaker) -> BreakerState:
    """Read through a call so mypy's assert-narrowing resets between
    state-machine steps."""
    return breaker.state


def _executor(
    providers: dict[str, LLMProvider], clock: ManualClock | None = None
) -> ResilientExecutor:
    return ResilientExecutor(
        providers,
        BreakerBoard(clock or ManualClock()),
        sleep=_no_sleep,
        rng=random.Random(7),
    )


class TestRoutingTable:
    def test_hybrid_chains_are_spine_nine_verbatim(self) -> None:
        router = ModelRouter()
        chat = router.plan("chat", "hybrid")
        assert [m.model for m in chat.chain] == [
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "llama3.1:8b",
        ]
        assert [m.model for m in router.plan("escalation", "hybrid").chain] == [
            "claude-opus-4-8",
            "claude-sonnet-5",
        ]
        assert [m.model for m in router.plan("classification", "hybrid").chain] == [
            "claude-haiku-4-5-20251001",
            "llama3.1:8b",
        ]

    def test_local_only_chains_contain_no_cloud_rungs(self) -> None:
        router = ModelRouter()
        roles: tuple[Role, ...] = ("chat", "escalation", "classification")
        for role in roles:
            plan = router.plan(role, "local_only")
            assert all(rung.is_local for rung in plan.chain), role

    def test_escalation_degrades_to_chat_in_local_only(self) -> None:
        plan = ModelRouter().plan("escalation", "local_only")
        assert plan.role == "chat"
        assert plan.degraded_role is True


class TestCircuitBreaker:
    def test_five_consecutive_failures_open_the_breaker(self) -> None:
        clock = ManualClock()
        breaker = CircuitBreaker(clock)
        for _ in range(5):
            assert breaker.allows()
            breaker.record_failure()
        assert breaker.state == "open"
        assert not breaker.allows()  # instant skip, no dial

    def test_open_cools_off_into_half_open_probe(self) -> None:
        clock = ManualClock()
        breaker = CircuitBreaker(clock)
        for _ in range(5):
            breaker.record_failure()
        clock.now += OPEN_SECONDS
        assert breaker.allows()  # the probe
        assert _state(breaker) == "half_open"
        breaker.record_success()
        assert _state(breaker) == "closed"

    def test_half_open_failure_reopens(self) -> None:
        clock = ManualClock()
        breaker = CircuitBreaker(clock)
        for _ in range(5):
            breaker.record_failure()
        clock.now += OPEN_SECONDS
        assert breaker.allows()
        breaker.record_failure()
        assert _state(breaker) == "open"

    def test_window_rate_trips_without_consecutive_run(self) -> None:
        clock = ManualClock()
        breaker = CircuitBreaker(clock)
        # Alternate success/failure: never 5 consecutive, but 50% of 20.
        for _ in range(10):
            breaker.record_success()
            breaker.record_failure()
        assert breaker.state == "open"


class TestCostMeter:
    def test_cost_matches_hand_computed_fixture(self) -> None:
        meter = CostMeter({"m": ModelRates(3.00, 15.00)})
        cost = meter.cost_usd("m", Usage(input_tokens=3812, output_tokens=402))
        assert cost == pytest.approx((3812 * 3.00 + 402 * 15.00) / 1_000_000)
        assert cost == round(cost, 6)

    def test_local_models_cost_zero(self) -> None:
        meter = CostMeter()
        assert meter.cost_usd("llama3.1:8b", Usage(input_tokens=999, output_tokens=999)) == 0.0

    def test_unknown_models_cost_zero_not_guesses(self) -> None:
        assert CostMeter().cost_usd("mystery", Usage(input_tokens=10, output_tokens=10)) == 0.0


class TestResilientExecutor:
    async def test_primary_success_is_not_degraded(self) -> None:
        anthropic = ScriptedProvider("anthropic", is_local=False)
        executor = _executor(
            {"anthropic": anthropic, "ollama": ScriptedProvider("ollama", is_local=True)}
        )
        plan = ModelRouter().plan("chat", "hybrid")
        result = await executor.complete(plan, REQUEST, profile="hybrid")
        assert result.served_by.model == "claude-sonnet-5"
        assert result.degraded is False

    async def test_retry_then_fallback_marks_degraded(self) -> None:
        # Sonnet fails 3 times (initial + 2 retries), haiku answers.
        anthropic = ScriptedProvider(
            "anthropic",
            is_local=False,
            failures=[ProviderUnavailable("down")] * 3,
        )
        executor = _executor(
            {"anthropic": anthropic, "ollama": ScriptedProvider("ollama", is_local=True)}
        )
        plan = ModelRouter().plan("chat", "hybrid")
        result = await executor.complete(plan, REQUEST, profile="hybrid")
        assert result.served_by.model == "claude-haiku-4-5-20251001"
        assert result.degraded is True
        assert anthropic.calls == 4  # 3 sonnet attempts + 1 haiku success

    async def test_non_retryable_errors_skip_straight_to_fallback(self) -> None:
        anthropic = ScriptedProvider(
            "anthropic", is_local=False, failures=[AuthFailed("bad key"), ContentRefused("no")]
        )
        ollama = ScriptedProvider("ollama", is_local=True)
        executor = _executor({"anthropic": anthropic, "ollama": ollama})
        plan = ModelRouter().plan("chat", "hybrid")
        result = await executor.complete(plan, REQUEST, profile="hybrid")
        # one sonnet attempt, one haiku attempt, then llama answers
        assert anthropic.calls == 2
        assert result.served_by.model == "llama3.1:8b"

    async def test_rate_limit_retry_after_is_honored(self) -> None:
        slept: list[float] = []

        async def recording_sleep(seconds: float) -> None:
            slept.append(seconds)

        anthropic = ScriptedProvider(
            "anthropic",
            is_local=False,
            failures=[RateLimited("slow down", retry_after_seconds=4.5)],
        )
        executor = ResilientExecutor(
            {"anthropic": anthropic, "ollama": ScriptedProvider("ollama", is_local=True)},
            BreakerBoard(ManualClock()),
            sleep=recording_sleep,
            rng=random.Random(7),
        )
        plan = ModelRouter().plan("chat", "hybrid")
        await executor.complete(plan, REQUEST, profile="hybrid")
        assert slept == [4.5]

    async def test_chain_exhaustion_raises_model_unavailable(self) -> None:
        failures = [ProviderUnavailable("down")] * 20
        providers: dict[str, LLMProvider] = {
            "anthropic": ScriptedProvider("anthropic", is_local=False, failures=list(failures)),
            "ollama": ScriptedProvider("ollama", is_local=True, failures=list(failures)),
        }
        plan = ModelRouter().plan("chat", "hybrid")
        with pytest.raises(ModelUnavailable):
            await _executor(providers).complete(plan, REQUEST, profile="hybrid")

    async def test_local_only_fails_closed_on_cloud_drift(self) -> None:
        """docs/20 §4.2 defense in depth: a drifted plan with a cloud
        rung must never dispatch in local-only profile."""
        drifted = RoutePlan(
            role="chat",
            chain=ModelRouter().plan("chat", "hybrid").chain,  # cloud rungs
            timeout_class="interactive_stream",
            max_tokens=64,
        )
        providers: dict[str, LLMProvider] = {
            "anthropic": ScriptedProvider("anthropic", is_local=False),
            "ollama": ScriptedProvider("ollama", is_local=True),
        }
        with pytest.raises(ModelUnavailable):
            await _executor(providers).complete(drifted, REQUEST, profile="local_only")

    async def test_open_breaker_skips_the_rung_instantly(self) -> None:
        clock = ManualClock()
        anthropic = ScriptedProvider("anthropic", is_local=False)
        ollama = ScriptedProvider("ollama", is_local=True)
        executor = _executor({"anthropic": anthropic, "ollama": ollama}, clock)
        plan = ModelRouter().plan("chat", "hybrid")
        board = executor._breakers  # test reaches into the board deliberately
        breaker = board.for_model("anthropic", "claude-sonnet-5")
        for _ in range(5):
            breaker.record_failure()
        result = await executor.complete(plan, REQUEST, profile="hybrid")
        assert result.served_by.model == "claude-haiku-4-5-20251001"
        assert anthropic.calls == 1  # haiku only; sonnet never dialed
