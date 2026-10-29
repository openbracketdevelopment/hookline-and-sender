import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.logging_config import log_after_commit
from backend.app.models import Delivery, Event
from backend.app.schemas import EventCreate


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _find_existing(
    session: Session, body: EventCreate
) -> tuple[Event, Delivery] | None:
    return session.execute(
        select(Event, Delivery)
        .join(Delivery, Delivery.event_id == Event.id)
        .where(
            Event.source == body.source,
            Event.source_event_id == body.source_event_id,
            Event.destination_id == body.destination_id,
        )
    ).first()


def _content_matches(event: Event, body: EventCreate) -> bool:
    return event.event_type == body.type and _canonical_json(
        event.payload
    ) == _canonical_json(body.data)


def accept_event(session: Session, body: EventCreate) -> tuple[Event, Delivery]:
    if body.destination_id != "demo-receiver":
        raise HTTPException(status_code=422, detail="Unknown destination_id")

    existing = _find_existing(session, body)
    if existing is not None:
        event, delivery = existing
        if not _content_matches(event, body):
            raise HTTPException(
                status_code=409,
                detail="source_event_id was already used with different content",
            ) from None
        return event, delivery

    event_id = str(uuid4())
    delivery_id = str(uuid4())
    accepted_at = datetime.now(UTC)
    event = Event(
        id=event_id,
        source=body.source,
        source_event_id=body.source_event_id,
        event_type=body.type,
        destination_id=body.destination_id,
        payload=body.data,
        accepted_at=accepted_at,
    )
    outbound = {
        "delivery_id": delivery_id,
        "event": {
            "accepted_at": accepted_at.isoformat(),
            "data": body.data,
            "id": event_id,
            "source": body.source,
            "source_event_id": body.source_event_id,
            "type": body.type,
        },
    }
    delivery = Delivery(
        id=delivery_id,
        event_id=event_id,
        destination_id=body.destination_id,
        request_body=_canonical_json(outbound),
        status="pending",
        attempt_count=0,
        next_attempt_at=accepted_at,
        created_at=accepted_at,
        updated_at=accepted_at,
    )
    session.add_all([event, delivery])
    log_after_commit(
        session,
        "event accepted",
        event_id=event_id,
        delivery_id=delivery_id,
        status="pending",
        attempt_number=0,
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raced = _find_existing(session, body)
        if raced is None:
            raise
        event, delivery = raced
        if not _content_matches(event, body):
            raise HTTPException(
                status_code=409,
                detail="source_event_id was already used with different content",
            ) from None
        return event, delivery

    session.refresh(event)
    session.refresh(delivery)
    return event, delivery
