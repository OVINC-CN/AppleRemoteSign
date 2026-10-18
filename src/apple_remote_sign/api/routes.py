from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.security import HTTPBearer

from apple_remote_sign.api.dependencies import access, context, principal
from apple_remote_sign.api.responses import ArtifactResponse
from apple_remote_sign.domain import Envelope
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService

router = APIRouter(
    prefix="/openapi/v1",
    dependencies=[Depends(HTTPBearer(auto_error=False))],
    responses={
        code: {"model": Envelope, "description": description}
        for code, description in {
            401: "Missing or invalid Bearer token",
            404: "Job not found",
            422: "Invalid request",
            500: "Job or service failed; data contains task information when available",
            502: "Apple or S3 query failed; persisted status is preserved",
            503: "Redis coordination unavailable or task lease lost; query an existing task first",
        }.items()
    },
)


@router.post("/jobs", response_model=Envelope, summary="Sign and submit a file for notarization")
def submit_job(
    request: Request,
    file: Annotated[
        UploadFile,
        File(
            description="ZIP archive containing one top-level macOS .app directory or .dmg file. "
            "The ZIP filename must contain a version number (X.Y.Z); "
            "a v prefix and any prerelease suffix are allowed."
        ),
    ],
) -> Envelope:
    identity = principal(request)
    app = access(request).require_application(identity.application_id or "", identity, signing=True)
    service = SubmissionService(context(request), app.id, key_id=identity.key_id)
    return Envelope(data=service.submit(file.filename, file.file))


@router.get(
    "/jobs/{job_id}",
    response_model=Envelope,
    summary="Query Apple once, staple and upload the accepted artifact to S3",
)
def query_job(request: Request, job_id: UUID) -> Envelope:
    job = access(request).require_job(str(job_id), principal(request))
    service = QueryService(context(request), job.application_id)
    return Envelope(data=service.query(str(job_id)))


@router.get(
    "/jobs/{job_id}/artifact",
    response_class=ArtifactResponse,
    status_code=302,
    summary="Redirect to an authenticated CDN URL for the completed artifact",
    responses={
        302: {
            "description": "Fresh authenticated CDN URL; the API does not return file bytes",
            "headers": {"Location": {"schema": {"type": "string", "format": "uri"}}},
        },
        409: {"model": Envelope, "description": "Artifact not ready"},
        410: {"model": Envelope, "description": "File expired or no longer available in S3"},
    },
)
def download_artifact(request: Request, job_id: UUID) -> ArtifactResponse:
    job = access(request).require_job(str(job_id), principal(request))
    service = QueryService(context(request), job.application_id)
    return ArtifactResponse(service.artifact(str(job_id)))
