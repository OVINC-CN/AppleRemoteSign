from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.services.context import ServiceContext

router = APIRouter(include_in_schema=False)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    if not request.app.state.ready:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    services: ServiceContext = request.app.state.context
    try:
        services.locks.ping()
        services.repository.check_connection()
    except CoordinationError, SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
