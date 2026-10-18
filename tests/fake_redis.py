"""In-memory Redis lock semantics for tests; never opens a socket."""

import threading
import time
from uuid import uuid4

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockNotOwnedError


class RedisState:
    def __init__(self):
        self.guard = threading.Condition()
        self.entries = {}
        self.available = True
        self.renewals = 0
        self.renewed = threading.Event()
        self.released = []

    def check(self):
        if not self.available:
            raise RedisConnectionError("redis://:never-log-this@redis.invalid/3 unavailable")

    def token(self, name):
        entry = self.entries.get(name)
        return entry[0] if entry and entry[1] > time.monotonic() else None

    def expire(self, resource):
        with self.guard:
            self.entries.pop(f"apple-remote-sign:lock:{resource}", None)
            self.guard.notify_all()


class FakeRedis(Redis):
    def __init__(self, state=None):
        self.state = state or RedisState()
        self.closed = False

    def __repr__(self):
        return "FakeRedis()"

    def ping(self):
        self.state.check()
        return True

    def close(self):
        self.closed = True

    def lock(self, name, *, timeout, thread_local):
        assert thread_local is False
        return FakeLock(self.state, name, timeout)


class FakeLock:
    def __init__(self, state, name, timeout):
        self.state = state
        self.name = name
        self.timeout = timeout
        self.token = uuid4().hex

    def acquire(self, *, blocking):
        with self.state.guard:
            while True:
                self.state.check()
                if self.state.token(self.name) is None:
                    self.state.entries[self.name] = (self.token, time.monotonic() + self.timeout)
                    return True
                if not blocking:
                    return False
                self.state.guard.wait(0.01)

    def owned(self):
        with self.state.guard:
            self.state.check()
            return self.state.token(self.name) == self.token

    def extend(self, additional_time, *, replace_ttl):
        with self.state.guard:
            self.state.check()
            if self.state.token(self.name) != self.token:
                raise LockNotOwnedError("Cannot extend another owner's lease")
            base = time.monotonic() if replace_ttl else self.state.entries[self.name][1]
            self.state.entries[self.name] = (self.token, base + additional_time)
            self.state.renewals += 1
            self.state.renewed.set()
            return True

    def release(self):
        with self.state.guard:
            self.state.check()
            if self.state.token(self.name) != self.token:
                raise LockNotOwnedError("Cannot release another owner's lease")
            self.state.entries.pop(self.name)
            self.state.released.append(self.name)
            self.state.guard.notify_all()
