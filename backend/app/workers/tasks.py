import logging

from celery import Task

from backend.app.logging_config import configure_logging
from backend.app.services.delivery import process_delivery
from backend.app.workers.celery_app import celery_app

configure_logging()
logger = logging.getLogger(__name__)


class DeliverTask(Task):
    name = "hookline.deliver"
    ignore_result = True

    def run(self, delivery_id: str) -> None:
        logger.info("delivery task received", extra={"delivery_id": delivery_id})
        process_delivery(delivery_id)


deliver_task = DeliverTask()
celery_app.register_task(deliver_task)
