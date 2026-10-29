import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

DeliveryStatus = Literal[
    "pending", "in_progress", "retry_scheduled", "succeeded", "failed"
]


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=100)
    source_event_id: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)
    destination_id: str = Field(min_length=1, max_length=100)
    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def require_json_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise PydanticCustomError(
                "invalid_json_data", "data must contain valid JSON values"
            ) from None
        return value


class EventAccepted(BaseModel):
    event_id: UUID
    delivery_id: UUID
    status_url: str


class EventSummary(BaseModel):
    id: UUID
    source: str
    source_event_id: str
    type: str
    data: dict[str, Any]
    accepted_at: datetime
    delivery_id: UUID
    destination_id: str
    delivery_status: DeliveryStatus


class AttemptSummary(BaseModel):
    id: UUID
    attempt_number: int
    started_at: datetime
    completed_at: datetime | None
    http_status: int | None
    duration_ms: int | None
    outcome: str
    error_detail: str | None


class DeliverySummary(BaseModel):
    id: UUID
    event_id: UUID
    destination_id: str
    status: DeliveryStatus
    attempt_count: int
    next_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class DeliveryDetail(DeliverySummary):
    attempts: list[AttemptSummary]


class DeliveryPage(BaseModel):
    items: list[DeliverySummary]
    limit: int
    offset: int
