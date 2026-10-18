import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import Context

import pytest
from pydantic import SecretStr, ValidationError

from apple_remote_sign.config import Settings
from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.integrations.redis import build_redis_client
from apple_remote_sign.locks import JobLocks, ensure_ownership
from apple_remote_sign.redaction import Redactor
from tests.fake_redis import FakeRedis


@pytest.mark.parametrize(
    ("url", "database"),
    [
        ("redis://localhost:6379", 0),
        ("redis://localhost:6379/3", 3),
        ("redis://localhost:6379/3?db=7", 7),
        ("rediss://signer:p%40ss%3Aword@redis.example:6380/5", 5),
    ],
)
def test_native_sdk_url_parses_database_and_tls_without_connecting(settings, url, database):
    settings.redis_url = SecretStr(url)
    with closing(build_redis_client(settings)) as client:
        options = client.connection_pool.connection_kwargs
        assert options.get("db", 0) == database
        assert options["socket_connect_timeout"] == options["socket_timeout"] == 3
        assert options["retry"].get_retries() == 0
        if url.startswith("rediss:"):
            assert client.connection_pool.connection_class.__name__ == "SSLConnection"
            assert options["username"] == "signer"
            assert options["password"] == "p@ss:word"


def test_redis_configuration_is_required(settings, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValidationError, match="redis_url"):
        Settings(_env_file=None, **settings.model_dump(exclude={"redis_url"}))


@pytest.mark.parametrize("url", ["http://redis/3", "redis:///3", "rediss://host:bad/3"])
def test_invalid_redis_url_is_rejected_without_exposing_credentials(settings, url):
    with pytest.raises(ValidationError, match="REDIS_URL"):
        Settings(_env_file=None, **(settings.model_dump() | {"redis_url": url}))


@pytest.mark.parametrize("path", ["/3?db=not-a-db", "/-1", "/3?socket_timeout=invalid"])
def test_invalid_sdk_options_fail_without_exposing_url(settings, path):
    settings.redis_url = SecretStr("redis://:do-not-print-this@localhost" + path)
    with pytest.raises(RuntimeError, match="REDIS_URL") as raised:
        build_redis_client(settings)
    assert "do-not-print-this" not in str(raised.value)


def test_redis_credentials_are_redacted_with_or_without_a_username(settings):
    settings.redis_url = SecretStr("redis://:p%40ss%3Aword@redis.example/3")
    cleaned = Redactor(settings.redis_secrets).clean(
        settings.redis_url.get_secret_value() + " p@ss:word"
    )
    assert "p%40ss" not in cleaned and "p@ss" not in cleaned
    assert "secret-password" not in Redactor().clean("rediss://:secret-password@host/3")


def test_long_task_renews_and_blocks_another_instance(redis):
    first = JobLocks(redis, ttl_seconds=1, renew_interval_seconds=0.02)
    second = JobLocks(FakeRedis(redis.state))
    with first.hold("long-task"):
        assert redis.state.renewed.wait(timeout=2)
        assert redis.state.renewals > 0
        with second.hold("long-task", blocking=False) as acquired:
            assert not acquired
    assert redis.state.released == ["apple-remote-sign:lock:long-task"]


def test_old_owner_cannot_release_or_renew_another_instances_lease(redis):
    first = JobLocks(redis)
    second = JobLocks(FakeRedis(redis.state))
    entered, release = threading.Event(), threading.Event()

    def new_owner():
        with second.hold("task"):
            entered.set()
            assert release.wait(timeout=3)

    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with pytest.raises(CoordinationError), first.hold("task"):
                redis.state.expire("task")
                pending = pool.submit(Context().run, new_owner)
                assert entered.wait(timeout=2)
                ensure_ownership()
            # Exiting the old scope must leave the new owner's token intact.
            with second.hold("task", blocking=False) as acquired:
                assert not acquired
            assert redis.state.released == []
        finally:
            release.set()
        pending.result(timeout=2)


def test_renewal_failure_aborts_the_holder_and_context_is_cleared(redis):
    locks = JobLocks(redis, ttl_seconds=1, renew_interval_seconds=0.01)
    with pytest.raises(CoordinationError), locks.hold("task"):
        assert redis.state.renewed.wait(timeout=2)
        redis.state.available = False
        # Wait for the renewal thread to observe the outage, without real Redis.
        from apple_remote_sign.locks import _active

        assert _active.get()[-1].lost.wait(timeout=2)
        ensure_ownership(verify=False)
    redis.state.available = True
    ensure_ownership()


def test_expired_lease_does_not_write_a_failed_or_completed_state(context, redis):
    from tests.fakes import TEST_APPLICATION_ID

    job = context.repository.create(TEST_APPLICATION_ID, "app-1.2.3.zip")
    with pytest.raises(CoordinationError), context.locks.hold(job.id):
        job.status = "completed"
        redis.state.expire(job.id)
        context.repository.save(job)
    assert context.repository.get(job.id).status == "preparing"


def test_releasing_content_lock_keeps_nested_task_lease_active(redis):
    from contextlib import ExitStack

    locks = JobLocks(redis)
    with pytest.raises(CoordinationError), ExitStack() as tasks:
        with locks.hold("content"):
            tasks.enter_context(locks.hold("task"))
        redis.state.expire("task")
        ensure_ownership()


def test_monotonic_deadline_detects_expiry_even_before_renewal(redis, monkeypatch):
    from apple_remote_sign import locks as lock_module

    with pytest.raises(CoordinationError), JobLocks(redis).hold("task"):
        lease = lock_module._active.get()[-1]
        monkeypatch.setattr(lock_module.time, "monotonic", lambda: lease.deadline + 1)
        ensure_ownership(verify=False)


def test_lost_lease_does_not_replace_the_shared_archive(context, redis, tmp_path):
    from tests.archive_helpers import dmg_zip

    path = tmp_path / "app-1.2.3.zip"
    original = dmg_zip()
    path.write_bytes(original)

    def sign_and_lose_lease(asset):
        result = context.tool.sign(asset)
        redis.state.expire("task")
        return result

    with pytest.raises(CoordinationError), context.locks.hold("task"):
        context.archives.transform(path, sign_and_lose_lease)
    assert path.read_bytes() == original
