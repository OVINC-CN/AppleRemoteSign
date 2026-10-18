"""JSON logging and request tracing. Credentials never belong in log messages."""

import asyncio
import json
import logging
import logging.config
from collections.abc import Iterable
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apple_remote_sign.redaction import Redactor
from apple_remote_sign.tracing import operation as operation
from apple_remote_sign.tracing import record_error, start_span, tracing_context

actor: ContextVar[str] = ContextVar("actor", default="")
_redactor = Redactor()
_configured = False
logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            message += "\n" + self.formatStack(record.stack_info)
        span_context = trace.get_current_span().get_span_context()
        if context := actor.get():
            message = context + "\n" + message
        # Explicit construction prevents extras, exception fields or logger metadata leaking out.
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "level": record.levelname,
                "message": _redactor.clean(message),
                "trace_id": f"{span_context.trace_id:032x}" if span_context.is_valid else "",
                "span_id": f"{span_context.span_id:016x}" if span_context.is_valid else "",
            },
            ensure_ascii=False,
        )


LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apple_remote_sign.observability.JsonFormatter"}},
    "handlers": {
        "json": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"handlers": ["json"], "level": "WARNING"},
    "loggers": {
        "apple_remote_sign": {"handlers": [], "propagate": True, "level": "INFO"},
        "uvicorn": {"handlers": [], "propagate": True, "level": "WARNING"},
        "uvicorn.error": {"handlers": [], "propagate": True, "level": "WARNING"},
        # Request details and durations belong to spans, without query strings or credentials.
        "uvicorn.access": {"handlers": [], "propagate": True, "level": "WARNING"},
        "httpx": {"handlers": [], "propagate": True, "level": "WARNING"},
        "httpcore": {"handlers": [], "propagate": True, "level": "WARNING"},
        "authlib": {"handlers": [], "propagate": True, "level": "WARNING"},
    },
}


def set_log_secrets(secrets: Iterable[str]) -> None:
    global _redactor
    _redactor = Redactor(secrets)


def configure_logging() -> None:
    global _configured
    if not _configured:
        logging.config.dictConfig(LOGGING_CONFIG)
        logging.captureWarnings(True)
        _configured = True


@dataclass
class RequestTrace:
    scope: Scope
    receive: Receive
    send: Send
    id: str = field(default_factory=lambda: uuid4().hex)
    status: int = 500
    sent: int = 0
    received: int = 0
    complete: bool = False

    async def read(self) -> Message:
        message = await self.receive()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
        elif message["type"] == "http.disconnect":
            trace.get_current_span().add_event("request.disconnected")
        return message

    async def write(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            message["headers"] = [
                *message["headers"],
                (b"x-request-id", self.id.encode()),
                (b"x-trace-id", self.scope["state"]["trace_id"].encode()),
            ]
        await self.send(message)
        if message["type"] == "http.response.body":
            self.sent += len(message.get("body", b""))
            self.complete = not message.get("more_body", False)

    async def run(self, app: ASGIApp) -> None:
        method, path = self.scope["method"], self.scope["path"]
        span = trace.get_current_span()
        span.set_attribute("http.request.method", method)
        span.set_attribute("url.path", path)
        try:
            await app(self.scope, self.read, self.write)
        except asyncio.CancelledError:
            logger.warning("request.cancelled\nmethod: %s\npath: %s", method, path)
            raise
        except Exception:
            logger.exception("request.failed\nmethod: %s\npath: %s", method, path)
            raise
        finally:
            span.set_attribute("http.response.status_code", self.status)
            span.set_attribute("http.request.body.size", self.received)
            span.set_attribute("http.response.body.size", self.sent)
            span.set_attribute("http.response.complete", self.complete)
            route = self.scope.get("route")
            if route is not None:
                span.set_attribute("http.route", route.path)
                span.update_name(f"{method} {route.path}")
            if self.status >= 500:
                span.set_status(StatusCode.ERROR)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp, tracer_provider: TracerProvider | None = None) -> None:
        self.app = app
        self.tracer_provider = tracer_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_trace = RequestTrace(scope, receive, send)
        actor_token = actor.set("")
        state = scope.setdefault("state", {})
        state["request_id"] = request_trace.id
        headers = Headers(scope=scope)
        parent = TraceContextTextMapPropagator().extract(
            {key: headers[key] for key in ("traceparent", "tracestate") if key in headers}
        )
        try:
            manager = (
                tracing_context(self.tracer_provider) if self.tracer_provider else nullcontext()
            )
            with manager:
                span = start_span(str(scope["method"]), kind=SpanKind.SERVER, context=parent)
                state["span_context"] = span.get_span_context()
                state["trace_id"] = f"{state['span_context'].trace_id:032x}"
                with trace.use_span(
                    span, end_on_exit=True, record_exception=False, set_status_on_exception=False
                ):
                    try:
                        await request_trace.run(self.app)
                    except BaseException as exc:
                        record_error(span, exc)
                        raise
        finally:
            actor.reset(actor_token)
