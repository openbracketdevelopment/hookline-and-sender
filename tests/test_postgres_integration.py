import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base, Delivery, Event
from backend.app.services.delivery import claim_delivery

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_factory():
    if not DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL checks")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
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


def test_postgres_claims_delivery_only_once(postgres_factory, monkeypatch):
    import backend.app.services.delivery as delivery_service

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
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_delivery(delivery_id), range(2)))
    assert sum(claim is not None for claim in claims) == 1
