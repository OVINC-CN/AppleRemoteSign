"""Opt-in SDK/Lua checks against existing Redis; never starts a Redis server."""

import os
import threading
from contextlib import closing
from uuid import uuid4

import pytest
from pydantic import SecretStr

from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.integrations.redis import build_redis_client
from apple_remote_sign.locks import JobLocks

pytestmark = pytest.mark.redis


@pytest.fixture
def redis_clients(settings):
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to an existing disposable Redis database.")
    settings.redis_url = SecretStr(url)
    with (
        closing(build_redis_client(settings)) as first,
        closing(build_redis_client(settings)) as second,
    ):
        first.ping()
        yield first, second


def test_real_sdk_lease_survives_its_initial_ttl_and_excludes_another_client(redis_clients):
    first, second = redis_clients
    identifier = "test-" + uuid4().hex
    holder = JobLocks(first, ttl_seconds=1, renew_interval_seconds=0.05)
    contender = JobLocks(second)
    with holder.hold(identifier):
        threading.Event().wait(1.2)
        with contender.hold(identifier, blocking=False) as acquired:
            assert not acquired
    with contender.hold(identifier, blocking=False) as acquired:
        assert acquired


def test_real_sdk_release_does_not_delete_a_replacement_token(redis_clients):
    first, second = redis_clients
    identifier = "test-" + uuid4().hex
    key = "apple-remote-sign:lock:" + identifier
    replacement = uuid4().hex.encode()
    try:
        with pytest.raises(CoordinationError), JobLocks(first).hold(identifier):
            second.set(key, replacement, px=30_000)
        assert second.get(key) == replacement
    finally:
        # Each test uses its own UUID key; never flush or modify unrelated keys.
        second.delete(key)
