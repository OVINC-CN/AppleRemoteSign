import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.archives import SigningArchive
from apple_remote_sign.domain import ApiError, CoordinationError, JobData, JobStatus, OperationError
from apple_remote_sign.integrations.apple import NotaryClient
from apple_remote_sign.integrations.cdn import CdnSigner
from apple_remote_sign.integrations.process import CommandError
from apple_remote_sign.integrations.rcodesign import SigningTool
from apple_remote_sign.integrations.s3 import ArtifactStore
from apple_remote_sign.integrations.wecom import NotificationEvent, WeComNotifier
from apple_remote_sign.locks import JobLocks, ensure_ownership
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.persistence.repository import JobRepository
from apple_remote_sign.redaction import Redactor
from apple_remote_sign.services.history import HistoryService
from apple_remote_sign.storage import FileStorage

logger = logging.getLogger(__name__)


@dataclass
class ServiceContext:
    repository: JobRepository
    storage: FileStorage
    tool: SigningTool
    apple: NotaryClient
    locks: JobLocks
    redactor: Redactor
    artifacts: ArtifactStore
    cdn: CdnSigner
    archives: SigningArchive = field(default_factory=SigningArchive)
    notifier: WeComNotifier | None = None

    def notify(self, job: Job, event: NotificationEvent) -> None:
        if self.notifier is not None:
            try:
                view = HistoryService(AccessRepository(self.repository.sessions)).view(job)
            except (ApiError, SQLAlchemyError, ValueError) as exc:
                logger.warning(
                    "wecom.notification_failed\njob_id: %s\nevent: %s\nreason: %s",
                    job.id,
                    event,
                    type(exc).__name__,
                )
                return
            ensure_ownership()
            self.notifier.notify(
                job, event, application_name=view.application.name, submitter=view.source
            )

    @contextmanager
    def hold_job(self, job: Job, *, blocking: bool = True) -> Iterator[bool]:
        try:
            with self.locks.hold(job.id, blocking=blocking) as acquired:
                yield acquired
        except CoordinationError as exc:
            exc.data = JobData.model_validate(job)
            raise

    def require_job(self, job_id: str, application_id: str) -> Job:
        job = self.repository.get(job_id)
        if job is None or job.application_id != application_id:
            raise ApiError(404, "Job not found.")
        return job

    def record(self, job: Job, label: str, output: str) -> None:
        text = self.redactor.clean(output)
        job.diagnostics = (job.diagnostics + f"\n[{label}]\n{text}")[-65536:]

    def fail(self, job: Job, label: str, error: OperationError | OSError) -> None:
        ensure_ownership()
        message = str(error) if isinstance(error, OperationError) else str(error.strerror)
        if isinstance(error, CommandError):
            self.record(job, label, error.output)
            message += " " + error.output[-2000:].strip()
        job.status = JobStatus.FAILED.value
        job.error_message = self.redactor.clean(f"{label}: {message}").strip()
        self.repository.save(job)
        logger.error(
            "job.failed\njob_id: %s\napplication_id: %s\nsubmission_id: %s\nstage: %s\nreason: %s",
            job.id,
            job.application_id,
            job.submission_id or "-",
            label,
            job.error_message,
        )

    @staticmethod
    def result(job: Job) -> JobData:
        data = JobData.model_validate(job)
        if job.status == JobStatus.FAILED.value:
            raise ApiError(500, job.error_message or "Job failed.", data)
        return data
