from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from threading import Event

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from apple_remote_sign.app import create_app
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.persistence.repository import JobRepository
from apple_remote_sign.recovery import RecoveryWorker
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip
from tests.fake_redis import FakeRedis
from tests.fakes import TEST_APPLICATION_ID


def test_startup_recovers_only_unsubmitted_tasks_without_ddl(context):
    pending = context.repository.create(TEST_APPLICATION_ID, "pending.zip")
    active = context.repository.create(TEST_APPLICATION_ID, "active.zip")
    active.status = "processing"
    context.repository.save(active)
    submitted = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "submitted-1.2.3.zip", BytesIO(dmg_zip(b"original"))
    )
    statements = []
    engine = context.repository.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", lambda _c, _r, sql, *_a: statements.append(sql))
    context.repository.check_schema()
    RecoveryWorker(context).scan()
    assert context.repository.get(pending.id).status == "failed"
    assert context.repository.get(active.id).status == "failed"
    assert context.repository.get(submitted.id).status == "processing"
    assert not any(
        sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for sql in statements
    )


def test_missing_schema_is_an_explicit_error(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    repository = JobRepository(sessionmaker(engine, expire_on_commit=False))
    with pytest.raises(RuntimeError, match=r"0001_init\.sql"):
        repository.check_schema()
    engine.dispose()


def test_concurrent_queries_only_staple_and_upload_once(context, apple, tool, artifacts):
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip(b"original"))
    )
    apple.current = "Accepted"
    tool.staple_delay = 0.05
    second = replace(context, locks=JobLocks(FakeRedis(context.locks.client.state)))
    services = [QueryService(instance, TEST_APPLICATION_ID) for instance in (context, second)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: services[index % 2].query(job.id), range(4)))
    assert all(result.status == "completed" for result in results)
    assert len(apple.calls) == 4
    assert [name for name, _ in tool.calls].count("staple") == 1
    assert len(artifacts.calls) == 1


def test_separate_instances_share_task_locks(redis):
    first = JobLocks(redis)
    second = JobLocks(FakeRedis(redis.state))
    with first.hold("task"), second.hold("task", blocking=False) as acquired:
        assert not acquired
    with second.hold("task", blocking=False) as acquired:
        assert acquired


def test_restarting_app_keeps_submitted_job_and_artifacts(settings, context, apple):
    from fastapi.testclient import TestClient

    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip(b"original"))
    )
    apple.current = "Accepted"
    QueryService(context, TEST_APPLICATION_ID).query(job.id)
    with TestClient(create_app(settings, context), follow_redirects=False) as client:
        response = client.get(
            f"/openapi/v1/jobs/{job.id}/artifact", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 302


def test_new_instance_does_not_fail_an_active_upload(settings, context, tool, monkeypatch):
    from fastapi.testclient import TestClient

    entered, release = Event(), Event()
    original_sign = tool.sign

    def sign(path):
        entered.set()
        assert release.wait(timeout=5)
        return original_sign(path)

    monkeypatch.setattr(tool, "sign", sign)
    second = replace(context, locks=JobLocks(FakeRedis(context.locks.client.state)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            SubmissionService(context, TEST_APPLICATION_ID).submit,
            "app-1.2.3.zip",
            BytesIO(dmg_zip()),
        )
        try:
            assert entered.wait(timeout=2)
            identifier = context.repository.interrupted_ids()[0]
            with TestClient(create_app(settings, second)) as client:
                assert client.get("/ready").status_code == 200
                assert context.repository.get(identifier).status == "processing"
        finally:
            release.set()
        assert pending.result(timeout=3).status == "processing"


def test_creation_is_already_locked_when_visible_to_recovery(context, monkeypatch):
    original_create = context.repository.create
    second = replace(context, locks=JobLocks(FakeRedis(context.locks.client.state)))

    def create(*args, **kwargs):
        job = original_create(*args, **kwargs)
        RecoveryWorker(second).scan()
        assert context.repository.get(job.id).status == "preparing"
        return job

    monkeypatch.setattr(context.repository, "create", create)
    result = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip())
    )
    assert result.status == "processing"


def test_later_scan_recovers_a_crashed_instances_expired_lease(context, redis):
    job = context.repository.create(TEST_APPLICATION_ID, "crashed-1.2.3.zip")
    lease = redis.lock(f"apple-remote-sign:lock:{job.id}", timeout=60, thread_local=False)
    assert lease.acquire(blocking=False)
    worker = RecoveryWorker(context)
    worker.scan()
    assert context.repository.get(job.id).status == "preparing"
    redis.state.expire(job.id)
    worker.scan()
    assert context.repository.get(job.id).status == "failed"


def test_recovery_rechecks_submission_id_after_candidate_scan(context, monkeypatch):
    job = context.repository.create(TEST_APPLICATION_ID, "app-1.2.3.zip")

    def candidates():
        job.submission_id = "saved-before-recovery"
        context.repository.save(job)
        return [job.id]

    monkeypatch.setattr(context.repository, "interrupted_ids", candidates)
    RecoveryWorker(context).scan()
    assert context.repository.get(job.id).status == "preparing"


def test_periodic_recovery_continues_after_startup(context, monkeypatch):
    recovered = Event()
    original_recover = context.repository.recover_interrupted

    def recover(identifier):
        result = original_recover(identifier)
        recovered.set()
        return result

    monkeypatch.setattr(context.repository, "recover_interrupted", recover)
    worker = RecoveryWorker(context, interval_seconds=0.01)
    worker.start()
    try:
        job = context.repository.create(TEST_APPLICATION_ID, "abandoned-1.2.3.zip")
        assert recovered.wait(timeout=2)
    finally:
        worker.close()
    assert context.repository.get(job.id).status == "failed"
