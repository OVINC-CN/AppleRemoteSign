from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class JobStatus(StrEnum):
    PREPARING = "preparing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    submission_id: str | None


class Envelope(BaseModel):
    data: JobData | None = None
    error_message: str = ""


class ApiError(Exception):
    def __init__(self, status_code: int, message: str, data: JobData | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.data = data


class OperationError(Exception):
    """A signing, filesystem or Apple operation failed."""


class CoordinationError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "Redis coordination is unavailable or the task lease was lost. "
            "Query the existing task before attempting another upload.",
        )
