import json
import logging
import multiprocessing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from backend.app.logging_config import ActivityFileHandler, log_after_commit
from backend.app.models import Delivery
from backend.app.services import delivery as delivery_service
from tests.test_api import AUTH, EVENT
from tests.test_delivery import _create_delivery


@pytest.fixture
def activity_file(tmp_path):
    logger = logging.getLogger("hookline.activity")
    handler = ActivityFileHandler(tmp_path)
    logger.addHandler(handler)
    yield tmp_path / "webhooks.jsonl"
    logger.removeHandler(handler)
    handler.close()


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize(
    ("http_status", "status"),
    [(200, "succeeded"), (503, "retry_scheduled"), (400, "failed")],
)
def test_activity_logs_acceptance_and_committed_results(
    client, monkeypatch, activity_file, http_status, status
):
    body = {**EVENT, "data": {"private": "do-not-log-this-payload"}}
    delivery_id = client.post("/api/v1/events", json=body, headers=AUTH).json()[
        "delivery_id"
    ]
    monkeypatch.setattr(delivery_service, "_send", lambda claim: (http_status, None))
    delivery_service.process_delivery(delivery_id)
    entries = records(activity_file)
    assert [entry["status"] for entry in entries] == ["pending", "in_progress", status]
    assert all(entry["delivery_id"] == delivery_id for entry in entries)
    assert entries[-1]["http_status"] == http_status
    assert entries[-1]["attempt_number"] == 1
    assert entries[-1]["error_detail"] == (
        None if http_status == 200 else f"HTTP {http_status}"
    )
    text = activity_file.read_text()
    assert "do-not-log-this-payload" not in text
    assert AUTH["Authorization"] not in text
    assert activity_file.stat().st_mode & 0o777 == 0o600


def test_rollback_does_not_log_success(db_session_factory, activity_file):
    with db_session_factory() as session:
        session.begin()
        log_after_commit(session, "must not appear", status="succeeded")
        session.rollback()
        with session.begin():
            log_after_commit(session, "committed", status="pending")
    assert [entry["message"] for entry in records(activity_file)] == ["committed"]


def test_duplicate_submission_is_not_logged_as_another_acceptance(
    client, activity_file
):
    first = client.post("/api/v1/events", json=EVENT, headers=AUTH)
    duplicate = client.post("/api/v1/events", json=EVENT, headers=AUTH)
    assert first.json() == duplicate.json()
    assert len(records(activity_file)) == 1


@pytest.mark.parametrize("at_limit", [False, True])
def test_worker_lease_recovery_is_logged(
    db_session_factory, monkeypatch, activity_file, at_limit
):
    delivery_id = _create_delivery(
        db_session_factory,
        status="in_progress",
        attempt_count=5 if at_limit else 1,
        lease=datetime.now(UTC) - timedelta(seconds=1),
    )
    monkeypatch.setattr(delivery_service, "_send", lambda claim: (200, None))
    delivery_service.process_delivery(delivery_id)
    entries = records(activity_file)
    assert entries[0]["outcome"] == "worker_lost"
    assert entries[-1]["status"] == ("failed" if at_limit else "succeeded")


def test_failed_completion_commit_does_not_log_result(
    client, monkeypatch, db_session_factory, activity_file
):
    delivery_id = client.post("/api/v1/events", json=EVENT, headers=AUTH).json()[
        "delivery_id"
    ]
    claim = delivery_service.claim_delivery(delivery_id)

    def fail_commit(session):
        raise RuntimeError("commit failed")

    event.listen(db_session_factory, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="commit failed"):
            delivery_service._complete(
                claim,
                outcome="succeeded",
                http_status=200,
                error_detail=None,
                duration_ms=1,
            )
    finally:
        event.remove(db_session_factory, "before_commit", fail_commit)
    assert [entry["status"] for entry in records(activity_file)] == [
        "pending",
        "in_progress",
    ]
    with db_session_factory() as session:
        assert session.get(Delivery, delivery_id).status == "in_progress"


def _write_activity(directory, worker, count, max_bytes):
    handler = ActivityFileHandler(Path(directory), max_bytes=max_bytes, backups=2)
    logger = logging.getLogger(f"activity-process-{worker}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    for number in range(count):
        logger.info("test activity", extra={"delivery_id": f"{worker}-{number}"})
    handler.close()


def test_multiple_processes_write_complete_records(tmp_path):
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_write_activity, args=(str(tmp_path), worker, 30, 100000)
        )
        for worker in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    entries = records(tmp_path / "webhooks.jsonl")
    assert len(entries) == 90
    assert len({entry["delivery_id"] for entry in entries}) == 90


def test_rotation_is_bounded_and_valid_across_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_write_activity, args=(str(tmp_path), worker, 30, 1000))
        for worker in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    files = list(tmp_path.glob("webhooks.jsonl*"))
    assert len(files) == 3
    assert all(records(path) for path in files)
    assert all(path.stat().st_size <= 1000 for path in files)


def test_logging_failure_is_visible_without_losing_database_history(
    client, monkeypatch, activity_file, capsys
):
    def unavailable(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(ActivityFileHandler, "_open", unavailable)
    response = client.post("/api/v1/events", headers=AUTH, json=EVENT)
    assert response.status_code == 202
    assert "Webhook activity file could not be written" in capsys.readouterr().err
    delivery_id = response.json()["delivery_id"]
    assert (
        client.get(f"/api/v1/deliveries/{delivery_id}", headers=AUTH).status_code == 200
    )
