from sqlalchemy import func, or_, select

from apple_remote_sign.access import Principal
from apple_remote_sign.api.web_models import ApplicationView, JobDetail, JobPage, JobView
from apple_remote_sign.domain import ApiError, JobStatus
from apple_remote_sign.persistence.access_models import ApiKey, Application, User
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.persistence.models import Job


class HistoryService:
    def __init__(self, access: AccessRepository) -> None:
        self.access = access

    def list(
        self,
        principal: Principal,
        page: int,
        page_size: int,
        application_id: str | None,
        status: JobStatus | None,
        filename: str | None,
    ) -> JobPage:
        if application_id:
            self.access.require_application(application_id, principal)
        allowed = self.access.application_query(principal).with_only_columns(Application.id)
        query = select(Job).where(Job.application_id.in_(allowed))
        if application_id:
            query = query.where(Job.application_id == application_id)
        if status:
            query = query.where(Job.status == status.value)
        if filename:
            query = query.where(
                or_(
                    Job.filename.contains(filename, autoescape=True),
                    Job.display_name.contains(filename, autoescape=True),
                )
            )
        with self.access.sessions() as session:
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            jobs = session.scalars(
                query.order_by(Job.created_at.desc(), Job.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        return JobPage(
            items=[self.view(job) for job in jobs], total=total, page=page, page_size=page_size
        )

    def detail(self, job_id: str, principal: Principal) -> JobDetail:
        job = self.access.require_job(job_id, principal)
        return JobDetail(
            **self.view(job).model_dump(),
            diagnostics=job.diagnostics,
            notary_log=job.notary_log,
            original_sha256=job.original_sha256,
            artifact_sha256=job.artifact_sha256,
        )

    def view(self, job: Job) -> JobView:
        with self.access.sessions() as session:
            app = session.get(Application, job.application_id)
            if app is None:
                raise ApiError(404, "Application not found.")
            source = "Unknown"
            if job.submitted_by_user_id:
                user = session.get(User, job.submitted_by_user_id)
                source = f"用户 · {user.username}" if user else "用户"
            elif job.submitted_by_key_id:
                key = session.get(ApiKey, job.submitted_by_key_id)
                source = f"OpenAPI · {key.name}" if key else "OpenAPI"
            return JobView(
                id=job.id,
                application=ApplicationView.model_validate(app),
                filename=job.filename,
                display_name=job.display_name or job.filename,
                status=JobStatus(job.status),
                submission_id=job.submission_id,
                source=source,
                size_bytes=job.size_bytes,
                error_message=job.error_message,
                created_at=job.created_at,
                updated_at=job.updated_at,
                last_checked_at=job.last_checked_at,
            )
