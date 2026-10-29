import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models import Delivery, Event
from backend.app.schemas import (
    AttemptSummary,
    DeliveryDetail,
    DeliveryPage,
    DeliveryStatus,
    DeliverySummary,
    EventAccepted,
    EventCreate,
    EventSummary,
)
from backend.app.services.events import accept_event

bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    settings = get_settings()
    expected = settings.api_key.get_secret_value()
    supplied = credentials.credentials if credentials else ""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


def _event_summary(event: Event, delivery: Delivery) -> EventSummary:
    return EventSummary(
        id=event.id,
        source=event.source,
        source_event_id=event.source_event_id,
        type=event.event_type,
        data=event.payload,
        accepted_at=event.accepted_at,
        delivery_id=delivery.id,
        destination_id=delivery.destination_id,
        delivery_status=delivery.status,
    )


def _delivery_summary(delivery: Delivery) -> DeliverySummary:
    return DeliverySummary(
        id=delivery.id,
        event_id=delivery.event_id,
        destination_id=delivery.destination_id,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        next_attempt_at=delivery.next_attempt_at,
        last_error=delivery.last_error,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
    )


@router.post(
    "/events",
    response_model=EventAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_event(
    body: EventCreate,
    session: Annotated[Session, Depends(get_db)],
) -> EventAccepted:
    event, delivery = accept_event(session, body)
    return EventAccepted(
        event_id=event.id,
        delivery_id=delivery.id,
        status_url=f"/api/v1/deliveries/{delivery.id}",
    )


@router.get("/events/{event_id}", response_model=EventSummary)
def get_event(
    event_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> EventSummary:
    result = session.execute(
        select(Event, Delivery)
        .join(Delivery, Delivery.event_id == Event.id)
        .where(Event.id == str(event_id))
    ).first()
    if result is None:
        raise HTTPException(status_code=404, detail="Event not found")
    event, delivery = result
    return _event_summary(event, delivery)


@router.get("/deliveries", response_model=DeliveryPage)
def list_deliveries(
    session: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    delivery_status: Annotated[DeliveryStatus | None, Query(alias="status")] = None,
) -> DeliveryPage:
    statement = select(Delivery).order_by(
        Delivery.created_at.desc(), Delivery.id.desc()
    )
    if delivery_status is not None:
        statement = statement.where(Delivery.status == delivery_status)
    items = session.scalars(statement.limit(limit).offset(offset)).all()
    return DeliveryPage(
        items=[_delivery_summary(delivery) for delivery in items],
        limit=limit,
        offset=offset,
    )


@router.get("/deliveries/{delivery_id}", response_model=DeliveryDetail)
def get_delivery(
    delivery_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> DeliveryDetail:
    delivery = session.scalar(
        select(Delivery)
        .options(joinedload(Delivery.attempts))
        .where(Delivery.id == str(delivery_id))
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    summary = _delivery_summary(delivery)
    return DeliveryDetail(
        **summary.model_dump(),
        attempts=[
            AttemptSummary(
                id=attempt.id,
                attempt_number=attempt.attempt_number,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                http_status=attempt.http_status,
                duration_ms=attempt.duration_ms,
                outcome=attempt.outcome,
                error_detail=attempt.error_detail,
            )
            for attempt in sorted(
                delivery.attempts, key=lambda item: item.attempt_number
            )
        ],
    )


health_router = APIRouter(prefix="/health")


@health_router.get("/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@health_router.get("/ready")
def ready() -> dict[str, str]:
    from backend.app.database import engine

    timeout = get_settings().dependency_timeout_seconds
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT id FROM deliveries LIMIT 0"))
        with Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            client.ping()
    except (SQLAlchemyError, RedisError) as exc:
        logger.warning(
            "readiness dependency check failed", extra={"outcome": type(exc).__name__}
        )
        raise HTTPException(
            status_code=503, detail="Required dependency unavailable"
        ) from exc
    return {"status": "ready", "checked_at": datetime.now(UTC).isoformat()}
