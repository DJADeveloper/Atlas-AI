"""First-token latency harness (M07 acceptance: p95 ≤ 2.5 s in hybrid
profile over 50 sequential requests against the live provider).

Measures the adapter streaming path exactly as chat uses it: open the
stream, clock to the FIRST text delta, then drain. Sequential on
purpose — the acceptance criterion describes interactive latency, not
throughput, and parallel requests would hide provider queueing.

    ANTHROPIC_API_KEY=... uv run python scripts/chat_latency.py \
        --provider anthropic --output chat_latency.json

`--provider ollama` measures the local rung instead (requires a
running Ollama with the chat model pulled).
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

from atlas.domain.ai.provider import ChatRequest, LLMProvider, ProviderChatMessage
from atlas.infrastructure.providers.anthropic import AnthropicChatProvider
from atlas.infrastructure.providers.ollama import OllamaChatProvider

REQUESTS = 50
MAX_TOKENS = 64
TARGET_FIRST_TOKEN_P95_MS = 2_500.0  # M07 acceptance ceiling (hybrid)

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"  # cheapest rung; same wire path
OLLAMA_MODEL = "llama3.1:8b"

_PROMPTS = [
    "In one sentence, what is a rolling summary?",
    "Name three uses for a local search index.",
    "Explain idempotency to a new engineer, briefly.",
    "What does p95 latency mean?",
    "Give one reason to version documents.",
]


def _build_provider(name: str) -> tuple[LLMProvider, str]:
    if name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required for --provider anthropic")
        return AnthropicChatProvider(api_key), ANTHROPIC_MODEL
    return OllamaChatProvider(os.environ.get("ATLAS_OLLAMA_URL", "http://localhost:11434")), (
        OLLAMA_MODEL
    )


async def _measure(provider: LLMProvider, model: str, requests: int) -> dict[str, object]:
    first_token_ms: list[float] = []
    total_ms: list[float] = []
    for index in range(requests):
        request = ChatRequest(
            messages=[ProviderChatMessage(role="user", content=_PROMPTS[index % len(_PROMPTS)])],
            max_tokens=MAX_TOKENS,
        )
        started = time.perf_counter()
        first: float | None = None
        async for event in provider.stream(model, request):
            if event.type == "text_delta" and first is None:
                first = (time.perf_counter() - started) * 1000
        if first is None:
            raise SystemExit(f"request {index}: stream produced no text delta")
        first_token_ms.append(first)
        total_ms.append((time.perf_counter() - started) * 1000)
    quantiles = statistics.quantiles(first_token_ms, n=20)
    return {
        "requests": requests,
        "model": model,
        "first_token_p50_ms": round(statistics.median(first_token_ms), 1),
        "first_token_p95_ms": round(quantiles[18], 1),
        "first_token_max_ms": round(max(first_token_ms), 1),
        "total_p50_ms": round(statistics.median(total_ms), 1),
        "target_first_token_p95_ms": TARGET_FIRST_TOKEN_P95_MS,
    }


async def _run(provider_name: str, requests: int) -> dict[str, object]:
    provider, model = _build_provider(provider_name)
    try:
        result = await _measure(provider, model, requests)
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
    result["provider"] = provider_name
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["anthropic", "ollama"], default="anthropic")
    parser.add_argument("--requests", type=int, default=REQUESTS)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    result = asyncio.run(_run(args.provider, args.requests))
    payload = json.dumps(result, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    p95 = result["first_token_p95_ms"]
    return 0 if isinstance(p95, float) and p95 <= TARGET_FIRST_TOKEN_P95_MS else 1


if __name__ == "__main__":
    sys.exit(main())
