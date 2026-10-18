import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from redis import Redis
from redis.exceptions import RedisError
from redis.lock import Lock

from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.observability import operation
from apple_remote_sign.tracing import record_error, start_span

logger = logging.getLogger(__name__)


def ensure_ownership(*, verify: bool = True) -> None:
    """Check every lease in this request, including nested creation/content locks."""
    for lease in _active.get():
        lease.check(verify=verify)


class Lease:
    def __init__(
        self, lock: Lock, resource: str, ttl: float, interval: float, acquired_at: float
    ) -> None:
        self.lock = lock
        self.resource = resource
        self.ttl = ttl
        self.interval = interval
        self.deadline = acquired_at + ttl
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._renew, name="redis-lease", daemon=True)

    def check(self, *, verify: bool = True) -> None:
        if self.lost.is_set() or time.monotonic() >= self.deadline:
            self.lost.set()
            raise CoordinationError()
        if verify:
            try:
                if not self.lock.owned():
                    self.lost.set()
            except RedisError:
                self.lost.set()
            if self.lost.is_set():
                raise CoordinationError()

    def _renew(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.check(verify=False)
                started = time.monotonic()
                self.lock.extend(self.ttl, replace_ttl=True)
                # Bound validity from before the network operation, never from its response.
                self.deadline = started + self.ttl
                self.check(verify=False)
            except RedisError, CoordinationError:
                self.lost.set()
                logger.warning("lock.lease_lost\nresource: %s", self.resource)
                return

    def close(self) -> None:
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join()
        try:
            # redis-py's Lua script compares the token before deleting the key.
            self.lock.release()
        except RedisError:
            raise CoordinationError() from None


_active: ContextVar[tuple[Lease, ...]] = ContextVar("active_job_leases", default=())


class JobLocks:
    def __init__(
        self, client: Redis, *, ttl_seconds: float = 60, renew_interval_seconds: float = 20
    ) -> None:
        if not 0 < renew_interval_seconds < ttl_seconds:
            raise ValueError("Renewal interval must be positive and shorter than the lease.")
        self.client = client
        self.ttl = ttl_seconds
        self.interval = renew_interval_seconds

    def ping(self) -> None:
        try:
            if not self.client.ping():
                raise CoordinationError()
        except RedisError:
            raise CoordinationError() from None

    @staticmethod
    def _acquire(lock: Lock, *, blocking: bool) -> float | None:
        while True:
            ensure_ownership()
            started = time.monotonic()
            try:
                if lock.acquire(blocking=False):
                    return started
            except RedisError:
                raise CoordinationError() from None
            if not blocking:
                return None
            time.sleep(0.1)

    @contextmanager
    def hold(self, job_id: str, *, blocking: bool = True) -> Iterator[bool]:
        # Every acquisition gets its own Lock/token. The renewal thread needs access
        # to that token, so thread_local is disabled only on this private Lock object.
        lock = self.client.lock(
            f"apple-remote-sign:lock:{job_id}", timeout=self.ttl, thread_local=False
        )
        with operation("lock.acquire", resource=job_id, blocking=blocking) as span:
            acquired_at = self._acquire(lock, blocking=blocking)
            span.set_attribute("lock.acquired", acquired_at is not None)
        if acquired_at is None:
            yield False
            return
        lease = Lease(lock, job_id, self.ttl, self.interval, acquired_at)
        _active.set((*_active.get(), lease))
        # Lease scopes can close out of acquisition order. Record their duration
        # without attaching spans that would restore a stale tracing context.
        span = start_span("lock.hold")
        span.set_attribute("resource", job_id)
        span.set_attribute("acquired", True)
        failed = False
        try:
            lease.thread.start()
            lease.check()
            yield True
            lease.check()
        except BaseException as exc:
            failed = True
            record_error(span, exc)
            raise
        finally:
            # Content locks are released before the nested task lease, so removal
            # must work in acquisition order as well as normal stack order.
            _active.set(tuple(item for item in _active.get() if item is not lease))
            try:
                lease.close()
            except CoordinationError as exc:
                if not failed:
                    record_error(span, exc)
                    raise
            finally:
                span.end()
