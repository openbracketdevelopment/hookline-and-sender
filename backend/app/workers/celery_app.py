from celery import Celery

from backend.app.config import get_settings

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
)
