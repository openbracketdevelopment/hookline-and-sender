import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.receiver import main as receiver
from backend.app.security import sign_request


def test_receiver_fails_twice_then_succeeds_and_deduplicates():
    receiver._attempts.clear()
    receiver._processed.clear()
    client = TestClient(receiver.app)
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

    assert send().status_code == 503
    assert send().status_code == 503
    success = send()
    assert success.status_code == 200
    assert success.json()["duplicate"] is False
    duplicate = send()
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert receiver._attempts[delivery_id] == 3
