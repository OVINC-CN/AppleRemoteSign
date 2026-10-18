import logging
from uuid import uuid4

from sqlalchemy import LargeBinary, cast, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from apple_remote_sign.domain import JobStatus
from apple_remote_sign.locks import ensure_ownership
from apple_remote_sign.persistence import access_models  # noqa: F401
from apple_remote_sign.persistence.models import Base, Job, utcnow

logger = logging.getLogger(__name__)


class JobRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def check_schema(self) -> None:
        try:
            with self.sessions() as session:
                for table in Base.metadata.sorted_tables:
                    session.execute(select(table).limit(0))
        except SQLAlchemyError:
            logger.exception("database.schema_check_failed")
            raise RuntimeError(
                "Database unavailable or schema missing/incompatible. "
                "For new databases, apply support-files/sql/0001_init.sql manually; "
                "the service never executes DDL."
            ) from None

    def check_connection(self) -> None:
        with self.sessions() as session:
            session.execute(select(1))

    def create(
        self,
        application_id: str,
        filename: str,
        sha256: str | None = None,
        *,
        user_id: str | None = None,
        key_id: str | None = None,
        job_id: str | None = None,
    ) -> Job:
        job = Job(
            id=job_id or str(uuid4()),
            application_id=application_id,
            submitted_by_user_id=user_id,
            submitted_by_key_id=key_id,
            filename=filename,
            original_sha256=sha256,
            status=JobStatus.PREPARING.value,
        )
        with self.sessions.begin() as session:
            ensure_ownership()
            session.add(job)
            session.flush()
            ensure_ownership()
        return job

    def find_reusable(self, application_id: str, sha256: str, filename: str) -> Job | None:
        with self.sessions() as session:
            return session.scalar(
                select(Job)
                .where(
                    Job.application_id == application_id,
                    Job.original_sha256 == sha256,
                    # Compare exact bytes even with MySQL's case-insensitive collation.
                    cast(Job.filename, LargeBinary) == filename.encode("utf-8"),
                    Job.status.in_(
                        [
                            JobStatus.PREPARING.value,
                            JobStatus.PROCESSING.value,
                            JobStatus.COMPLETED.value,
                        ]
                    ),
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(1)
            )

    def get(self, job_id: str) -> Job | None:
        with self.sessions() as session:
            return session.get(Job, job_id)

    def save(self, job: Job) -> None:
        job.updated_at = utcnow()
        with self.sessions.begin() as session:
            ensure_ownership()
            session.merge(job)
            session.flush()
            ensure_ownership()

    def interrupted_ids(self) -> list[str]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(Job.id).where(
                        Job.status.in_([JobStatus.PREPARING.value, JobStatus.PROCESSING.value]),
                        Job.submission_id.is_(None),
                    )
                )
            )

    def recover_interrupted(self, job_id: str) -> bool:
        # The caller must hold this task's Redis lease. Recheck persisted state in
        # the UPDATE as another request may have completed since the scan.
        with self.sessions.begin() as session:
            ensure_ownership()
            result = session.connection().execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.in_([JobStatus.PREPARING.value, JobStatus.PROCESSING.value]),
                    Job.submission_id.is_(None),
                )
                .values(
                    status=JobStatus.FAILED.value,
                    error_message="Processing interrupted before a submission ID was saved. "
                    "No automatic resubmission was attempted.",
                    updated_at=utcnow(),
                )
            )
            ensure_ownership()
        return bool(result.rowcount)
