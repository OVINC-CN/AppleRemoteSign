import json
import logging
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import ApiError, OperationError
from apple_remote_sign.locks import ensure_ownership
from apple_remote_sign.notary import NotaryDetails
from apple_remote_sign.observability import operation
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.services.context import ServiceContext

logger = logging.getLogger(__name__)


class NotaryDetailsService:
    def __init__(self, context: ServiceContext, application_id: str) -> None:
        self.context = context
        self.application_id = application_id

    def details(self, job_id: str) -> NotaryDetails | None:
        ctx = self.context
        snapshot = ctx.require_job(job_id, self.application_id)
        if snapshot.notary_log:
            return self._cached(snapshot)
        if not snapshot.submission_id:
            return None
        with ctx.hold_job(snapshot, blocking=False) as acquired:
            if not acquired:
                raise ApiError(409, "Notarization details are busy. Please try again shortly.")
            job = ctx.require_job(job_id, self.application_id)
            if job.notary_log:
                return self._cached(job)
            if not job.submission_id:
                return None
            value = self._fetch(job.submission_id, job.id)
            if value is None:
                return None
            details, serialized = value
            job.notary_log = serialized
            try:
                ctx.repository.save(job)
            except SQLAlchemyError:
                logger.exception("job.notary_details.database_failed\njob_id: %s", job.id)
                raise ApiError(
                    500, "Could not save notarization details. Please try again."
                ) from None
            return details

    def prefetch_accepted(self, job: Job) -> None:
        """Cache details for a known Accepted job while its caller holds the lease."""
        if job.notary_log or not job.submission_id:
            return
        ctx = self.context
        previous = job.notary_log
        try:
            _, serialized = self._fetch_log(job.submission_id, job.id)
            job.notary_log = serialized
            ctx.repository.save(job)
        except (OperationError, ValueError, SQLAlchemyError) as exc:
            job.notary_log = previous
            reason = (
                ctx.redactor.clean(str(exc))
                if isinstance(exc, OperationError)
                else type(exc).__name__
            )
            logger.warning(
                "job.notary_details.prefetch_failed\njob_id: %s\nreason: %s", job.id, reason
            )

    def _cached(self, job: Job) -> NotaryDetails | None:
        try:
            value = json.loads(job.notary_log or "")
            if not isinstance(value, dict):
                raise ValueError("Expected a notarization log object.")
            # Older rejection logs may contain only issues and a status summary.
            if value.get("status") != "Accepted":
                return None
            return self._validate(value, job.submission_id)
        except ValueError:
            raise ApiError(500, "Stored notarization details are invalid.") from None

    def _fetch(self, identifier: str, job_id: str) -> tuple[NotaryDetails, str] | None:
        ctx = self.context
        try:
            ensure_ownership()
            with operation("apple.status", job_id=job_id, submission_id=identifier) as span:
                status = ctx.apple.status(identifier)
                span.set_attribute("apple.notarization.status", status)
            if status != "Accepted":
                return None
            return self._fetch_log(identifier, job_id)
        except OperationError as exc:
            message = ctx.redactor.clean(str(exc))
            logger.warning("job.notary_details.failed\njob_id: %s\nreason: %s", job_id, message)
            raise ApiError(502, message) from None
        except ValueError:
            raise ApiError(502, "Apple returned invalid notarization details.") from None

    def _fetch_log(self, identifier: str, job_id: str) -> tuple[NotaryDetails, str]:
        ensure_ownership()
        with operation("apple.notary_log", job_id=job_id, submission_id=identifier):
            value = self._clean(self.context.apple.log(identifier))
        details = self._validate(value, identifier)
        return details, json.dumps(value, ensure_ascii=False)

    def _clean(self, value: object) -> object:
        # Redact string values before serialization so quotes, URL query strings,
        # and credentials cannot turn an otherwise valid log into invalid JSON.
        if isinstance(value, str):
            return self.context.redactor.clean(value)
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        return value

    @staticmethod
    def _validate(value: object, identifier: str | None) -> NotaryDetails:
        details = NotaryDetails.model_validate(value)
        if identifier is None or UUID(details.jobId) != UUID(identifier):
            raise ValueError("Unexpected notarization job ID.")
        return details
