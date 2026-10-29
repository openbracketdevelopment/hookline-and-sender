import pytest
from pydantic import ValidationError

from backend.app.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dispatcher_interval_seconds", 0),
        ("dispatcher_interval_seconds", -1),
        ("dispatch_cooldown_seconds", 0),
        ("max_request_bytes", 0),
        ("max_request_bytes", 262145),
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", float("nan")),
        ("request_timeout_seconds", float("inf")),
        ("retry_base_seconds", -1),
        ("retry_base_seconds", float("nan")),
        ("retry_max_seconds", float("inf")),
        ("lease_margin_seconds", 0),
        ("dependency_timeout_seconds", 0),
        ("max_attempts", 6),
        ("signature_tolerance_seconds", 0),
    ],
)
def test_invalid_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_retry_base_cannot_exceed_cap():
    with pytest.raises(ValidationError):
        Settings(retry_base_seconds=10, retry_max_seconds=1)


def test_lease_rounds_up_instead_of_truncating():
    settings = Settings(request_timeout_seconds=3.5, lease_margin_seconds=10)
    assert settings.request_lease_seconds == 14
