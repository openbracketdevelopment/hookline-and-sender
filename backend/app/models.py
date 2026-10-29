from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


json_type = JSON().with_variant(JSONB(), "postgresql")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_event_id",
            "destination_id",
            name="uq_events_producer_destination",
        ),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delivery: Mapped["Delivery"] = relationship(back_populates="event", uselist=False)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        Index("ix_deliveries_dispatch", "status", "next_attempt_at"),
        Index("ix_deliveries_lease", "status", "lease_expires_at"),
        Index("ix_deliveries_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[str] = mapped_column(String(100), nullable=False)
    request_body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    event: Mapped[Event] = relationship(back_populates="delivery")
    attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan"
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_id", "attempt_number", name="uq_attempts_delivery_number"
        ),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    delivery: Mapped[Delivery] = relationship(back_populates="attempts")
