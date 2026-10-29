"""Create events, deliveries, and delivery attempts.

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("destination_id", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "source_event_id",
            "destination_id",
            name="uq_events_producer_destination",
        ),
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.String(length=100), nullable=False),
        sa.Column("request_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deliveries_dispatch",
        "deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_deliveries_lease",
        "deliveries",
        ["status", "lease_expires_at"],
    )
    op.create_index("ix_deliveries_created_at", "deliveries", ["created_at"])
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("error_detail", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(["delivery_id"], ["deliveries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_id", "attempt_number", name="uq_attempts_delivery_number"
        ),
    )


def downgrade() -> None:
    op.drop_table("delivery_attempts")
    op.drop_index("ix_deliveries_created_at", table_name="deliveries")
    op.drop_index("ix_deliveries_lease", table_name="deliveries")
    op.drop_index("ix_deliveries_dispatch", table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_table("events")
