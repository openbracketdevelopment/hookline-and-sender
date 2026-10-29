import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.receiver import main as receiver
from backend.app.security import sign_request


@pytest.mark.parametrize("failures", [0, 2])
def test_receiver_succeeds_and_deduplicates_with_optional_failures(failures):
    client = TestClient(receiver.app)
    assert client.post("/demo/reset", params={"failures": failures}).status_code == 200
    delivery_id = str(uuid4())
    event_id = str(uuid4())
    body = json.dumps(
        {"delivery_id": delivery_id, "event": {"id": event_id}},
        separators=(",", ":"),
    ).encode()
    settings = get_settings()

    def send():
        timestamp = str(int(time.time()))
        return client.post(
            "/webhooks",
            content=body,
            headers={
                "X-Hookline-Timestamp": timestamp,
                "X-Hookline-Signature": sign_request(
                    settings.demo_signing_secret.get_secret_value(),
                    timestamp,
                    body,
                ),
                "X-Hookline-Event-Id": event_id,
                "X-Hookline-Delivery-Id": delivery_id,
            },
        )

    for _ in range(failures):
        assert send().status_code == 503
    success = send()
    assert success.status_code == 200
    assert success.json()["duplicate"] is False
    duplicate = send()
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert receiver._attempts[delivery_id] == failures + 1
    assert client.post("/demo/reset").status_code == 200


def test_receiver_reset_validates_failure_count():
    client = TestClient(receiver.app)
    assert client.post("/demo/reset?failures=3").status_code == 422
    assert client.post("/demo/reset?failures=-1").status_code == 422


def test_receiver_controls_are_disabled_outside_development(monkeypatch):
    from backend.app.config import Settings

    monkeypatch.setattr(
        receiver,
        "get_settings",
        lambda: Settings(
            app_env="test",
            api_key="a" * 32,
            demo_signing_secret="b" * 32,
            demo_receiver_url="https://demo.invalid/webhooks",
        ),
    )
    client = TestClient(receiver.app)
    assert client.post("/demo/reset?failures=2").status_code == 404
    assert client.get("/demo/state").status_code == 404
