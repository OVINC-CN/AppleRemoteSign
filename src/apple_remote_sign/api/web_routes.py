from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, Response, UploadFile

from apple_remote_sign.api.dependencies import access, context, principal
from apple_remote_sign.api.web_models import (
    ApplicationView,
    ArtifactLink,
    JobDetail,
    JobPage,
    JobUpdate,
    JobView,
    MeView,
    WebEnvelope,
)
from apple_remote_sign.domain import ApiError, Envelope, JobStatus
from apple_remote_sign.notary import NotaryDetails
from apple_remote_sign.services.history import HistoryService
from apple_remote_sign.services.notary import NotaryDetailsService
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService

router = APIRouter(prefix="/api/v1", include_in_schema=False)


@router.get("/me")
def me(request: Request) -> WebEnvelope[MeView]:
    user = principal(request)
    return WebEnvelope(
        data=MeView(
            username=user.username or "",
            is_admin=user.is_admin,
            csrf_token=user.csrf_token or "",
            max_upload_bytes=request.app.state.settings.max_upload_bytes,
        )
    )


@router.get("/applications")
def applications(request: Request) -> WebEnvelope[list[ApplicationView]]:
    return WebEnvelope(
        data=[
            ApplicationView.model_validate(app)
            for app in access(request).applications(principal(request))
        ]
    )


@router.get("/jobs")
def jobs(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    application_id: UUID | None = None,
    status: JobStatus | None = None,
    filename: Annotated[str | None, Query(max_length=255)] = None,
) -> WebEnvelope[JobPage]:
    data = HistoryService(access(request)).list(
        principal(request),
        page,
        page_size,
        str(application_id) if application_id else None,
        status,
        filename,
    )
    return WebEnvelope(data=data)


@router.get("/jobs/{job_id}")
def detail(request: Request, job_id: UUID) -> WebEnvelope[JobDetail]:
    return WebEnvelope(data=HistoryService(access(request)).detail(str(job_id), principal(request)))


@router.get("/jobs/{job_id}/notary-details")
def notary_details(request: Request, job_id: UUID) -> WebEnvelope[NotaryDetails | None]:
    job = access(request).require_job(str(job_id), principal(request))
    return WebEnvelope(
        data=NotaryDetailsService(context(request), job.application_id).details(job.id)
    )


@router.patch("/jobs/{job_id}")
def rename(request: Request, job_id: UUID, body: JobUpdate) -> WebEnvelope[JobView]:
    ctx = context(request)
    snapshot = access(request).require_job(str(job_id), principal(request))
    with ctx.hold_job(snapshot, blocking=False) as acquired:
        if not acquired:
            raise ApiError(
                409, "This job is already being processed or refreshed. Please try again shortly."
            )
        job = access(request).require_job(str(job_id), principal(request))
        job.display_name = body.display_name
        ctx.repository.save(job)
    return WebEnvelope(data=HistoryService(access(request)).view(job))


@router.post("/applications/{application_id}/jobs")
def submit(
    request: Request,
    application_id: UUID,
    file: Annotated[UploadFile, File()],
) -> Envelope:
    user = principal(request)
    app = access(request).require_application(str(application_id), user, signing=True)
    return Envelope(
        data=SubmissionService(context(request), app.id, user_id=user.user_id).submit(
            file.filename, file.file
        )
    )


@router.post("/jobs/{job_id}/refresh")
def refresh(request: Request, job_id: UUID) -> Envelope:
    job = access(request).require_job(str(job_id), principal(request))
    return Envelope(
        data=QueryService(context(request), job.application_id).query(job.id, wait=False)
    )


@router.get("/jobs/{job_id}/artifact-url")
def artifact_url(request: Request, response: Response, job_id: UUID) -> WebEnvelope[ArtifactLink]:
    job = access(request).require_job(str(job_id), principal(request))
    url = QueryService(context(request), job.application_id).artifact(job.id)
    response.headers["Cache-Control"] = "no-store"
    return WebEnvelope(data=ArtifactLink(url=url))
