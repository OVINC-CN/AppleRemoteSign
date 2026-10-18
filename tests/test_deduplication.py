import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from urllib.parse import unquote, urlsplit

import pytest
from sqlalchemy import func, select

from apple_remote_sign.domain import ApiError
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip, read_dmg
from tests.fake_redis import FakeRedis
from tests.fakes import TEST_APPLICATION_ID


@pytest.mark.parametrize("state", ["preparing", "processing", "completed"])
def test_upload_reuses_every_nonfailed_status(client, context, tool, apple, state):
    digest = hashlib.sha256(dmg_zip(b"same content")).hexdigest()
    existing = context.repository.create(TEST_APPLICATION_ID, "first-1.2.3.zip", digest)
    existing.status = state
    context.repository.save(existing)
    response = client.post(
        "/openapi/v1/jobs", files={"file": ("first-1.2.3.zip", dmg_zip(b"same content"))}
    )
    assert response.status_code == 200
    assert response.json()["data"]["id"] == existing.id
    assert response.json()["data"]["status"] == state
    assert tool.calls == []
    assert apple.calls == []
    assert not context.storage.root.exists()


def test_failed_history_creates_new_task_and_keeps_previous(context, tool):
    service = SubmissionService(context, TEST_APPLICATION_ID)
    tool.fail_sign = True
    with pytest.raises(ApiError) as raised:
        service.submit("same-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
    old_id = raised.value.data.id
    tool.fail_sign = False
    new = service.submit("same-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
    assert new.id != old_id
    assert context.repository.get(old_id).status == "failed"
    assert new.status == "processing"
    assert (
        context.repository.get(old_id).original_sha256
        == context.repository.get(new.id).original_sha256
    )
    assert [operation for operation, _ in tool.calls].count("sign") == 2


def test_completed_content_reused_without_new_apple_call(context, tool, apple):
    service = SubmissionService(context, TEST_APPLICATION_ID)
    first = service.submit("first-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
    apple.current = "Accepted"
    QueryService(context, TEST_APPLICATION_ID).query(first.id)
    duplicate = service.submit("first-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
    assert duplicate.id == first.id
    assert duplicate.status == "completed"
    assert context.repository.get(first.id).filename == "first-1.2.3.zip"
    assert len(apple.calls) == 1
    assert [operation for operation, _ in tool.calls] == ["sign", "submit", "staple"]


@pytest.mark.parametrize(
    "filename", ["renamed-1.2.3.zip", "First-1.2.3.zip", "first-1.2.3 .zip", "fírst-1.2.3.zip"]
)
def test_same_content_with_different_name_keeps_both_filenames(context, apple, artifacts, filename):
    service = SubmissionService(context, TEST_APPLICATION_ID)
    first = service.submit("first-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
    apple.current = "Accepted"
    query = QueryService(context, TEST_APPLICATION_ID)
    query.query(first.id)
    second = service.submit(filename, BytesIO(dmg_zip(b"same content")))
    assert second.id != first.id
    query.query(second.id)
    for job_id, original_name in ((first.id, "first-1.2.3.zip"), (second.id, filename)):
        job = context.repository.get(job_id)
        assert job.filename == original_name
        artifact = query.artifact(job_id)
        assert unquote(urlsplit(artifact).path).endswith("/" + original_name)
        assert read_dmg(artifacts.objects[job.artifact_s3_key]) == b"same content|signed|stapled"
        assert service.submit(original_name, BytesIO(dmg_zip(b"same content"))).id == job_id


def test_different_content_with_same_name_is_new_task(context):
    service = SubmissionService(context, TEST_APPLICATION_ID)
    a = service.submit("same-1.2.3.zip", BytesIO(dmg_zip(b"first")))
    b = service.submit("same-1.2.3.zip", BytesIO(dmg_zip(b"second")))
    assert a.id != b.id


def test_failed_latest_record_does_not_hide_older_reusable_task(context):
    digest = hashlib.sha256(dmg_zip(b"same content")).hexdigest()
    old = context.repository.create(TEST_APPLICATION_ID, "same-1.2.3.zip", digest)
    old.status = "completed"
    context.repository.save(old)
    latest = context.repository.create(TEST_APPLICATION_ID, "same-1.2.3.zip", digest)
    latest.status = "failed"
    context.repository.save(latest)
    response = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "same-1.2.3.zip", BytesIO(dmg_zip(b"same content"))
    )
    assert response.id == old.id


def test_concurrent_uploads_return_running_task_without_waiting(context, tool, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original_sign = tool.sign

    def blocking_sign(path):
        started.set()
        assert release.wait(timeout=5)
        return original_sign(path)

    monkeypatch.setattr(tool, "sign", blocking_sign)
    service = SubmissionService(context, TEST_APPLICATION_ID)
    other = SubmissionService(
        replace(context, locks=JobLocks(FakeRedis(context.locks.client.state))), TEST_APPLICATION_ID
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(service.submit, "first-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
        try:
            assert started.wait(timeout=2)
            duplicates = [
                pool.submit(other.submit, "first-1.2.3.zip", BytesIO(dmg_zip(b"same content")))
                for _ in range(3)
            ]
            results = [future.result(timeout=2) for future in duplicates]
            assert all(result.status == "processing" for result in results)
            # A duplicate's status can be read without waiting for signing to finish.
            assert (
                QueryService(context, TEST_APPLICATION_ID).query(results[0].id).status
                == "processing"
            )
            assert tool.calls == []
        finally:
            release.set()
        primary = first.result(timeout=2)
    assert all(result.id == primary.id for result in results)
    assert [operation for operation, _ in tool.calls] == ["sign", "submit"]
    with context.repository.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
