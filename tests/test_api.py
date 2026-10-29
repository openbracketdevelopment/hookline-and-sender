import json

from backend.app.config import get_settings
from backend.app.models import Delivery, Event
from backend.app.workers import dispatcher

API_KEY = get_settings().api_key.get_secret_value()
AUTH = {"Authorization": f"Bearer {API_KEY}"}
EVENT = {
    "source": "payload-demo",
    "source_event_id": "publish-page-about-v1",
    "type": "content.published",
    "destination_id": "demo-receiver",
    "data": {
        "collection": "pages",
        "document_id": "page_123",
        "slug": "about",
    },
}


def test_health_and_business_authentication(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/api/v1/deliveries").status_code == 401
    assert client.get("/api/v1/deliveries", headers=AUTH).status_code == 200


def test_acceptance_duplicate_and_conflict(client, db_session_factory):
    first = client.post("/api/v1/events", json=EVENT, headers=AUTH)
    assert first.status_code == 202
    same = {**EVENT, "data": dict(reversed(list(EVENT["data"].items())))}
    duplicate = client.post("/api/v1/events", json=same, headers=AUTH)
    assert duplicate.status_code == 202
    assert duplicate.json() == first.json()

    conflict = {**EVENT, "data": {**EVENT["data"], "slug": "different"}}
    response = client.post("/api/v1/events", json=conflict, headers=AUTH)
    assert response.status_code == 409
    with db_session_factory() as session:
        assert session.query(Event).count() == 1
        assert session.query(Delivery).count() == 1
    event_id = first.json()["event_id"]
    details = client.get(f"/api/v1/events/{event_id}", headers=AUTH)
    assert details.status_code == 200
    assert details.json()["delivery_id"] == first.json()["delivery_id"]


def test_validation_rejects_unknown_destination_and_extra_fields(client):
    unknown = {**EVENT, "destination_id": "https://attacker.example"}
    assert client.post("/api/v1/events", json=unknown, headers=AUTH).status_code == 422
    extra = {**EVENT, "unexpected": True}
    assert client.post("/api/v1/events", json=extra, headers=AUTH).status_code == 422
    non_json_number = client.post(
        "/api/v1/events",
        content='{"source":"test","source_event_id":"nan","type":"test",'
        '"destination_id":"demo-receiver","data":{"value":NaN}}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert non_json_number.status_code == 422


def test_content_length_body_limit(client):
    oversized = {**EVENT, "data": {"payload": "x" * (256 * 1024)}}
    response = client.post("/api/v1/events", json=oversized, headers=AUTH)
    assert response.status_code == 413


def test_body_limit_without_content_length():
    from backend.app.main import EventBodyLimitMiddleware

    called = False
    sent = []

    async def app(scope, receive, send):
        nonlocal called
        called = True

    body = b"x" * (256 * 1024 + 1)
    chunks = iter(
        [
            {"type": "http.request", "body": body[:100_000], "more_body": True},
            {"type": "http.request", "body": body[100_000:], "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = EventBodyLimitMiddleware(app)
    import asyncio

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/events",
                "headers": [],
            },
            receive,
            send,
        )
    )
    assert called is False
    assert sent[0]["status"] == 413


def test_acceptance_does_not_depend_on_redis_and_dispatch_retries(
    client, db_session_factory, monkeypatch
):
    accepted = client.post("/api/v1/events", json=EVENT, headers=AUTH)
    assert accepted.status_code == 202
    delivery_id = accepted.json()["delivery_id"]
    calls = []

    def redis_unavailable(*, args):
        raise dispatcher.OperationalError("broker unavailable")

    monkeypatch.setattr(dispatcher.deliver_task, "apply_async", redis_unavailable)
    assert dispatcher.dispatch_due() == 0
    with db_session_factory() as session:
        delivery = session.get(Delivery, delivery_id)
        assert delivery.status == "pending"
        assert delivery.last_dispatched_at is None

    monkeypatch.setattr(
        dispatcher.deliver_task,
        "apply_async",
        lambda *, args: (
            calls.append(args[0]),
            delivery_service.process_delivery(args[0]),
        ),
    )
    from backend.app.services import delivery as delivery_service

    monkeypatch.setattr(delivery_service, "_send", lambda claim: (200, None))
    assert dispatcher.dispatch_due() == 1
    assert calls == [delivery_id]
    with db_session_factory() as session:
        assert session.get(Delivery, delivery_id).status == "succeeded"


def test_delivery_list_filters_and_paginates(client):
    for index in range(2):
        body = {**EVENT, "source_event_id": f"event-{index}"}
        assert client.post("/api/v1/events", json=body, headers=AUTH).status_code == 202
    response = client.get(
        "/api/v1/deliveries?status=pending&limit=1&offset=0",
        headers=AUTH,
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["status"] == "pending"


def test_normalized_payload_is_stored(client, db_session_factory):
    client.post("/api/v1/events", json=EVENT, headers=AUTH)
    with db_session_factory() as session:
        event = session.query(Event).one()
        delivery = session.query(Delivery).one()
        assert event.payload == EVENT["data"]
        outbound = json.loads(delivery.request_body)
        assert outbound["event"]["id"] == event.id
        assert outbound["delivery_id"] == delivery.id
