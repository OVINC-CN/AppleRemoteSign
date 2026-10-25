from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import OperationError
from apple_remote_sign.services.query import QueryService
from tests.archive_helpers import dmg_zip, read_dmg
from tests.test_web_api import login_as

DOWNLOAD_PATHS = [
    "/openapi/v1/jobs/{job_id}/artifact",
    "/api/v1/jobs/{job_id}/artifact-url",
]


def download_url(response):
    if response.status_code == 302:
        assert not response.content
        return response.headers["location"]
    assert response.status_code == 200
    assert "location" not in response.headers
    assert response.json()["error_message"] == ""
    return response.json()["data"]["url"]


def accepted_job(client, apple):
    response = client.post("/openapi/v1/jobs", files={"file": ("release-1.2.3.zip", dmg_zip())})
    apple.current = "Accepted"
    return response.json()["data"]["id"]


def test_job_is_not_completed_until_upload_succeeds(client, context, apple, artifacts):
    job_id = accepted_job(client, apple)

    def during_upload():
        job = context.repository.get(job_id)
        assert job.status == "processing"
        assert job.artifact_s3_key is None
        assert job.artifact_sha256 == context.storage.inspect(job.artifact_path).sha256

    artifacts.before_upload = during_upload
    response = client.get(f"/openapi/v1/jobs/{job_id}")
    assert response.json()["data"]["status"] == "completed"
    job = context.repository.get(job_id)
    assert job.artifact_s3_key in artifacts.objects


@pytest.mark.parametrize("cache_state", ["valid", "missing", "corrupt"])
def test_failed_upload_is_retryable_without_resigning(
    client, context, apple, tool, artifacts, cache_state, monkeypatch
):
    create = context.repository.create

    def create_on_previous_day(*args, **kwargs):
        job = create(*args, **kwargs)
        job.created_at = datetime(2025, 12, 31, 23, 59, 59)
        context.repository.save(job)
        return job

    monkeypatch.setattr(context.repository, "create", create_on_previous_day)
    job_id = accepted_job(client, apple)
    artifacts.error = "S3 upload unavailable"
    response = client.get(f"/openapi/v1/jobs/{job_id}")
    assert response.status_code == 500
    assert response.json()["data"]["status"] == "failed"
    assert "upload: S3 upload unavailable" in response.json()["error_message"]
    assert client.get(f"/openapi/v1/jobs/{job_id}/artifact").status_code == 500
    job = context.repository.get(job_id)
    assert job.artifact_s3_key is None
    path = context.storage.require_file(job.artifact_path)
    if cache_state == "missing":
        path.unlink()
    elif cache_state == "corrupt":
        path.write_bytes(b"corrupt artifact")
    artifacts.error = None
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == "completed"
    job = context.repository.get(job_id)
    directory = f"{job.application_id}/2025/12/31/{job_id}"
    for stage in ("original", "submitted", "artifact"):
        assert getattr(job, f"{stage}_path") == f"{directory}/{stage}/{job.filename}"
    assert context.storage.resolve(f"{directory}/working").is_dir()
    assert job.artifact_s3_key == f"app-dist/{directory}/{job.filename}"
    assert read_dmg(artifacts.objects[job.artifact_s3_key]) == b"original|signed|stapled"
    operations = [name for name, _ in tool.calls]
    assert operations.count("sign") == operations.count("submit") == 1
    assert operations.count("staple") == (1 if cache_state == "valid" else 2)
    assert artifacts.calls == [job.artifact_s3_key, job.artifact_s3_key]


def test_uploaded_artifact_can_retry_after_database_commit_failure(
    client, context, apple, artifacts, tool, monkeypatch
):
    job_id = accepted_job(client, apple)
    save = context.repository.save
    fail_once = True

    def save_with_failure(job):
        nonlocal fail_once
        if job.status == "completed" and fail_once:
            fail_once = False
            raise SQLAlchemyError("Simulated completion commit failure")
        save(job)

    monkeypatch.setattr(context.repository, "save", save_with_failure)
    response = client.get(f"/openapi/v1/jobs/{job_id}")
    assert response.status_code == 500
    job = context.repository.get(job_id)
    assert job.status == "processing" and job.artifact_s3_key is None
    assert client.get(f"/openapi/v1/jobs/{job_id}/artifact").status_code == 409
    assert len(artifacts.objects) == 1
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == "completed"
    assert len(artifacts.objects) == 1
    assert len(artifacts.calls) == 2
    assert [name for name, _ in tool.calls] == ["sign", "submit", "staple"]


@pytest.mark.parametrize("download_path", DOWNLOAD_PATHS)
def test_download_checks_s3_each_time_without_local_files_or_apple(
    client, context, settings, apple, artifacts, download_path, monkeypatch
):
    job_id = accepted_job(client, apple)
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == 200
    job = context.repository.get(job_id)
    context.storage.require_file(job.artifact_path).unlink()
    artifacts.prefix = "changed-prefix/"
    # A completed job is also safe to refresh after its local cache is removed.
    assert QueryService(context, job.application_id).query(job_id).status == "completed"
    assert len(artifacts.calls) == 1
    before = len(apple.calls)
    monkeypatch.setattr(
        context.storage, "require_file", lambda *_args: pytest.fail("Download accessed local disk")
    )
    monkeypatch.setattr(artifacts, "upload", lambda *_args: pytest.fail("Download uploaded to S3"))
    apple.error = "Apple must not be queried"
    if download_path.startswith("/api/"):
        login_as(client, context, settings, "admin")
    responses = [client.get(download_path.format(job_id=job_id)) for _ in range(2)]
    assert download_url(responses[0]) != download_url(responses[1])
    for response in responses:
        url = urlsplit(download_url(response))
        assert url.path == "/" + job.artifact_s3_key
        assert "eo_sign" in parse_qs(url.query)
        assert response.headers["cache-control"] == "no-store"
    assert len(apple.calls) == before
    assert artifacts.lookups == [job.artifact_s3_key, job.artifact_s3_key]


@pytest.mark.parametrize("download_path", DOWNLOAD_PATHS)
@pytest.mark.parametrize("cache_state", ["valid", "missing", "corrupt"])
def test_removed_artifact_is_restored_before_signing_a_link(
    client, context, settings, apple, tool, artifacts, download_path, cache_state, monkeypatch
):
    job_id = accepted_job(client, apple)
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == 200
    job = context.repository.get(job_id)
    if download_path.startswith("/api/"):
        login_as(client, context, settings, "admin")
    path = download_path.format(job_id=job_id)
    assert download_url(client.get(path))
    content = artifacts.objects.pop(job.artifact_s3_key)
    local = context.storage.require_file(job.artifact_path)
    submitted = context.storage.require_file(job.submitted_path).read_bytes()
    if cache_state == "missing":
        local.unlink()
    elif cache_state == "corrupt":
        local.write_bytes(b"corrupt artifact")
    artifacts.prefix = "changed-prefix/"
    before = len(apple.calls)
    apple.error = "Apple must not be queried"
    sign = context.cdn.sign

    def sign_existing(key):
        assert key in artifacts.objects
        assert (
            context.repository.get(job_id).artifact_sha256
            == context.storage.inspect(job.artifact_path).sha256
        )
        return sign(key)

    monkeypatch.setattr(context.cdn, "sign", sign_existing)
    response = client.get(path)
    assert urlsplit(download_url(response)).path == "/" + job.artifact_s3_key
    assert response.headers["cache-control"] == "no-store"
    assert read_dmg(artifacts.objects[job.artifact_s3_key]) == b"original|signed|stapled"
    if cache_state == "valid":
        assert artifacts.objects[job.artifact_s3_key] == content
    stored = context.repository.get(job_id)
    assert stored.status == "completed"
    assert stored.artifact_s3_key == job.artifact_s3_key
    assert stored.submission_id == job.submission_id
    assert context.storage.require_file(job.submitted_path).read_bytes() == submitted
    assert len(apple.calls) == before
    operations = [name for name, _ in tool.calls]
    assert operations.count("sign") == operations.count("submit") == 1
    assert operations.count("staple") == (1 if cache_state == "valid" else 2)
    assert download_url(client.get(path))
    assert artifacts.calls == [job.artifact_s3_key] * 2
    assert artifacts.lookups == [job.artifact_s3_key] * 3


@pytest.mark.parametrize("download_path", DOWNLOAD_PATHS)
@pytest.mark.parametrize("failure", ["upload", "missing-source", "staple"])
def test_artifact_restore_failure_can_retry_download_without_changing_completed_status(
    client, context, settings, apple, tool, artifacts, download_path, failure, monkeypatch
):
    job_id = accepted_job(client, apple)
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == 200
    job = context.repository.get(job_id)
    if download_path.startswith("/api/"):
        login_as(client, context, settings, "admin")
    path = download_path.format(job_id=job_id)
    artifacts.objects.pop(job.artifact_s3_key)
    submitted_path = context.storage.require_file(job.submitted_path)
    submitted = submitted_path.read_bytes()
    if failure != "upload":
        context.storage.require_file(job.artifact_path).unlink()
    if failure == "missing-source":
        submitted_path.unlink()
    tool.fail_staple = failure == "staple"

    def fail_upload():
        raise OperationError("S3 upload unavailable")

    artifacts.before_upload = fail_upload if failure == "upload" else None
    before = len(apple.calls)
    apple.error = "Apple must not be queried"
    with monkeypatch.context() as patch:
        patch.setattr(context.cdn, "sign", lambda *_: pytest.fail("Missing artifact signed"))
        response = client.get(path)
    assert response.status_code == 502
    assert response.json()["error_message"]
    assert response.json()["data"]["status"] == "completed"
    assert "location" not in response.headers
    assert context.repository.get(job_id).status == "completed"
    assert job.artifact_s3_key not in artifacts.objects
    artifacts.before_upload = None
    tool.fail_staple = False
    if failure == "missing-source":
        submitted_path.write_bytes(submitted)
    assert download_url(client.get(path))
    assert read_dmg(artifacts.objects[job.artifact_s3_key]) == b"original|signed|stapled"
    assert len(apple.calls) == before
    operations = [name for name, _ in tool.calls]
    assert operations.count("sign") == operations.count("submit") == 1


@pytest.mark.parametrize("download_path", DOWNLOAD_PATHS)
def test_artifact_lookup_failure_is_retryable_without_marking_file_expired(
    client, context, settings, apple, artifacts, download_path, monkeypatch
):
    job_id = accepted_job(client, apple)
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == 200
    if download_path.startswith("/api/"):
        login_as(client, context, settings, "admin")
    path = download_path.format(job_id=job_id)
    artifacts.error = "S3 artifact lookup failed (ClientError)."
    with monkeypatch.context() as patch:
        patch.setattr(context.cdn, "sign", lambda *_: pytest.fail("Unchecked artifact signed"))
        response = client.get(path)
    assert response.status_code == 502
    assert response.json()["error_message"] == artifacts.error
    assert response.json()["data"]["status"] == "completed"
    assert "location" not in response.headers
    assert context.repository.get(job_id).status == "completed"
    artifacts.error = None
    assert download_url(client.get(path))


def test_completed_job_without_s3_reference_returns_an_error(client, context, apple):
    job_id = accepted_job(client, apple)
    job = context.repository.get(job_id)
    job.status = "completed"
    context.repository.save(job)
    response = client.get(f"/openapi/v1/jobs/{job_id}/artifact")
    assert response.status_code == 500
    assert response.json()["error_message"]
