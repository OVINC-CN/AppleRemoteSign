import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.app import create_app
from apple_remote_sign.domain import ApiError, OperationError
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip
from tests.fakes import TEST_APPLICATION_ID
from tests.test_web_api import login_as


@pytest.fixture
def accepted_log():
    path = Path(__file__).resolve().parents[1] / "src/web/src/test-fixtures/notary-accepted.json"
    return json.loads(path.read_text())


@pytest.fixture
def notary_job(context, accepted_log):
    job = context.repository.create(TEST_APPLICATION_ID, accepted_log["archiveFilename"])
    job.submission_id = accepted_log["jobId"]
    job.status = "completed"
    context.repository.save(job)
    return job


@pytest.fixture
def processing_job(context, accepted_log, apple):
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip())
    )
    accepted_log["jobId"] = job.submission_id
    apple.current = "Accepted"
    apple.log_data = accepted_log
    return job


@pytest.fixture
def web(settings, context):
    with TestClient(create_app(settings, context), raise_server_exceptions=False) as client:
        login_as(client, context, settings, "admin")
        yield client


def endpoint(job):
    return f"/api/v1/jobs/{job.id}/notary-details"


@pytest.mark.parametrize("source", ["web", "openapi"])
def test_success_prefetches_topology_details_after_completion_commit(
    web, context, processing_job, accepted_log, apple, artifacts, monkeypatch, source
):
    fetch_log = apple.log

    def check_committed(identifier):
        saved = context.repository.get(processing_job.id)
        assert saved.status == "completed"
        assert saved.artifact_s3_key in artifacts.objects
        return fetch_log(identifier)

    monkeypatch.setattr(apple, "log", check_committed)
    method, path = (
        ("POST", f"/api/v1/jobs/{processing_job.id}/refresh")
        if source == "web"
        else ("GET", f"/openapi/v1/jobs/{processing_job.id}")
    )
    response = web.request(method, path, headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200 and response.json()["data"]["status"] == "completed"
    stored = context.repository.get(processing_job.id)
    assert json.loads(stored.notary_log) == accepted_log
    assert len(json.loads(stored.notary_log)["ticketContents"]) == 4
    assert apple.calls == [processing_job.submission_id]
    assert apple.logs == [processing_job.submission_id]
    apple.error = "Cached details must not call Apple"
    response = web.get(endpoint(processing_job))
    assert response.status_code == 200 and response.json()["data"] == accepted_log
    assert apple.calls == [processing_job.submission_id]
    assert apple.logs == [processing_job.submission_id]


def test_success_prefetch_redacts_values_and_preserves_topology(
    web, context, processing_job, accepted_log, apple
):
    accepted_log["extra"] = {"url": "https://logs.example/file?signature=test-token"}
    accepted_log["issues"] = [{"severity": "warning", "message": 'Quote " and Bearer test-token'}]
    assert QueryService(context, TEST_APPLICATION_ID).query(processing_job.id).status == "completed"
    serialized = context.repository.get(processing_job.id).notary_log
    assert "test-token" not in serialized
    cached = json.loads(serialized)
    assert cached["extra"]["url"] == "https://logs.example/file?[REDACTED]"
    assert cached["issues"][0]["message"] == 'Quote " and Bearer [REDACTED]'
    assert cached["ticketContents"] == accepted_log["ticketContents"]
    response = web.get(endpoint(processing_job))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["extra"] == cached["extra"]
    assert data["issues"][0]["message"] == cached["issues"][0]["message"]
    assert data["ticketContents"] == cached["ticketContents"]
    assert apple.calls == [processing_job.submission_id]
    assert apple.logs == [processing_job.submission_id]


@pytest.mark.parametrize("failure", ["log", "wrong_id", "wrong_status", "bad_ticket", "database"])
def test_prefetch_failure_only_logs_and_details_can_backfill_later(
    web, context, processing_job, accepted_log, apple, artifacts, monkeypatch, caplog, failure
):
    valid_log = json.loads(json.dumps(accepted_log))
    save = context.repository.save

    def unavailable(job):
        if job.notary_log:
            raise SQLAlchemyError("private-database-password")
        save(job)

    def failed_log(_identifier):
        raise OperationError("Log unavailable: Bearer test-token")

    with monkeypatch.context() as patch:
        if failure == "log":
            patch.setattr(apple, "log", failed_log)
        elif failure == "wrong_id":
            accepted_log["jobId"] = str(uuid4())
        elif failure == "wrong_status":
            accepted_log["status"] = "Invalid"
        elif failure == "bad_ticket":
            accepted_log["ticketContents"] = [{"path": 42}]
        else:
            patch.setattr(context.repository, "save", unavailable)
        response = web.post(f"/api/v1/jobs/{processing_job.id}/refresh")
    assert response.status_code == 200 and response.json()["data"]["status"] == "completed"
    stored = context.repository.get(processing_job.id)
    assert stored.status == "completed" and stored.error_message == ""
    assert stored.artifact_s3_key in artifacts.objects
    assert stored.notary_log is None and "[notary-log]" not in stored.diagnostics
    assert "job.notary_details.prefetch_failed" in caplog.text
    assert "test-token" not in caplog.text and "private-database-password" not in caplog.text
    assert web.get(f"/api/v1/jobs/{processing_job.id}/artifact-url").status_code == 200
    apple.log_data = valid_log
    response = web.get(endpoint(processing_job))
    assert response.status_code == 200 and response.json()["data"] == valid_log
    assert json.loads(context.repository.get(processing_job.id).notary_log) == valid_log


def test_refresh_retries_missing_details_without_repeating_delivery(
    context, processing_job, apple, artifacts
):
    service = QueryService(context, TEST_APPLICATION_ID)
    apple.log_error = True
    assert service.query(processing_job.id).status == "completed"
    assert context.repository.get(processing_job.id).notary_log is None
    apple.log_error = False
    for _ in range(2):
        assert service.query(processing_job.id).status == "completed"
    assert context.repository.get(processing_job.id).notary_log is not None
    assert apple.calls == [processing_job.submission_id] * 3
    assert apple.logs == [processing_job.submission_id] * 2
    assert len(artifacts.calls) == 1


@pytest.mark.parametrize("outcome", ["processing", "staple", "upload"])
def test_unfinished_jobs_do_not_prefetch_details(
    context, processing_job, apple, tool, artifacts, outcome
):
    service = QueryService(context, TEST_APPLICATION_ID)
    if outcome == "processing":
        apple.current = "In Progress"
        assert service.query(processing_job.id).status == "processing"
    else:
        tool.fail_staple = outcome == "staple"
        artifacts.error = "S3 unavailable" if outcome == "upload" else None
        with pytest.raises(ApiError):
            service.query(processing_job.id)
    assert context.repository.get(processing_job.id).notary_log is None
    assert apple.logs == []


def test_cached_accepted_log_never_queries_apple_or_requires_a_lease(
    web, context, notary_job, accepted_log, apple, redis
):
    notary_job.notary_log = json.dumps(accepted_log)
    context.repository.save(notary_job)
    before = context.repository.get(notary_job.id)
    redis.state.available = False
    response = web.get(endpoint(notary_job))
    redis.state.available = True
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"data": accepted_log, "error_message": ""}
    assert apple.calls == [] and apple.logs == []
    assert context.repository.get(notary_job.id).updated_at == before.updated_at


@pytest.mark.parametrize("status", ["completed", "failed", "processing"])
def test_backfills_accepted_details_once_without_signing_or_changing_status(
    web, context, notary_job, accepted_log, apple, tool, artifacts, status
):
    notary_job.status = status
    notary_job.error_message = "Existing delivery failure" if status == "failed" else ""
    context.repository.save(notary_job)
    apple.current = "Accepted"
    apple.log_data = accepted_log
    for _ in range(2):
        response = web.get(endpoint(notary_job))
        assert response.status_code == 200
        assert response.json()["data"] == accepted_log
    stored = context.repository.get(notary_job.id)
    assert json.loads(stored.notary_log) == accepted_log
    assert len(json.loads(stored.notary_log)["ticketContents"]) == 4
    assert stored.status == status and stored.error_message == notary_job.error_message
    assert stored.last_checked_at == notary_job.last_checked_at
    assert apple.calls == [notary_job.submission_id]
    assert apple.logs == [notary_job.submission_id]
    assert tool.calls == [] and artifacts.calls == []


@pytest.mark.parametrize("status", ["Invalid", "Rejected", None])
def test_cached_rejection_including_legacy_logs_does_not_query_apple(
    web, context, notary_job, apple, status
):
    value = {"statusSummary": "Invalid archive", "issues": [{"message": "Rejected"}]}
    if status:
        value["status"] = status
    notary_job.notary_log = json.dumps(value)
    context.repository.save(notary_job)
    response = web.get(endpoint(notary_job))
    assert response.status_code == 200 and response.json()["data"] is None
    assert apple.calls == [] and apple.logs == []


@pytest.mark.parametrize("status", ["In Progress", "Invalid", "Rejected"])
def test_unaccepted_jobs_do_not_download_logs_or_change_state(
    web, context, notary_job, apple, status
):
    apple.current = status
    before = context.repository.get(notary_job.id)
    response = web.get(endpoint(notary_job))
    assert response.status_code == 200 and response.json()["data"] is None
    stored = context.repository.get(notary_job.id)
    assert stored.status == before.status and stored.updated_at == before.updated_at
    assert stored.notary_log is None and apple.logs == []


def test_unsubmitted_jobs_do_not_query_apple(web, context, apple):
    job = context.repository.create(TEST_APPLICATION_ID, "pending-1.2.3.zip")
    response = web.get(endpoint(job))
    assert response.status_code == 200 and response.json()["data"] is None
    assert apple.calls == [] and apple.logs == []


@pytest.mark.parametrize("tickets", [[], None])
def test_empty_tickets_are_valid_accepted_details(web, notary_job, accepted_log, apple, tickets):
    accepted_log["ticketContents"] = tickets
    apple.current = "Accepted"
    apple.log_data = accepted_log
    response = web.get(endpoint(notary_job))
    assert response.status_code == 200
    assert response.json()["data"]["ticketContents"] == tickets


def test_ticket_architecture_can_be_absent(web, context, notary_job, accepted_log, apple):
    del accepted_log["ticketContents"][0]["arch"]
    apple.current = "Accepted"
    apple.log_data = accepted_log
    response = web.get(endpoint(notary_job))
    assert response.status_code == 200
    assert response.json()["data"]["ticketContents"][0]["arch"] is None
    stored = json.loads(context.repository.get(notary_job.id).notary_log)
    assert "arch" not in stored["ticketContents"][0]


def test_requires_web_login_and_job_access(web, context, settings, notary_job, apple):
    web.cookies.clear()
    response = web.get(endpoint(notary_job), headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 401
    login_as(web, context, settings, "alice")
    assert web.get(endpoint(notary_job)).status_code == 404
    assert web.get(f"/api/v1/jobs/{uuid4()}/notary-details").status_code == 404
    AccessRepository(context.repository.sessions).set_users(TEST_APPLICATION_ID, ["alice"])
    assert web.get(endpoint(notary_job)).status_code == 200
    AccessRepository(context.repository.sessions).set_users(TEST_APPLICATION_ID, [])
    assert web.get(endpoint(notary_job)).status_code == 404
    assert apple.logs == []


def test_busy_job_returns_immediately_without_querying_apple(web, context, notary_job, apple):
    with ThreadPoolExecutor(max_workers=1) as pool, context.locks.hold(notary_job.id):
        response = pool.submit(web.get, endpoint(notary_job)).result(timeout=2)
    assert response.status_code == 409
    assert apple.calls == [] and apple.logs == []


def test_rechecks_database_after_acquiring_the_lease(
    web, context, notary_job, accepted_log, apple, monkeypatch
):
    hold = context.hold_job

    @contextmanager
    def populated_while_waiting(snapshot, *, blocking=True):
        with hold(snapshot, blocking=blocking) as acquired:
            if acquired:
                current = context.repository.get(snapshot.id)
                current.notary_log = json.dumps(accepted_log)
                context.repository.save(current)
            yield acquired

    monkeypatch.setattr(context, "hold_job", populated_while_waiting)
    response = web.get(endpoint(notary_job))
    assert response.status_code == 200 and response.json()["data"] == accepted_log
    assert apple.calls == [] and apple.logs == []


@pytest.mark.parametrize("failure", ["status", "log", "wrong_id", "wrong_status", "bad_ticket"])
def test_upstream_failures_preserve_the_job_and_do_not_cache_invalid_data(
    web, context, notary_job, accepted_log, apple, failure
):
    apple.current = "Accepted"
    apple.log_data = accepted_log
    if failure == "status":
        apple.error = "Request failed: Bearer test-token"
    elif failure == "log":
        apple.log_error = True
    elif failure == "wrong_id":
        accepted_log["jobId"] = str(uuid4())
    elif failure == "wrong_status":
        accepted_log["status"] = "Invalid"
    else:
        accepted_log["ticketContents"] = [{"path": 42}]
    before = context.repository.get(notary_job.id)
    response = web.get(endpoint(notary_job))
    assert response.status_code == 502 and "test-token" not in response.text
    stored = context.repository.get(notary_job.id)
    assert stored.notary_log is None
    assert stored.status == before.status and stored.updated_at == before.updated_at


def test_redacts_json_values_and_preserves_unknown_fields_and_warnings(
    web, context, notary_job, accepted_log, apple
):
    accepted_log["extra"] = {"url": "https://logs.example/file?signature=test-token"}
    accepted_log["issues"] = [{"severity": "warning", "message": 'Quote " and Bearer test-token'}]
    apple.current = "Accepted"
    apple.log_data = accepted_log
    response = web.get(endpoint(notary_job))
    assert response.status_code == 200 and "test-token" not in response.text
    data = response.json()["data"]
    assert data["extra"]["url"] == "https://logs.example/file?[REDACTED]"
    assert data["issues"][0]["message"] == 'Quote " and Bearer [REDACTED]'
    stored = json.loads(context.repository.get(notary_job.id).notary_log)
    assert stored["extra"] == data["extra"]
    assert stored["issues"][0]["message"] == data["issues"][0]["message"]


@pytest.mark.parametrize("value", ["invalid JSON", "[]", '{"status":"Accepted"}'])
def test_invalid_cached_data_is_not_overwritten_or_refetched(
    web, context, notary_job, apple, value
):
    notary_job.notary_log = value
    context.repository.save(notary_job)
    assert web.get(endpoint(notary_job)).status_code == 500
    assert context.repository.get(notary_job.id).notary_log == value
    assert apple.calls == [] and apple.logs == []


def test_database_failure_does_not_claim_details_were_saved(
    web, context, notary_job, accepted_log, apple, monkeypatch
):
    apple.current = "Accepted"
    apple.log_data = accepted_log

    def unavailable(_job):
        raise SQLAlchemyError("private-database-password")

    monkeypatch.setattr(context.repository, "save", unavailable)
    response = web.get(endpoint(notary_job))
    assert response.status_code == 500 and "private-database-password" not in response.text
    assert context.repository.get(notary_job.id).notary_log is None


def test_lost_lease_does_not_save_details(
    web, context, notary_job, accepted_log, apple, redis, monkeypatch
):
    apple.current = "Accepted"

    def lose_lease(_identifier):
        redis.state.expire(notary_job.id)
        return accepted_log

    monkeypatch.setattr(apple, "log", lose_lease)
    assert web.get(endpoint(notary_job)).status_code == 503
    assert context.repository.get(notary_job.id).notary_log is None
