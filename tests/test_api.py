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


def test_docs_description_explains_the_app(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Hook, Line & Sender"
    description = schema["info"]["description"]
    assert "webhook delivery service" in description
    assert "not connected yet" in description
    assert "HTTP 202" in description


def test_explorer_serves_local_shell_and_theme_assets(client):
    page = client.get("/docs")
    assert page.status_code == 200
    assert 'id="theme-toggle"' in page.text
    assert "5.34.0" in page.text
    assert client.get("/explorer-assets/explorer.css").status_code == 200
    script = client.get("/explorer-assets/explorer.js").text
    assert "persistAuthorization: false" in script
    assert "validatorUrl: null" in script
    assert "hookline-theme" in client.get("/explorer-assets/theme.js").text


def test_activity_dashboard_shell_and_authenticated_history(client):
    page = client.get("/activity")
    assert page.status_code == 200
    assert 'id="delivery-rows"' in page.text
    assert 'href="/docs"' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/explorer-assets/activity.js").status_code == 200
    assert client.get("/explorer-assets/activity.css").status_code == 200
    assert 'href="/activity"' in client.get("/docs").text
    delivery_id = client.post("/api/v1/events", headers=AUTH, json=EVENT).json()[
        "delivery_id"
    ]
    assert client.get(f"/api/v1/deliveries/{delivery_id}").status_code == 401
    page = client.get(
        "/api/v1/deliveries?status=pending&limit=26&offset=0", headers=AUTH
    ).json()
    assert page["items"][0]["id"] == delivery_id
    assert "data" not in page["items"][0]
    assert (
        client.get("/api/v1/deliveries?status=succeeded", headers=AUTH).json()["items"]
        == []
    )


def test_ready_checks_schema_and_bounded_redis_ping(
    client, db_session_factory, monkeypatch
):
    from backend.app import database
    from backend.app.api import routes

    captured = {}

    class HealthyRedis:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ping(self):
            return True

    def redis_client(url, **options):
        captured.update(options)
        return HealthyRedis()

    monkeypatch.setattr(database, "engine", db_session_factory.kw["bind"])
    monkeypatch.setattr(routes.Redis, "from_url", redis_client)
    assert client.get("/health/ready").status_code == 200
    assert captured["socket_timeout"] == get_settings().dependency_timeout_seconds
    assert captured["socket_connect_timeout"] == captured["socket_timeout"]
    assert captured["retry"].get_retries() == 0


def test_ready_rejects_missing_database_schema(client, db_session_factory, monkeypatch):
    from backend.app import database
    from backend.app.models import Base

    engine = db_session_factory.kw["bind"]
    Base.metadata.drop_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    assert client.get("/health/ready").status_code == 503


def test_ready_rejects_stalled_redis(client, db_session_factory, monkeypatch):
    from redis.exceptions import TimeoutError

    from backend.app import database
    from backend.app.api import routes

    class StalledRedis:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ping(self):
            raise TimeoutError("Redis did not respond")

    monkeypatch.setattr(database, "engine", db_session_factory.kw["bind"])
    monkeypatch.setattr(
        routes.Redis, "from_url", lambda *args, **kwargs: StalledRedis()
    )
    assert client.get("/health/ready").status_code == 503


def test_publisher_has_bounded_socket_waits_and_no_inline_retries():
    from backend.app.workers.celery_app import celery_app

    options = celery_app.conf.broker_transport_options
    assert (
        options["socket_connect_timeout"] == get_settings().dependency_timeout_seconds
    )
    assert options["socket_timeout"] == options["socket_connect_timeout"]
    assert options["retry_on_timeout"] is False
    assert celery_app.conf.task_publish_retry is False


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
