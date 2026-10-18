from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apple_remote_sign.domain import JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('preparing', 'processing', 'completed', 'failed')", name="ck_jobs_status"
        ),
        Index("ix_jobs_submission_id", "submission_id", unique=True),
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_hash_status", "application_id", "original_sha256", "status", "created_at"),
        Index("ix_jobs_application_created", "application_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    submitted_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    submitted_by_key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id"))
    filename: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PREPARING.value)
    submission_id: Mapped[str | None] = mapped_column(String(36))
    original_path: Mapped[str | None] = mapped_column(String(1024))
    submitted_path: Mapped[str | None] = mapped_column(String(1024))
    artifact_path: Mapped[str | None] = mapped_column(String(1024))
    artifact_s3_key: Mapped[str | None] = mapped_column(String(1024))
    original_sha256: Mapped[str | None] = mapped_column(String(64))
    submitted_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str] = mapped_column(Text, default="")
    diagnostics: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), default="")
    notary_log: Mapped[str | None] = mapped_column(Text().with_variant(LONGTEXT, "mysql"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
