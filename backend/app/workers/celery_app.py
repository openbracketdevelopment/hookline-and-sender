from celery import Celery
from celery.signals import setup_logging

from backend.app.config import get_settings
from backend.app.logging_config import configure_logging


@setup_logging.connect
def setup_worker_logging(**_kwargs: object) -> None:
    configure_logging()


settings = get_settings()

celery_app = Celery(
    "hookline",
    broker=settings.redis_url,
    include=["backend.app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=settings.dependency_timeout_seconds,
    task_publish_retry=False,
    broker_transport_options={
        "socket_connect_timeout": settings.dependency_timeout_seconds,
        "socket_timeout": settings.dependency_timeout_seconds,
        "retry_on_timeout": False,
    },
)
