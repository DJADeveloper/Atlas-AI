"""Normalized provider error taxonomy (docs/20 §3).

The contract that lets retry policy live entirely above the adapters:
the runtime branches on error TYPE, never on vendor status codes.
Retryability is a property of the class, declared once."""

from typing import ClassVar

from atlas.shared.errors import AtlasError


class ProviderError(AtlasError):
    """Base for the normalized taxonomy; `retryable` drives §5.2 policy."""

    status = 502
    retryable: ClassVar[bool] = False


class ProviderTimeout(ProviderError):
    code = "provider_timeout"
    title = "Provider timed out"
    retryable = True


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    title = "Provider unavailable"
    retryable = True


class RateLimited(ProviderError):
    code = "rate_limited"
    title = "Provider rate limit"
    status = 429
    retryable = True

    def __init__(self, detail: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(detail, context={"retry_after_seconds": retry_after_seconds})
        self.retry_after_seconds = retry_after_seconds


class AuthFailed(ProviderError):
    code = "provider_auth_failed"
    title = "Provider authentication failed"
    status = 401
    retryable = False  # surface to the user; retrying burns nothing but money


class ContextWindowExceeded(ProviderError):
    code = "context_window_exceeded"
    title = "Context window exceeded"
    status = 413
    retryable = False  # shrink and re-pack instead (docs/20 §9)


class ContentRefused(ProviderError):
    code = "content_refused"
    title = "Provider refused the request"
    status = 422
    retryable = False  # surface honestly


class MalformedResponse(ProviderError):
    code = "malformed_provider_response"
    title = "Malformed provider response"
    retryable = True  # exactly once — enforced by the retry policy, not here


class ModelUnavailable(ProviderError):
    """Fallback chain exhausted (docs/20 §5.4): every rung failed or the
    local-only profile has no running local model."""

    code = "model_unavailable"
    title = "No model available"
    status = 503
    retryable = False
