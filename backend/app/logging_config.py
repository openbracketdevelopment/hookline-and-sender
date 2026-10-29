import fcntl
import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

_CONTEXT_FIELDS = (
    "event_id",
    "delivery_id",
    "attempt_number",
    "status",
    "outcome",
    "http_status",
    "duration_ms",
    "error_detail",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


class ActivityFileHandler(RotatingFileHandler):
    """Serialize rotation across API and Celery processes on macOS/Linux."""

    def __init__(
        self, directory: Path, *, max_bytes: int = 5 * 1024 * 1024, backups: int = 5
    ) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_path = directory / "webhooks.lock"
        super().__init__(
            directory / "webhooks.jsonl",
            maxBytes=max_bytes,
            backupCount=backups,
            encoding="utf-8",
            delay=True,
        )
        self.setFormatter(JsonFormatter())

    def _open(self):
        descriptor = os.open(
            self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600
        )
        return os.fdopen(descriptor, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(descriptor, "a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    super().emit(record)
                finally:
                    # Reopen after every lock: another process may have rotated it.
                    if self.stream is not None:
                        self.stream.close()
                        self.stream = None
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
        except OSError:
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        sys.stderr.write(
            "ERROR: Webhook activity file could not be written. "
            "Check log directory permissions and disk space; "
            "database history remains available.\n"
        )


def log_after_commit(session: Session, message: str, **context: object) -> None:
    transaction = session.get_transaction()

    def committed(_session: Session) -> None:
        if _session.get_transaction() is transaction:
            logging.getLogger("hookline.activity").info(message, extra=context)

    event.listen(session, "after_commit", committed, once=True)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
        existing.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    activity = logging.getLogger("hookline.activity")
    for existing in activity.handlers[:]:
        activity.removeHandler(existing)
        existing.close()
    activity.setLevel(logging.INFO)
    directory = os.environ.get("ACTIVITY_LOG_DIR")
    if directory:
        activity.addHandler(ActivityFileHandler(Path(directory)))
