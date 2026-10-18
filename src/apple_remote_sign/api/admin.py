from uuid import UUID

from fastapi import APIRouter, Request

from apple_remote_sign.api.dependencies import access, principal
from apple_remote_sign.api.web_models import (
    ApplicationCreate,
    ApplicationUpdate,
    ApplicationView,
    CreatedKey,
    GrantUpdate,
    KeyCreate,
    KeyView,
    WebEnvelope,
)
from apple_remote_sign.domain import ApiError

router = APIRouter(prefix="/api/v1/admin", include_in_schema=False)


@router.get("/users")
def users(request: Request) -> WebEnvelope[list[str]]:
    return WebEnvelope(data=sorted(request.app.state.settings.allowed_usernames))


@router.get("/applications")
def applications(request: Request) -> WebEnvelope[list[ApplicationView]]:
    return WebEnvelope(
        data=[
            ApplicationView.model_validate(app)
            for app in access(request).applications(principal(request))
        ]
    )


@router.post("/applications", status_code=201)
def create_application(request: Request, body: ApplicationCreate) -> WebEnvelope[ApplicationView]:
    app = access(request).create_application(body.identifier, body.name)
    return WebEnvelope(data=ApplicationView.model_validate(app))


@router.patch("/applications/{application_id}")
def update_application(
    request: Request,
    application_id: UUID,
    body: ApplicationUpdate,
) -> WebEnvelope[ApplicationView]:
    app = access(request).update_application(str(application_id), body.name, body.enabled)
    return WebEnvelope(data=ApplicationView.model_validate(app))


@router.get("/applications/{application_id}/users")
def grants(request: Request, application_id: UUID) -> WebEnvelope[list[str]]:
    return WebEnvelope(data=list(access(request).usernames(str(application_id))))


@router.put("/applications/{application_id}/users")
def set_grants(
    request: Request,
    application_id: UUID,
    body: GrantUpdate,
) -> WebEnvelope[None]:
    if not set(body.usernames) <= request.app.state.settings.allowed_usernames:
        raise ApiError(422, "Users must be on the login allowlist or administrator list.")
    access(request).set_users(str(application_id), body.usernames)
    return WebEnvelope(data=None)


@router.get("/applications/{application_id}/keys")
def keys(request: Request, application_id: UUID) -> WebEnvelope[list[KeyView]]:
    return WebEnvelope(
        data=[KeyView.model_validate(key) for key in access(request).keys(str(application_id))]
    )


@router.post("/applications/{application_id}/keys", status_code=201)
def create_key(
    request: Request,
    application_id: UUID,
    body: KeyCreate,
) -> WebEnvelope[CreatedKey]:
    key, token = access(request).create_key(str(application_id), body.name)
    return WebEnvelope(data=CreatedKey(key=KeyView.model_validate(key), token=token))


@router.post("/applications/{application_id}/keys/{key_id}/revoke")
def revoke_key(request: Request, application_id: UUID, key_id: UUID) -> WebEnvelope[None]:
    access(request).revoke_key(str(application_id), str(key_id))
    return WebEnvelope(data=None)
