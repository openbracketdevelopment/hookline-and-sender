import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.app.models import Base, Delivery, DeliveryAttempt, Event
from backend.app.schemas import EventCreate
from backend.app.services import delivery as delivery_service
from backend.app.services.delivery import claim_delivery
from backend.app.services.events import accept_event

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_factory():
    if not DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL checks")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    schema = f"test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(isolated)
        yield sessionmaker(bind=isolated, expire_on_commit=False)
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_postgres_enforces_producer_identity(postgres_factory):
    now = datetime.now(UTC)
    with postgres_factory.begin() as session:
        session.add(
            Event(
                id=str(uuid4()),
                source="integration",
                source_event_id="stable-id",
                event_type="test",
                destination_id="demo-receiver",
                payload={"value": 1},
                accepted_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        with postgres_factory.begin() as session:
            session.add(
                Event(
                    id=str(uuid4()),
                    source="integration",
                    source_event_id="stable-id",
                    event_type="test",
                    destination_id="demo-receiver",
                    payload={"value": 1},
                    accepted_at=now,
                )
            )


@pytest.fixture
def postgres_delivery(postgres_factory, monkeypatch):
    now = datetime.now(UTC)
    event_id = str(uuid4())
    delivery_id = str(uuid4())
    with postgres_factory.begin() as session:
        session.add(
            Event(
                id=event_id,
                source="integration",
                source_event_id=str(uuid4()),
                event_type="test",
                destination_id="demo-receiver",
                payload={},
                accepted_at=now,
            )
        )
        session.add(
            Delivery(
                id=delivery_id,
                event_id=event_id,
                destination_id="demo-receiver",
                request_body="{}",
                status="pending",
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    monkeypatch.setattr(delivery_service, "SessionLocal", postgres_factory)
    return delivery_id


def test_postgres_claims_delivery_only_once(postgres_delivery):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_delivery(postgres_delivery), range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_expired_worker_cannot_overwrite_recovered_attempt(
    postgres_factory, postgres_delivery
):
    original = claim_delivery(postgres_delivery)
    assert original is not None
    with postgres_factory.begin() as session:
        delivery = session.get(Delivery, postgres_delivery)
        delivery.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovered = list(
            pool.map(lambda _: claim_delivery(postgres_delivery), range(2))
        )
    claimed = [claim for claim in recovered if claim is not None]
    assert len(claimed) == 1
    current = claimed[0]
    assert current.attempt_number == 2

    delivery_service._complete(
        original,
        outcome="succeeded",
        http_status=200,
        error_detail=None,
        duration_ms=1,
    )
    with postgres_factory() as session:
        assert session.get(Delivery, postgres_delivery).status == "in_progress"
        assert (
            session.get(DeliveryAttempt, original.attempt_id).outcome == "worker_lost"
        )

    delivery_service._complete(
        current,
        outcome="succeeded",
        http_status=200,
        error_detail=None,
        duration_ms=1,
    )
    assert claim_delivery(postgres_delivery) is None
    with postgres_factory() as session:
        assert session.get(Delivery, postgres_delivery).status == "succeeded"
        attempts = session.scalars(
            select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number)
        ).all()
        assert [attempt.outcome for attempt in attempts] == ["worker_lost", "succeeded"]


def test_concurrent_dispatchers_can_publish_duplicates_but_only_send_once(
    postgres_factory, postgres_delivery, monkeypatch
):
    from backend.app.workers import dispatcher

    queued = []
    lock = threading.Lock()
    both_scanned = threading.Barrier(2)

    def enqueue(*, args):
        both_scanned.wait(timeout=5)
        with lock:
            queued.append(args[0])

    monkeypatch.setattr(dispatcher, "SessionLocal", postgres_factory)
    monkeypatch.setattr(dispatcher.deliver_task, "apply_async", enqueue)
    with ThreadPoolExecutor(max_workers=2) as pool:
        published = list(pool.map(lambda _: dispatcher.dispatch_due(), range(2)))
    assert published == [1, 1]
    assert queued == [postgres_delivery, postgres_delivery]

    sent = []

    def send(claim):
        with lock:
            sent.append(claim.delivery_id)
        return 200, None

    monkeypatch.setattr(delivery_service, "_send", send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(delivery_service.process_delivery, queued))
    assert sent == [postgres_delivery]


def test_lost_response_retries_same_body_and_delivery_id(
    postgres_factory, postgres_delivery, monkeypatch
):
    original_client = httpx.AsyncClient
    processed = set()
    requests = []
    side_effects = []

    async def receiver(request):
        delivery_id = request.headers["X-Hookline-Delivery-Id"]
        requests.append((delivery_id, request.content))
        if delivery_id in processed:
            return httpx.Response(200)
        processed.add(delivery_id)
        side_effects.append(delivery_id)
        raise httpx.ReadTimeout("Response lost after the receiver processed the event")

    monkeypatch.setattr(
        delivery_service.httpx,
        "AsyncClient",
        lambda **options: original_client(
            transport=httpx.MockTransport(receiver), **options
        ),
    )
    delivery_service.process_delivery(postgres_delivery)
    with postgres_factory.begin() as session:
        delivery = session.get(Delivery, postgres_delivery)
        assert delivery.status == "retry_scheduled"
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    delivery_service.process_delivery(postgres_delivery)
    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert side_effects == [postgres_delivery]
    with postgres_factory() as session:
        assert session.get(Delivery, postgres_delivery).status == "succeeded"


def test_expired_final_attempt_fails_without_sending_again(
    postgres_factory, postgres_delivery, monkeypatch
):
    from backend.app.config import Settings

    monkeypatch.setattr(
        delivery_service, "get_settings", lambda: Settings(max_attempts=1)
    )
    claim = claim_delivery(postgres_delivery)
    assert claim is not None
    with postgres_factory.begin() as session:
        delivery = session.get(Delivery, postgres_delivery)
        delivery.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert claim_delivery(postgres_delivery) is None
    with postgres_factory() as session:
        assert session.get(Delivery, postgres_delivery).status == "failed"
        assert session.get(DeliveryAttempt, claim.attempt_id).outcome == "worker_lost"


def test_concurrent_producer_submissions_reuse_committed_ids(postgres_factory):
    event = EventCreate(
        source="integration",
        source_event_id="concurrent-submit",
        type="content.published",
        destination_id="demo-receiver",
        data={"slug": "about"},
    )
    start = threading.Barrier(2)

    def submit(_):
        with postgres_factory() as session:
            start.wait(timeout=5)
            saved_event, delivery = accept_event(session, event)
            return saved_event.id, delivery.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
