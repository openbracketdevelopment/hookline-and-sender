import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.models import Delivery, DeliveryAttempt
from backend.app.security import sign_request

logger = logging.getLogger(__name__)
TERMINAL_STATES = {"succeeded", "failed"}


@dataclass(frozen=True)
class DeliveryClaim:
    delivery_id: str
    event_id: str
    attempt_id: str
    attempt_number: int
    claim_token: str
    request_body: bytes


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def claim_delivery(delivery_id: str) -> DeliveryClaim | None:
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal.begin() as session:
        delivery = session.scalar(
            select(Delivery).where(Delivery.id == delivery_id).with_for_update()
        )
        if delivery is None or delivery.status in TERMINAL_STATES:
            return None

        if delivery.status == "in_progress":
            if (
                delivery.lease_expires_at is None
                or _utc(delivery.lease_expires_at) > now
            ):
                return None
            previous = session.scalar(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.delivery_id == delivery.id)
                .order_by(DeliveryAttempt.attempt_number.desc())
                .limit(1)
            )
            if previous is not None and previous.completed_at is None:
                previous.completed_at = now
                previous.duration_ms = max(
                    0, int((now - _utc(previous.started_at)).total_seconds() * 1000)
                )
                previous.outcome = "worker_lost"
                previous.error_detail = "Worker lease expired before completion"
            if delivery.attempt_count >= settings.max_attempts:
                delivery.status = "failed"
                delivery.last_error = "Worker lease expired at attempt limit"
                delivery.next_attempt_at = None
                delivery.lease_expires_at = None
                delivery.claim_token = None
                delivery.updated_at = now
                logger.warning(
                    "delivery failed after worker lease expired",
                    extra={
                        "event_id": delivery.event_id,
                        "delivery_id": delivery.id,
                        "attempt_number": delivery.attempt_count,
                        "status": delivery.status,
                    },
                )
                return None
            delivery.status = "retry_scheduled"
            delivery.next_attempt_at = now
            delivery.lease_expires_at = None
            delivery.claim_token = None
        elif delivery.status not in {"pending", "retry_scheduled"}:
            return None

        if (
            delivery.next_attempt_at is not None
            and _utc(delivery.next_attempt_at) > now
        ):
            return None
        if delivery.attempt_count >= settings.max_attempts:
            delivery.status = "failed"
            delivery.next_attempt_at = None
            delivery.updated_at = now
            return None

        number = delivery.attempt_count + 1
        token = str(uuid4())
        attempt = DeliveryAttempt(
            id=str(uuid4()),
            delivery_id=delivery.id,
            attempt_number=number,
            started_at=now,
            outcome="in_progress",
        )
        session.add(attempt)
        delivery.attempt_count = number
        delivery.status = "in_progress"
        delivery.lease_expires_at = now + timedelta(
            seconds=settings.request_lease_seconds
        )
        delivery.claim_token = token
        delivery.next_attempt_at = None
        delivery.last_dispatched_at = None
        delivery.updated_at = now
        claim = DeliveryClaim(
            delivery_id=delivery.id,
            event_id=delivery.event_id,
            attempt_id=attempt.id,
            attempt_number=number,
            claim_token=token,
            request_body=delivery.request_body.encode("utf-8"),
        )
    return claim


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - now).total_seconds())


def _retry_delay(attempt_number: int, retry_after: float | None) -> float:
    settings = get_settings()
    exponential = min(
        settings.retry_max_seconds,
        settings.retry_base_seconds * (2 ** max(0, attempt_number - 1)),
    )
    minimum = max(exponential, min(retry_after or 0.0, settings.retry_max_seconds))
    jitter_limit = min(0.25, max(0.0, settings.retry_max_seconds - minimum))
    return min(settings.retry_max_seconds, minimum + random.uniform(0, jitter_limit))


def _send(claim: DeliveryClaim) -> tuple[int, str | None]:
    settings = get_settings()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Hookline-Timestamp": timestamp,
        "X-Hookline-Signature": sign_request(
            settings.demo_signing_secret.get_secret_value(),
            timestamp,
            claim.request_body,
        ),
        "X-Hookline-Event-Id": claim.event_id,
        "X-Hookline-Delivery-Id": claim.delivery_id,
    }
    timeout = httpx.Timeout(
        connect=settings.request_timeout_seconds,
        read=settings.request_timeout_seconds,
        write=settings.request_timeout_seconds,
        pool=settings.request_timeout_seconds,
    )
    with httpx.Client(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        with client.stream(
            "POST",
            settings.demo_receiver_url,
            content=claim.request_body,
            headers=headers,
        ) as response:
            for _chunk in response.iter_bytes(chunk_size=1024):
                break
            return response.status_code, response.headers.get("Retry-After")


def _complete(
    claim: DeliveryClaim,
    *,
    outcome: str,
    http_status: int | None,
    error_detail: str | None,
    duration_ms: int,
    retry_after: str | None = None,
) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal.begin() as session:
        delivery = session.scalar(
            select(Delivery).where(Delivery.id == claim.delivery_id).with_for_update()
        )
        if (
            delivery is None
            or delivery.claim_token != claim.claim_token
            or delivery.status != "in_progress"
        ):
            return
        attempt = session.get(DeliveryAttempt, claim.attempt_id)
        if attempt is None:
            return
        attempt.completed_at = now
        attempt.http_status = http_status
        attempt.duration_ms = duration_ms
        attempt.outcome = outcome
        attempt.error_detail = error_detail[:256] if error_detail else None
        delivery.lease_expires_at = None
        delivery.claim_token = None
        delivery.updated_at = now

        if outcome == "succeeded":
            delivery.status = "succeeded"
            delivery.last_error = None
            delivery.next_attempt_at = None
        elif outcome in {"retryable_http_error", "network_error", "timeout"}:
            if delivery.attempt_count >= settings.max_attempts:
                delivery.status = "failed"
                delivery.next_attempt_at = None
            else:
                delay = _retry_delay(
                    claim.attempt_number,
                    _retry_after_seconds(retry_after, now)
                    if retry_after is not None
                    else None,
                )
                delivery.status = "retry_scheduled"
                delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.last_error = error_detail[:256] if error_detail else outcome
        else:
            delivery.status = "failed"
            delivery.next_attempt_at = None
            delivery.last_error = error_detail[:256] if error_detail else outcome

        logger.info(
            "delivery attempt completed",
            extra={
                "event_id": delivery.event_id,
                "delivery_id": delivery.id,
                "attempt_number": claim.attempt_number,
                "status": delivery.status,
                "outcome": outcome,
            },
        )


def process_delivery(delivery_id: str) -> None:
    claim = claim_delivery(delivery_id)
    if claim is None:
        return

    started = time.monotonic()
    http_status: int | None = None
    retry_after: str | None = None
    try:
        http_status, retry_after = _send(claim)
    except httpx.TimeoutException as exc:
        outcome = "timeout"
        error = type(exc).__name__
    except httpx.TransportError as exc:
        outcome = "network_error"
        error = type(exc).__name__
    else:
        if 200 <= http_status < 300:
            outcome = "succeeded"
            error = None
        elif http_status in {408, 429} or 500 <= http_status < 600:
            outcome = "retryable_http_error"
            error = f"HTTP {http_status}"
        else:
            outcome = "terminal_http_error"
            error = f"HTTP {http_status}"

    _complete(
        claim,
        outcome=outcome,
        http_status=http_status,
        error_detail=error,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        retry_after=retry_after,
    )
