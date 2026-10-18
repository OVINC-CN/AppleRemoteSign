import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import ApiError, OperationError
from apple_remote_sign.integrations.wecom import WeComNotifier
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.recovery import RecoveryWorker
from apple_remote_sign.services.history import HistoryService
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip
from tests.fake_redis import FakeRedis
from tests.fakes import TEST_APPLICATION_ID
from tests.test_web_api import login_as
from tests.test_wecom import WEBHOOK_URL


@pytest.fixture
def notifications(context, monkeypatch):
    messages = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload["msgtype"] == "markdown"
        messages.append(payload["markdown"]["content"])
        return httpx.Response(200, json={"errcode": 0})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        context.notifier = WeComNotifier(client, SecretStr(WEBHOOK_URL))
        notify = context.notifier.notify

        def check_committed(job, event, **details):
            saved = context.repository.get(job.id)
            assert saved is not None
            assert (
                saved.status
                == {
                    "submitted": "preparing",
                    "completed": "completed",
                    "failed": "failed",
                }[event]
            )
            if event == "failed":
                assert saved.error_message == job.error_message
            notify(job, event, **details)

        monkeypatch.setattr(context.notifier, "notify", check_committed)
        yield messages


@pytest.mark.parametrize("source", ["web", "openapi"])
def test_both_routes_notify_once_per_new_task_and_committed_completion(
    client, context, settings, apple, artifacts, notifications, source, monkeypatch
):
    if source == "web":
        login_as(client, context, settings, "admin")
    upload_path = (
        f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs"
        if source == "web"
        else "/openapi/v1/jobs"
    )
    notify = context.notifier.notify

    def check_committed(job, event, **details):
        saved = context.repository.get(job.id)
        assert saved is not None
        if event == "submitted":
            assert saved.status == "preparing"
            assert saved.submission_id is None
        else:
            assert saved.status == "completed"
            assert saved.artifact_s3_key in artifacts.objects
        notify(job, event, **details)

    monkeypatch.setattr(context.notifier, "notify", check_committed)

    def upload():
        return client.post(upload_path, files={"file": ("新应用-v1.2.3.zip", dmg_zip())})

    response = upload()
    assert response.status_code == 200
    job_id = response.json()["data"]["id"]
    assert len(notifications) == 1 and "新签名提交" in notifications[0]
    expected_source = "用户 · admin" if source == "web" else "OpenAPI · Test key"
    assert expected_source in notifications[0]
    assert upload().json()["data"]["id"] == job_id

    def during_upload():
        assert len(notifications) == 1
        assert context.repository.get(job_id).status == "processing"

    artifacts.before_upload = during_upload
    apple.current = "Accepted"
    for _ in range(2):
        response = (
            client.post(f"/api/v1/jobs/{job_id}/refresh")
            if source == "web"
            else client.get(f"/openapi/v1/jobs/{job_id}")
        )
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "completed"
    assert upload().json()["data"]["id"] == job_id
    assert len(notifications) == 2 and "签名成功" in notifications[1]
    view = HistoryService(AccessRepository(context.repository.sessions)).view(
        context.repository.get(job_id)
    )
    assert view.source == expected_source
    for title, content in zip(("新签名提交", "签名成功"), notifications, strict=True):
        assert content.splitlines() == [
            f"**AppleRemoteSign · {title}**",
            "**文件名**: 新应用-v1.2.3.zip",
            f"**应用名称**: {view.application.name}",
            f"**任务 ID**: {job_id}",
            f"**提交人**: {view.source}",
        ]


@pytest.mark.parametrize("source", ["web", "openapi"])
@pytest.mark.parametrize("failure", ["prepare", "sign", "submit"])
def test_failed_submission_sends_failure_after_commit(
    client, context, settings, tool, notifications, failure, source, monkeypatch
):
    if source == "web":
        login_as(client, context, settings, "admin")
    upload_path = (
        f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs"
        if source == "web"
        else "/openapi/v1/jobs"
    )
    if failure == "prepare":

        def fail_upload(*args):
            raise OperationError("Disk unavailable")

        monkeypatch.setattr(context.storage, "save_upload", fail_upload)
    else:
        setattr(tool, f"fail_{failure}", True)
    response = client.post(upload_path, files={"file": ("app-1.2.3.zip", dmg_zip())})
    assert response.status_code == 500
    job_id = response.json()["data"]["id"]
    saved = context.repository.get(job_id)
    assert saved.status == "failed" and saved.error_message.startswith(f"{failure}:")
    assert len(notifications) == 2
    assert "新签名提交" in notifications[0]
    expected_source = "用户 · admin" if source == "web" else "OpenAPI · Test key"
    assert notifications[1].splitlines() == [
        "**AppleRemoteSign · 签名失败**",
        "**文件名**: app-1.2.3.zip",
        "**应用名称**: Test app",
        f"**任务 ID**: {job_id}",
        f"**提交人**: {expected_source}",
    ]


@pytest.mark.parametrize("failure", ["staple", "upload", "commit", "apple", "Invalid", "Rejected"])
def test_result_notifications_wait_for_database_commit_and_do_not_repeat_on_retry(
    context, apple, artifacts, tool, notifications, monkeypatch, failure
):
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip())
    )
    apple.current = "Accepted"
    if failure == "staple":
        tool.fail_staple = True
    elif failure == "upload":
        artifacts.error = "S3 unavailable"
    elif failure == "apple":
        apple.error = "Apple unavailable"
    elif failure in {"Invalid", "Rejected"}:
        apple.current = failure
    save = context.repository.save

    def save_with_failure(value):
        if failure == "commit" and value.status == "completed":
            raise SQLAlchemyError("Commit failed")
        save(value)

    monkeypatch.setattr(context.repository, "save", save_with_failure)
    service = QueryService(context, TEST_APPLICATION_ID)
    failure_notifications = int(failure not in {"commit", "apple"})
    for _ in range(2):
        with pytest.raises(ApiError):
            service.query(job.id)
        assert len(notifications) == 1 + failure_notifications
    if failure_notifications:
        assert "签名失败" in notifications[1]
    assert context.repository.get(job.id).status != "completed"
    tool.fail_staple = False
    artifacts.error = apple.error = None
    apple.current = "Accepted"
    monkeypatch.setattr(context.repository, "save", save)
    assert service.query(job.id).status == "completed"
    assert service.query(job.id).status == "completed"
    assert len(notifications) == 2 + failure_notifications
    assert "签名成功" in notifications[-1]


@pytest.mark.parametrize("failure", ["sign", "submit", "staple", "upload", "Invalid", "Rejected"])
def test_failure_notification_waits_for_failure_commit(
    context, apple, tool, artifacts, notifications, monkeypatch, failure
):
    save = context.repository.save

    def save_with_failure(job):
        if job.status == "failed":
            raise SQLAlchemyError("Failure commit unavailable")
        save(job)

    monkeypatch.setattr(context.repository, "save", save_with_failure)
    service = SubmissionService(context, TEST_APPLICATION_ID)
    if failure in {"sign", "submit"}:
        setattr(tool, f"fail_{failure}", True)
        with pytest.raises(ApiError, match="Database update failed"):
            service.submit("app-1.2.3.zip", BytesIO(dmg_zip()))
    else:
        job = service.submit("app-1.2.3.zip", BytesIO(dmg_zip()))
        apple.current = failure if failure in {"Invalid", "Rejected"} else "Accepted"
        tool.fail_staple = failure == "staple"
        artifacts.error = "S3 unavailable" if failure == "upload" else None
        with pytest.raises(ApiError, match="Database update failed"):
            QueryService(context, TEST_APPLICATION_ID).query(job.id)
    assert len(notifications) == 1 and "新签名提交" in notifications[0]


@pytest.mark.parametrize("failure", ["log", "validation", "database"])
def test_prefetch_failure_does_not_prevent_success_notification(
    context, apple, notifications, monkeypatch, caplog, failure
):
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip())
    )
    apple.current = "Accepted"
    if failure == "log":
        apple.log_error = True
    elif failure == "validation":
        apple.log_data = {"status": "Accepted"}
    else:
        save = context.repository.save

        def save_with_failure(value):
            if value.notary_log:
                raise SQLAlchemyError("Notary log commit failed")
            save(value)

        monkeypatch.setattr(context.repository, "save", save_with_failure)
    assert QueryService(context, TEST_APPLICATION_ID).query(job.id).status == "completed"
    saved = context.repository.get(job.id)
    assert saved.status == "completed" and saved.notary_log is None
    assert len(notifications) == 2 and "签名成功" in notifications[1]
    assert "job.notary_details.prefetch_failed" in caplog.text


def test_in_progress_and_invalid_upload_do_not_send_success(client, apple, notifications):
    assert (
        client.post("/openapi/v1/jobs", files={"file": ("no-version.zip", dmg_zip())}).status_code
        == 422
    )
    assert notifications == []
    response = client.post("/openapi/v1/jobs", files={"file": ("app-1.2.3.zip", dmg_zip())})
    job_id = response.json()["data"]["id"]
    assert apple.current == "In Progress"
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == "processing"
    assert len(notifications) == 1


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_webhook_failure_does_not_change_job_or_api_result(
    client, context, apple, notifications, monkeypatch, outcome
):
    attempts = []

    def timeout(*args, **kwargs):
        attempts.append(args)
        raise httpx.ReadTimeout("Webhook timed out")

    monkeypatch.setattr(context.notifier.client, "post", timeout)
    response = client.post("/openapi/v1/jobs", files={"file": ("app-1.2.3.zip", dmg_zip())})
    assert response.status_code == 200
    job_id = response.json()["data"]["id"]
    assert response.json()["data"]["status"] == "processing"
    apple.current = "Accepted" if outcome == "completed" else "Invalid"
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == outcome
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == (
        200 if outcome == "completed" else 500
    )
    saved = context.repository.get(job_id)
    assert saved.status == outcome
    assert bool(saved.error_message) == (outcome == "failed")
    assert len(attempts) == 2
    assert notifications == []


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_notification_metadata_failure_does_not_change_signing_result(
    client, apple, notifications, monkeypatch, caplog, outcome
):
    def fail_view(*args):
        raise SQLAlchemyError(WEBHOOK_URL)

    monkeypatch.setattr(HistoryService, "view", fail_view)
    response = client.post("/openapi/v1/jobs", files={"file": ("app-1.2.3.zip", dmg_zip())})
    assert response.status_code == 200
    job_id = response.json()["data"]["id"]
    apple.current = "Accepted" if outcome == "completed" else "Rejected"
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == outcome
    assert notifications == []
    assert "SQLAlchemyError" in caplog.text
    assert "test-robot-secret" not in caplog.text


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_concurrent_replica_refresh_sends_one_result(context, apple, tool, notifications, outcome):
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip())
    )
    apple.current = "Accepted" if outcome == "completed" else "Invalid"
    tool.staple_delay = 0.05
    other = replace(context, locks=JobLocks(FakeRedis(context.locks.client.state)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(QueryService(ctx, TEST_APPLICATION_ID).query, job.id)
            for ctx in (context, other)
        ]
        for future in futures:
            if outcome == "completed":
                assert future.result(timeout=5).status == "completed"
            else:
                with pytest.raises(ApiError) as raised:
                    future.result(timeout=5)
                assert raised.value.data.status == "failed"
    assert len(notifications) == 2
    title = "签名成功" if outcome == "completed" else "签名失败"
    assert sum(title in content for content in notifications) == 1


def test_recovery_only_notifies_jobs_committed_as_failed(context, notifications):
    abandoned = context.repository.create(TEST_APPLICATION_ID, "abandoned-1.2.3.zip")
    active = context.repository.create(TEST_APPLICATION_ID, "active-1.2.3.zip")
    worker = RecoveryWorker(context)
    with context.locks.hold(active.id):
        worker.scan()
        assert context.repository.get(active.id).status == "preparing"
    assert context.repository.get(abandoned.id).status == "failed"
    assert len(notifications) == 1
    assert "签名失败" in notifications[0] and abandoned.id in notifications[0]
    worker.scan()
    worker.scan()
    assert context.repository.get(active.id).status == "failed"
    assert len(notifications) == 2
    assert "签名失败" in notifications[1] and active.id in notifications[1]


def test_recovery_notification_lookup_failure_does_not_interrupt_recovery(
    context, notifications, monkeypatch, caplog
):
    job = context.repository.create(TEST_APPLICATION_ID, "abandoned-1.2.3.zip")

    def fail_lookup(*args):
        raise SQLAlchemyError(WEBHOOK_URL)

    with monkeypatch.context() as patch:
        patch.setattr(context.repository, "get", fail_lookup)
        RecoveryWorker(context).scan()
    assert context.repository.get(job.id).status == "failed"
    assert notifications == []
    assert "SQLAlchemyError" in caplog.text
    assert "test-robot-secret" not in caplog.text
