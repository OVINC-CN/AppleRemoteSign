import json
import logging

from opentelemetry.trace import SpanKind
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import ApiError, JobData, JobStatus, OperationError
from apple_remote_sign.locks import ensure_ownership
from apple_remote_sign.observability import operation
from apple_remote_sign.persistence.models import Job, utcnow
from apple_remote_sign.services.context import ServiceContext
from apple_remote_sign.services.notary import NotaryDetailsService
from apple_remote_sign.storage import StoredFile, job_directory

logger = logging.getLogger(__name__)


class QueryService:
    def __init__(self, context: ServiceContext, application_id: str) -> None:
        self.context = context
        self.application_id = application_id

    def query(self, job_id: str, *, wait: bool = True) -> JobData:
        ctx = self.context
        snapshot = ctx.require_job(job_id, self.application_id)
        if snapshot.submission_id is None:
            return ctx.result(snapshot)
        with ctx.hold_job(snapshot, blocking=wait) as acquired:
            if not acquired:
                logger.info("job.refresh.busy\njob_id: %s", job_id)
                raise ApiError(
                    409,
                    "This job is already being processed or refreshed. Please try again shortly.",
                    JobData.model_validate(snapshot),
                )
            job = ctx.require_job(job_id, self.application_id)
            try:
                if job.submission_id:
                    self._refresh(job, job.submission_id)
            except SQLAlchemyError:
                logger.exception("job.refresh.database_failed\njob_id: %s", job_id)
                raise ApiError(
                    500,
                    "Database update failed; query this task again.",
                    JobData.model_validate(job),
                ) from None
            return ctx.result(job)

    def _refresh(self, job: Job, identifier: str) -> None:
        ctx = self.context
        previous = job.status
        was_completed = previous == JobStatus.COMPLETED.value and bool(job.artifact_s3_key)
        try:
            ensure_ownership()
            with operation("apple.status", job_id=job.id, submission_id=identifier) as span:
                status = ctx.apple.status(identifier)
                span.set_attribute("apple.notarization.status", status)
        except OperationError as exc:
            message = ctx.redactor.clean(str(exc))
            ctx.record(job, "query", message)
            ctx.repository.save(job)
            raise ApiError(502, message, JobData.model_validate(job)) from None
        ensure_ownership()
        job.last_checked_at = utcnow()
        if status == "Accepted":
            self._staple(job)
        elif status in ("Invalid", "Rejected"):
            self._reject(job, identifier, status)
        elif status == "In Progress":
            # Apple allocates an ID before upload. A failed upload can stay "In Progress"
            # indefinitely; it does not undo a local failure or a completed artifact.
            if job.status not in {JobStatus.FAILED.value, JobStatus.COMPLETED.value}:
                job.status = JobStatus.PROCESSING.value
                job.error_message = ""
        else:
            raise ApiError(502, "Unrecognized Apple status.", JobData.model_validate(job))
        ctx.repository.save(job)
        if previous != job.status:
            logger.info(
                "job.status_changed\njob_id: %s\nprevious_status: %s\nstatus: %s\napple_status: %s",
                job.id,
                previous,
                job.status,
                status,
            )
        if status == "Accepted" and job.status == JobStatus.COMPLETED.value:
            NotaryDetailsService(ctx, job.application_id).prefetch_accepted(job)
        if not was_completed and job.status == JobStatus.COMPLETED.value and job.artifact_s3_key:
            ctx.notify(job, "completed")
        elif previous != JobStatus.FAILED.value and job.status == JobStatus.FAILED.value:
            ctx.notify(job, "failed")

    def _staple(self, job: Job) -> None:
        ctx = self.context
        if job.status == JobStatus.COMPLETED.value and job.artifact_s3_key:
            return
        job.status = JobStatus.PROCESSING.value
        ctx.repository.save(job)
        stage = "staple"
        try:
            artifact = self._artifact_for_upload(job)
            stage = "upload"
            key = job.artifact_s3_key or ctx.artifacts.key_for(
                job_directory(job.application_id, job.id, job.created_at), job.filename
            )
            self._upload_artifact(job, artifact, key)
            job.artifact_s3_key = key
            job.status = JobStatus.COMPLETED.value
            job.error_message = ""
        except (OperationError, OSError) as exc:
            ctx.fail(job, stage, exc)

    def _artifact_for_upload(self, job: Job) -> StoredFile:
        artifact = self._cached_artifact(job)
        if artifact is None:
            artifact = self._local_artifact(job)
            job.artifact_path = artifact.path
            job.artifact_sha256 = artifact.sha256
            self.context.repository.save(job)
        return artifact

    def _upload_artifact(self, job: Job, artifact: StoredFile, key: str) -> None:
        ctx = self.context
        with operation("s3.upload", kind=SpanKind.CLIENT, job_id=job.id):
            ensure_ownership()
            ctx.artifacts.upload(
                ctx.storage.require_file(artifact.path), key, job.filename, artifact.sha256
            )
        ensure_ownership()

    def _cached_artifact(self, job: Job) -> StoredFile | None:
        if not job.artifact_path or not job.artifact_sha256:
            return None
        try:
            artifact = self.context.storage.inspect(job.artifact_path)
        except OperationError, OSError:
            return None
        return artifact if artifact.sha256 == job.artifact_sha256 else None

    def _local_artifact(self, job: Job) -> StoredFile:
        ctx = self.context
        if job.submitted_path is None:
            raise OperationError("Signed submission file is missing.")
        ensure_ownership()
        directory = job_directory(job.application_id, job.id, job.created_at)
        candidate = ctx.storage.copy(job.submitted_path, directory, "working", job.filename)
        with operation("job.staple", job_id=job.id):
            output = ctx.archives.transform(ctx.storage.require_file(candidate), ctx.tool.staple)
        ctx.record(job, "staple", output)
        with operation("artifact.publish", job_id=job.id):
            ensure_ownership()
            return ctx.storage.publish(candidate, directory, job.filename)

    def _reject(self, job: Job, identifier: str, status: str) -> None:
        ctx = self.context
        message = f"Apple notarization {status}."
        try:
            if job.notary_log is None:
                ensure_ownership()
                with operation("apple.notary_log", job_id=job.id, submission_id=identifier):
                    job.notary_log = ctx.redactor.clean(
                        json.dumps(ctx.apple.log(identifier), ensure_ascii=False)
                    )
            details = json.loads(job.notary_log)
            message += " " + self._rejection_details(details)
        except (OperationError, ValueError) as exc:
            ctx.record(job, "notary-log", str(exc))
        job.status = JobStatus.FAILED.value
        job.error_message = ctx.redactor.clean(message).strip()
        logger.warning(
            "job.notarization_rejected\njob_id: %s\nstatus: %s\nreason: %s",
            job.id,
            status,
            job.error_message,
        )

    @staticmethod
    def _rejection_details(details: object) -> str:
        if not isinstance(details, dict):
            return ""
        parts = [str(details.get("statusSummary", ""))]
        issues = details.get("issues", [])
        if isinstance(issues, list):
            parts.extend(
                str(issue.get("message", "")) for issue in issues[:3] if isinstance(issue, dict)
            )
        return " ".join(parts)[:2000]

    def artifact(self, job_id: str) -> str:
        ctx = self.context
        snapshot = ctx.require_job(job_id, self.application_id)
        with ctx.hold_job(snapshot):
            job = ctx.require_job(job_id, self.application_id)
            data = ctx.result(job)
            if job.status != JobStatus.COMPLETED.value:
                raise ApiError(409, "Artifact is not ready; query the job first.", data)
            if not job.artifact_s3_key:
                raise ApiError(
                    500, "Artifact is unavailable; query this job to retry delivery.", data
                )
            try:
                with operation("s3.head", kind=SpanKind.CLIENT, job_id=job.id):
                    ensure_ownership()
                    exists = ctx.artifacts.exists(job.artifact_s3_key)
                ensure_ownership()
                if not exists:
                    artifact = self._artifact_for_upload(job)
                    self._upload_artifact(job, artifact, job.artifact_s3_key)
            except (OperationError, OSError) as exc:
                message = str(exc) if isinstance(exc, OperationError) else str(exc.strerror)
                raise ApiError(502, ctx.redactor.clean(message), data) from None
            logger.info(
                "artifact.download\njob_id: %s\napplication_id: %s",
                job.id,
                job.application_id,
            )
            return ctx.cdn.sign(job.artifact_s3_key)
