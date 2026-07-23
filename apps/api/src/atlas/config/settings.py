"""Runtime settings.

Sources are layered lowest-to-highest precedence: field defaults → `.env`
file → environment variables → explicit constructor arguments (tests).
All environment variables use the `ATLAS_` prefix (spine §8 conventions).

M02 scope: profile selection, connection targets, logging, feature-flag
seeds. Provider *behavior* (adapters, routing, fallback) arrives at M07 —
here a profile only resolves to configuration data.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas.config.profiles import PROFILES, Profile, ProviderProfile


class Settings(BaseSettings):
    """Process-level settings.

    Defaults match the compose `core` profile so a fresh clone works with
    zero configuration.
    """

    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    profile: Profile = "hybrid"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
    redis_url: str = "redis://localhost:6379/0"
    readiness_timeout_seconds: float = 2.0
    # Compose and desktop targets set this (docs/40 §2.2); tests and
    # bare dev servers migrate explicitly via make db-upgrade.
    run_migrations_on_startup: bool = False

    feature_flags: dict[str, bool] = Field(default_factory=dict)

    @property
    def provider_profile(self) -> ProviderProfile:
        """The model-routing configuration this profile resolves to."""
        return PROFILES[self.profile]


def load_settings() -> Settings:
    """Read settings from the environment.

    A plain factory, deliberately uncached: the composition root calls it
    exactly once per process and owns the instance (no module-level
    singletons — M02 acceptance).
    """
    return Settings()
