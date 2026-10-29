import asyncio
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from backend.app.models import Delivery, DeliveryAttempt, Event
from backend.app.services import delivery as delivery_service


def _create_delivery(factory, status="pending", attempt_count=0, lease=None):
    now = datetime.now(UTC)
    event_id = str(uuid4())
    delivery_id = str(uuid4())
    with factory.begin() as session:
        event = Event(
            id=event_id,
            source="test",
            source_event_id=str(uuid4()),
            event_type="test.event",
            destination_id="demo-receiver",
            payload={"value": 1},
            accepted_at=now,
        )
        session.add(event)
        session.add(
            Delivery(
                id=delivery_id,
                event_id=event_id,
                destination_id="demo-receiver",
                request_body='{"delivery_id":"stable","event":{"id":"event"}}',
                status=status,
                attempt_count=attempt_count,
                next_attempt_at=now - timedelta(seconds=1),
                lease_expires_at=lease,
                created_at=now,
                updated_at=now,
            )
        )
        if status == "in_progress":
            session.add(
                DeliveryAttempt(
                    id=str(uuid4()),
                    delivery_id=delivery_id,
                    attempt_number=attempt_count,
                    started_at=now - timedelta(seconds=20),
                    outcome="in_progress",
                )
            )
    return delivery_id


@pytest.mark.parametrize(
    ("http_status", "expected_status", "expected_outcome"),
    [
        (200, "succeeded", "succeeded"),
        (503, "retry_scheduled", "retryable_http_error"),
        (429, "retry_scheduled", "retryable_http_error"),
        (408, "retry_scheduled", "retryable_http_error"),
        (400, "failed", "terminal_http_error"),
        (302, "failed", "terminal_http_error"),
    ],
)
def test_http_response_policy(
    db_session_factory, monkeypatch, http_status, expected_status, expected_outcome
):
    delivery_id = _create_delivery(db_session_factory)
    monkeypatch.setattr(delivery_service, "_send", lambda claim: (http_status, None))
    delivery_service.process_delivery(delivery_id)
    with db_session_factory() as session:
        delivery = session.get(Delivery, delivery_id)
        attempt = session.query(DeliveryAttempt).one()
        assert delivery.status == expected_status
        assert attempt.outcome == expected_outcome
        assert attempt.http_status == http_status


def test_timeout_is_recorded_and_retried(db_session_factory, monkeypatch):
    delivery_id = _create_delivery(db_session_factory)

    def timeout(_claim):
        raise httpx.ReadTimeout("receiver timed out")

    monkeypatch.setattr(delivery_service, "_send", timeout)
    delivery_service.process_delivery(delivery_id)
    with db_session_factory() as session:
        delivery = session.get(Delivery, delivery_id)
        attempt = session.query(DeliveryAttempt).one()
        assert delivery.status == "retry_scheduled"
        assert attempt.outcome == "timeout"
        assert attempt.error_detail == "ReadTimeout"


def test_outgoing_request_signs_exact_persisted_bytes(monkeypatch):
    from backend.app.config import get_settings
    from backend.app.security import verify_request

    original_client = httpx.AsyncClient
    captured = {}

    def handle(request):
        captured["request"] = request
        return httpx.Response(200)

    def make_client(**options):
        captured["options"] = options
        return original_client(transport=httpx.MockTransport(handle), **options)

    monkeypatch.setattr(delivery_service.httpx, "AsyncClient", make_client)
    claim = delivery_service.DeliveryClaim(
        delivery_id="delivery-stable",
        event_id="event-stable",
        attempt_id="attempt-1",
        attempt_number=1,
        claim_token="claim-token",
        request_body=b'{"fixed":"bytes"}',
        request_deadline=time.monotonic() + 3,
    )
    assert delivery_service._send(claim) == (200, None)

    request = captured["request"]
    timestamp = request.headers["X-Hookline-Timestamp"]
    assert request.content == claim.request_body
    assert request.headers["X-Hookline-Event-Id"] == claim.event_id
    assert request.headers["X-Hookline-Delivery-Id"] == claim.delivery_id
    assert verify_request(
        get_settings().demo_signing_secret.get_secret_value(),
        timestamp,
        request.headers["X-Hookline-Signature"],
        claim.request_body,
        now=int(timestamp),
    )
    assert captured["options"]["follow_redirects"] is False
    assert captured["options"]["trust_env"] is False


def _mock_outbound(monkeypatch, handler):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        delivery_service.httpx,
        "AsyncClient",
        lambda **options: original_client(
            transport=httpx.MockTransport(handler), **options
        ),
    )


def test_success_headers_do_not_require_response_body(db_session_factory, monkeypatch):
    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("Response body must never be read")
            yield b""

    _mock_outbound(
        monkeypatch, lambda request: httpx.Response(200, stream=StalledBody())
    )
    delivery_id = _create_delivery(db_session_factory)
    delivery_service.process_delivery(delivery_id)
    with db_session_factory() as session:
        assert session.get(Delivery, delivery_id).status == "succeeded"


def test_total_request_deadline_cancels_stalled_request(
    db_session_factory, monkeypatch
):
    from backend.app.config import Settings

    settings = Settings(request_timeout_seconds=0.1)
    monkeypatch.setattr(delivery_service, "get_settings", lambda: settings)
    cancelled = False

    async def stalled_headers(request):
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        finally:
            cancelled = True
        return httpx.Response(200)

    _mock_outbound(monkeypatch, stalled_headers)
    delivery_id = _create_delivery(db_session_factory)
    started = time.monotonic()
    delivery_service.process_delivery(delivery_id)
    elapsed = time.monotonic() - started
    assert cancelled
    assert elapsed < 1
    with db_session_factory() as session:
        assert session.get(Delivery, delivery_id).status == "retry_scheduled"
        assert session.query(DeliveryAttempt).one().outcome == "timeout"


def test_expired_request_budget_does_not_send(db_session_factory, monkeypatch):
    sent = []
    _mock_outbound(
        monkeypatch, lambda request: sent.append(request) or httpx.Response(200)
    )
    delivery_id = _create_delivery(db_session_factory)
    claim = delivery_service.claim_delivery(delivery_id)
    assert claim is not None
    from dataclasses import replace

    expired = replace(claim, request_deadline=time.monotonic() - 1)
    with pytest.raises(httpx.TimeoutException):
        delivery_service._send(expired)
    assert sent == []


def test_maximum_attempts_terminally_fails(db_session_factory, monkeypatch):
    from backend.app.config import Settings

    delivery_id = _create_delivery(db_session_factory, attempt_count=4)
    monkeypatch.setattr(
        delivery_service, "get_settings", lambda: Settings(max_attempts=5)
    )
    monkeypatch.setattr(delivery_service, "_send", lambda claim: (503, None))
    delivery_service.process_delivery(delivery_id)
    with db_session_factory() as session:
        delivery = session.get(Delivery, delivery_id)
        assert delivery.status == "failed"
        assert delivery.attempt_count == 5
        assert session.query(DeliveryAttempt).count() == 1


def test_duplicate_task_does_not_redeliver(db_session_factory, monkeypatch):
    delivery_id = _create_delivery(db_session_factory)
    sends = []
    monkeypatch.setattr(
        delivery_service, "_send", lambda claim: (sends.append(claim) or 200, None)
    )
    delivery_service.process_delivery(delivery_id)
    delivery_service.process_delivery(delivery_id)
    assert len(sends) == 1


def test_expired_lease_records_lost_attempt_and_recovers(
    db_session_factory, monkeypatch
):
    delivery_id = _create_delivery(
        db_session_factory,
        status="in_progress",
        attempt_count=1,
        lease=datetime.now(UTC) - timedelta(seconds=1),
    )
    monkeypatch.setattr(delivery_service, "_send", lambda claim: (200, None))
    delivery_service.process_delivery(delivery_id)
    with db_session_factory() as session:
        delivery = session.get(Delivery, delivery_id)
        attempts = (
            session.query(DeliveryAttempt)
            .order_by(DeliveryAttempt.attempt_number)
            .all()
        )
        assert delivery.status == "succeeded"
        assert delivery.attempt_count == 2
        assert [attempt.outcome for attempt in attempts] == [
            "worker_lost",
            "succeeded",
        ]


def test_retry_after_is_capped():
    from backend.app.config import Settings

    settings = Settings(retry_base_seconds=2, retry_max_seconds=10)
    original = delivery_service.get_settings
    delivery_service.get_settings = lambda: settings
    try:
        assert delivery_service._retry_delay(1, 1000) == 10
    finally:
        delivery_service.get_settings = original


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        ("5", 5),
        ("-1", 0),
        ("NaN", None),
        ("Infinity", None),
        ("not-a-date", None),
        ("1" * 129, None),
        ("Thu, 01 Jan 2026 00:00:10 GMT", 10),
        ("Wed, 31 Dec 2025 23:59:50 GMT", 0),
    ],
)
def test_retry_after_parsing(header, seconds):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert delivery_service._retry_after_seconds(header, now) == seconds
