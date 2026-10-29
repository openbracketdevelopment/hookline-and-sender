import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.security import sign_request, verify_request


def test_signature_validation_and_tampering():
    secret = "test-destination-secret"
    timestamp = "1800000000"
    body = b'{"value":1}'
    signature = sign_request(secret, timestamp, body)
    assert verify_request(secret, timestamp, signature, body, now=1_800_000_000)
    assert not verify_request(
        secret, timestamp, signature, b'{"value":2}', now=1_800_000_000
    )
    assert not verify_request(secret, timestamp, signature, body, now=1_800_000_301)
    assert not verify_request(
        secret, "not-a-timestamp", signature, body, now=1_800_000_000
    )
    assert not verify_request(secret, "١800000000", signature, body, now=1_800_000_000)
    assert not verify_request(secret, timestamp, "é", body, now=1_800_000_000)


def test_local_http_destination_requires_explicit_development_configuration():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            api_key="a" * 32,
            demo_signing_secret="b" * 32,
            demo_receiver_url="http://receiver:8080/webhooks",
            allow_insecure_demo_http=True,
        )

    settings = Settings(
        app_env="development",
        demo_receiver_url="http://receiver:8080/webhooks",
        allow_insecure_demo_http=True,
    )
    assert settings.demo_receiver_url.startswith("http://")
