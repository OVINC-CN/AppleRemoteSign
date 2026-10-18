import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from starlette.exceptions import HTTPException

from apple_remote_sign.domain import ApiError, Envelope

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        logger.warning("request.rejected\nstatus: %d\nreason: %s", exc.status_code, exc)
        return JSONResponse(
            Envelope(data=exc.data, error_message=str(exc)).model_dump(mode="json"),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Avoid echoing input values (which can contain credentials).
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors()
        )
        logger.warning("request.validation_failed")
        return JSONResponse({"data": None, "error_message": details}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"data": None, "error_message": str(exc.detail)},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # The request middleware records the stack while its span is still active.
        span_context = getattr(request.state, "span_context", trace.INVALID_SPAN_CONTEXT)
        with trace.use_span(trace.NonRecordingSpan(span_context)):
            logger.error(
                "request.unhandled\ntype: %s",
                type(exc).__name__,
            )
        return JSONResponse(
            {"data": None, "error_message": "Internal service or database error."},
            status_code=500,
            headers={
                "X-Request-ID": getattr(request.state, "request_id", ""),
                "X-Trace-ID": getattr(request.state, "trace_id", ""),
            },
        )
