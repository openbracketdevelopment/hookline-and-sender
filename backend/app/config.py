from functools import lru_cache
from math import ceil
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        allow_inf_nan=False,
    )

    app_env: str = "development"
    api_key: SecretStr = SecretStr("dev-only-change-me")
    demo_signing_secret: SecretStr = SecretStr("dev-only-signing-secret-change-me")
    database_url: str = "postgresql+psycopg://hookline:hookline@localhost:5432/hookline"
    redis_url: str = "redis://localhost:6379/0"
    demo_receiver_url: str = "https://demo.invalid/webhooks"
    allow_insecure_demo_http: bool = False
    max_request_bytes: int = Field(default=256 * 1024, gt=0, le=256 * 1024)
    request_timeout_seconds: float = Field(default=3.0, gt=0, le=300)
    lease_margin_seconds: int = Field(default=10, ge=1, le=300)
    dependency_timeout_seconds: int = Field(default=3, ge=2, le=30)
    dispatcher_interval_seconds: float = Field(default=2.0, gt=0, le=300)
    dispatch_cooldown_seconds: int = Field(default=5, ge=1, le=300)
    max_attempts: int = Field(default=5, ge=1, le=5)
    retry_base_seconds: float = Field(default=1.0, gt=0, le=86400)
    retry_max_seconds: float = Field(default=30.0, gt=0, le=86400)
    signature_tolerance_seconds: int = Field(default=300, ge=1, le=300)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("RETRY_BASE_SECONDS must not exceed RETRY_MAX_SECONDS")
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
        return ceil(self.request_timeout_seconds + self.lease_margin_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
