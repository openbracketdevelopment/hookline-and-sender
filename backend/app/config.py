from functools import lru_cache
from urllib.parse import urlparse

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    api_key: SecretStr = SecretStr("dev-only-change-me")
    demo_signing_secret: SecretStr = SecretStr("dev-only-signing-secret-change-me")
    database_url: str = "postgresql+psycopg://hookline:hookline@localhost:5432/hookline"
    redis_url: str = "redis://localhost:6379/0"
    demo_receiver_url: str = "https://demo.invalid/webhooks"
    allow_insecure_demo_http: bool = False
    max_request_bytes: int = 256 * 1024
    request_timeout_seconds: float = 3.0
    lease_margin_seconds: int = 10
    dispatcher_interval_seconds: float = 2.0
    dispatch_cooldown_seconds: int = 5
    max_attempts: int = 5
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    signature_tolerance_seconds: int = 300

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.max_attempts < 1:
            raise ValueError("MAX_ATTEMPTS must be at least one")
        if self.request_timeout_seconds <= 0 or self.lease_margin_seconds < 1:
            raise ValueError("Request timeout and lease margin must be positive")
        if self.retry_base_seconds <= 0 or self.retry_max_seconds <= 0:
            raise ValueError("Retry delays must be positive")
        if self.app_env != "development":
            for name, secret in (
                ("API_KEY", self.api_key),
                ("DEMO_SIGNING_SECRET", self.demo_signing_secret),
            ):
                if len(secret.get_secret_value()) < 32:
                    raise ValueError(f"{name} must contain at least 32 characters")
        parsed = urlparse(self.demo_receiver_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("DEMO_RECEIVER_URL must be an absolute HTTP(S) URL")
        if parsed.scheme == "http" and (
            self.app_env != "development"
            or not self.allow_insecure_demo_http
            or parsed.hostname not in {"receiver", "localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError(
                "HTTP destinations are restricted to the explicitly enabled local demo"
            )
        return self

    @property
    def request_lease_seconds(self) -> int:
        return int(self.request_timeout_seconds + self.lease_margin_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
