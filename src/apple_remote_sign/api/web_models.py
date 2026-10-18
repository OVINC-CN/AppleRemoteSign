from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from apple_remote_sign.domain import JobStatus

UtcTime = Annotated[datetime, AfterValidator(lambda value: value.replace(tzinfo=UTC))]


class WebModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class WebEnvelope[T](BaseModel):
    data: T
    error_message: str = ""


class ArtifactLink(WebModel):
    url: str


class ApplicationView(WebModel):
    id: str
    identifier: str
    name: str
    enabled: bool
    created_at: UtcTime


class ApplicationCreate(WebModel):
    identifier: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=255)


class ApplicationUpdate(WebModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None


class GrantUpdate(WebModel):
    usernames: list[str] = Field(max_length=1000)


class KeyCreate(WebModel):
    name: str = Field(min_length=1, max_length=255)


class KeyView(WebModel):
    id: str
    name: str
    prefix: str
    created_at: UtcTime
    last_used_at: UtcTime | None
    revoked_at: UtcTime | None


class CreatedKey(WebModel):
    key: KeyView
    token: str


class MeView(WebModel):
    username: str
    is_admin: bool
    csrf_token: str
    max_upload_bytes: int


class JobUpdate(WebModel):
    display_name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=255,
            pattern=r"^[^\x00-\x1f\x7f]+$",
        ),
    ]


class JobView(WebModel):
    id: str
    application: ApplicationView
    filename: str
    display_name: str
    status: JobStatus
    submission_id: str | None
    source: str
    size_bytes: int | None
    error_message: str
    created_at: UtcTime
    updated_at: UtcTime
    last_checked_at: UtcTime | None


class JobDetail(JobView):
    diagnostics: str
    notary_log: str | None
    original_sha256: str | None
    artifact_sha256: str | None


class JobPage(WebModel):
    items: list[JobView]
    total: int
    page: int
    page_size: int
