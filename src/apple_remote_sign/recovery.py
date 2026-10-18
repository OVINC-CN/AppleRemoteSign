import logging
import threading
from contextvars import copy_context

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.observability import operation
from apple_remote_sign.services.context import ServiceContext

logger = logging.getLogger(__name__)


class RecoveryWorker:
    """Only reconcile abandoned database records; never execute or resubmit jobs."""

    def __init__(self, services: ServiceContext, *, interval_seconds: float = 30) -> None:
        self.services = services
        self.interval = interval_seconds
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="job-recovery", daemon=True, context=copy_context()
        )

    def scan(self) -> None:
        recovered = 0
        for job_id in self.services.repository.interrupted_ids():
            if self.stop.is_set():
                break
            with self.services.locks.hold(job_id, blocking=False) as acquired:
                if acquired and self.services.repository.recover_interrupted(job_id):
                    recovered += 1
                    self._notify_failure(job_id)
        if recovered:
            logger.info("job.interrupted_recovered\ncount: %d", recovered)

    def _notify_failure(self, job_id: str) -> None:
        if self.services.notifier is None:
            return
        try:
            job = self.services.repository.get(job_id)
        except SQLAlchemyError as exc:
            logger.warning(
                "wecom.notification_failed\njob_id: %s\nevent: failed\nreason: %s",
                job_id,
                type(exc).__name__,
            )
            return
        if job is not None:
            self.services.notify(job, "failed")

    def start(self) -> None:
        with operation("startup.recover_interrupted"):
            self.scan()
        self.thread.start()

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                with operation("jobs.recover_interrupted"):
                    self.scan()
            except (CoordinationError, RedisError, SQLAlchemyError) as exc:
                logger.warning("job.recovery_unavailable\ntype: %s", type(exc).__name__)

    def close(self) -> None:
        self.stop.set()
        self.thread.join()
