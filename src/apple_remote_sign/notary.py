from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NotaryModel(BaseModel):
    # Keep fields added by Apple when returning the original log to the browser.
    model_config = ConfigDict(extra="allow", strict=True)


class NotaryTicket(NotaryModel):
    path: str = Field(min_length=1)
    arch: str | None = None
    cdhash: str
    digestAlgorithm: str


class NotaryIssue(NotaryModel):
    severity: str | None = None
    code: int | str | None = None
    path: str | None = None
    architecture: str | None = None
    message: str | None = None
    docUrl: str | None = None


class NotaryDetails(NotaryModel):
    archiveFilename: str = Field(min_length=1)
    jobId: str
    logFormatVersion: int
    sha256: str
    status: Literal["Accepted"]
    statusCode: int
    statusSummary: str
    uploadDate: str
    issues: list[NotaryIssue] | None = None
    ticketContents: list[NotaryTicket] | None = None
