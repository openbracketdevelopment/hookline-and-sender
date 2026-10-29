import logging
import time
from datetime import UTC, datetime, timedelta

from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.logging_config import configure_logging
from backend.app.models import Delivery
from backend.app.workers.tasks import deliver_task

configure_logging()
logger = logging.getLogger(__name__)


def dispatch_due(limit: int = 100) -> int:
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.dispatch_cooldown_seconds)
    with SessionLocal() as session:
        delivery_ids = session.scalars(
            select(Delivery.id)
            .where(
                or_(
                    (
                        Delivery.status.in_(("pending", "retry_scheduled"))
                        & (Delivery.next_attempt_at <= now)
                    ),
                    (
                        (Delivery.status == "in_progress")
                        & (Delivery.lease_expires_at <= now)
                    ),
                ),
                or_(
                    Delivery.last_dispatched_at.is_(None),
                    Delivery.last_dispatched_at <= cutoff,
                ),
            )
            .order_by(Delivery.next_attempt_at)
            .limit(limit)
        ).all()

    dispatched = 0
    for delivery_id in delivery_ids:
        try:
            deliver_task.apply_async(args=[delivery_id])
        except (OperationalError, RedisError, OSError) as exc:
            logger.error(
                "delivery enqueue failed",
                extra={"delivery_id": delivery_id, "outcome": type(exc).__name__},
            )
            continue

        with SessionLocal.begin() as session:
            delivery = session.get(Delivery, delivery_id)
            if delivery is not None:
                delivery.last_dispatched_at = datetime.now(UTC)
                delivery.updated_at = datetime.now(UTC)
        dispatched += 1
    return dispatched


def main() -> None:
    interval = get_settings().dispatcher_interval_seconds
    logger.info("dispatcher started")
    while True:
        try:
            dispatch_due()
        except SQLAlchemyError:
            logger.exception("dispatcher scan failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
