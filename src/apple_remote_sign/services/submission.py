import logging
import re
from contextlib import ExitStack
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import ApiError, CoordinationError, JobData, JobStatus, OperationError
from apple_remote_sign.locks import ensure_ownership
from apple_remote_sign.observability import operation
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.services.context import ServiceContext
from apple_remote_sign.storage import job_directory, safe_filename

logger = logging.getLogger(__name__)


class SubmissionService:
    def __init__(
        self,
        context: ServiceContext,
        application_id: str,
        *,
        user_id: str | None = None,
        key_id: str | None = None,
    ) -> None:
        self.context = context
        self.application_id = application_id
        self.user_id = user_id
        self.key_id = key_id

    def submit(self, filename: str | None, source: BinaryIO) -> JobData:
        ctx = self.context
        try:
            name = safe_filename(filename)
            ctx.archives.validate_filename(name)
            if not re.search(r"\d+\.\d+\.\d+", name):
                raise OperationError(
                    "ZIP filename must contain a version number (X.Y.Z), "
                    "for example product-v1.2.3.zip."
                )
            with operation("upload.validate", application_id=self.application_id):
                sha256 = ctx.storage.fingerprint(source)
                ctx.archives.validate_upload(name, source)
        except OperationError as exc:
            raise ApiError(422, str(exc)) from None
        except OSError as exc:
            raise ApiError(500, ctx.redactor.clean(str(exc))) from None
        return self._submit(name, sha256, source)

    def _submit(self, name: str, sha256: str, source: BinaryIO) -> JobData:
        ctx = self.context
        job: Job | None = None
        try:
            with ExitStack() as leases:
                # Release content coordination after creation so duplicate uploads
                # can return immediately, while retaining the task lease throughout.
                with ctx.locks.hold(f"sha256:{self.application_id}:{sha256}"):
                    job = ctx.repository.find_reusable(self.application_id, sha256, name)
                    if job is not None:
                        logger.info(
                            "job.reused\njob_id: %s\napplication_id: %s\nstatus: %s",
                            job.id,
                            self.application_id,
                            job.status,
                        )
                        return JobData.model_validate(job)
                    identifier = str(uuid4())
                    # Recovery cannot observe a committed record without its lease.
                    leases.enter_context(ctx.locks.hold(identifier))
                    job = ctx.repository.create(
                        self.application_id,
                        name,
                        sha256,
                        user_id=self.user_id,
                        key_id=self.key_id,
                        job_id=identifier,
                    )
                    logger.info(
                        "job.created\njob_id: %s\napplication_id: %s\nsource: %s",
                        job.id,
                        self.application_id,
                        "web" if self.user_id else "openapi",
                    )
                assert job is not None
                ctx.notify(job, "submitted")
                self._process(job, source)
                if job.status == JobStatus.FAILED.value:
                    ctx.notify(job, "failed")
        except CoordinationError as exc:
            if job is not None:
                exc.data = JobData.model_validate(job)
            raise
        except SQLAlchemyError:
            if job is not None:
                logger.exception("job.database_failed\njob_id: %s", job.id)
                raise ApiError(
                    500,
                    "Database update failed. Preserve this task/submission ID; do not resubmit.",
                    JobData.model_validate(job),
                ) from None
            raise
        assert job is not None
        return ctx.result(job)

    def _process(self, job: Job, source: BinaryIO) -> None:
        ctx = self.context
        directory = job_directory(job.application_id, job.id, job.created_at)
        stage = "prepare"
        try:
            ensure_ownership()
            with operation("upload.persist", job_id=job.id):
                original = ctx.storage.save_upload(directory, job.filename, source)
            ensure_ownership()
            if original.sha256 != job.original_sha256:
                raise OperationError("Upload content changed after hashing.")
            job.original_path = original.path
            job.original_sha256 = original.sha256
            job.size_bytes = original.size
            job.status = JobStatus.PROCESSING.value
            ctx.repository.save(job)

            stage = "sign"
            ensure_ownership()
            signed_path = ctx.storage.copy(original.path, directory, "submitted", job.filename)
            with operation("job.sign", job_id=job.id):
                output = ctx.archives.transform(
                    ctx.storage.require_file(signed_path), ctx.tool.sign
                )
            ensure_ownership()
            ctx.record(job, stage, output)
            signed = ctx.storage.inspect(signed_path)
            job.submitted_path = signed.path
            job.submitted_sha256 = signed.sha256
            ctx.repository.save(job)
            logger.info("job.signed\njob_id: %s", job.id)

            stage = "submit"
            ensure_ownership()
            with operation("job.submit", job_id=job.id):
                output = ctx.tool.submit(
                    ctx.storage.resolve(signed.path),
                    lambda identifier: self._save_id(job, identifier),
                )
            ensure_ownership()
            ctx.record(job, stage, output)
            if job.submission_id is None:
                raise OperationError("Tool did not report a submission ID.")
            ctx.repository.save(job)
            logger.info("job.submitted\njob_id: %s\nsubmission_id: %s", job.id, job.submission_id)
        except (OperationError, OSError) as exc:
            ctx.fail(job, stage, exc)

    def _save_id(self, job: Job, identifier: str) -> None:
        job.submission_id = identifier
        self.context.repository.save(job)
        logger.info(
            "job.submission_id_saved\njob_id: %s\nsubmission_id: %s\nupload_complete: false",
            job.id,
            identifier,
        )
