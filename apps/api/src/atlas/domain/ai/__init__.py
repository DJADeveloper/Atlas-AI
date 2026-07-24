"""AI provider port (docs/20): vendor-neutral types, normalized errors."""

from atlas.domain.ai.errors import (
    AuthFailed,
    ContentRefused,
    ContextWindowExceeded,
    MalformedResponse,
    ModelUnavailable,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from atlas.domain.ai.provider import (
    ChatEvent,
    ChatRequest,
    ChatResponse,
    ChatRole,
    LLMProvider,
    ProviderChatMessage,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "AuthFailed",
    "ChatEvent",
    "ChatRequest",
    "ChatResponse",
    "ChatRole",
    "ContentRefused",
    "ContextWindowExceeded",
    "LLMProvider",
    "MalformedResponse",
    "ModelUnavailable",
    "ProviderChatMessage",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RateLimited",
    "StopReason",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
