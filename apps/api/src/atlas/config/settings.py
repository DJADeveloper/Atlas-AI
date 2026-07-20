"""Runtime settings.

M01 deliberately ships only what the readiness probe needs: connection
targets and a probe timeout. The full configuration system — profiles
(hybrid / local-only), feature flags, and the DI composition root — is
M02 scope (`docs/60-milestones.md`).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings, sourced from environment variables.

    Defaults match the compose `core` profile so a fresh clone works
    with zero configuration.
    """

    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
    redis_url: str = "redis://localhost:6379/0"
    readiness_timeout_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
