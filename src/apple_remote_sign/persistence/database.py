import logging
import time
from typing import Any

from opentelemetry.trace import SpanKind
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Connection, Engine, ExceptionContext, ExecutionContext
from sqlalchemy.pool import ConnectionPoolEntry

from apple_remote_sign.config import Settings
from apple_remote_sign.observability import operation
from apple_remote_sign.tracing import record_error, start_span

logger = logging.getLogger(__name__)
SQL_OPERATIONS = {"SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN", "COMMIT", "ROLLBACK", "SET"}


def build_engine(url: URL, settings: Settings) -> Engine:
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        hide_parameters=True,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
            "read_timeout": settings.database_io_timeout_seconds,
            "write_timeout": settings.database_io_timeout_seconds,
        },
    )

    instrument_engine(engine)
    return engine


def instrument_engine(engine: Engine) -> None:
    @event.listens_for(engine, "do_connect")
    def connecting(dialect: Any, _entry: ConnectionPoolEntry, args: Any, kwargs: Any) -> Any:
        with operation("database.connect", kind=SpanKind.CLIENT):
            return dialect.connect(*args, **kwargs)

    _instrument_pool(engine)
    _instrument_queries(engine)


def _instrument_pool(engine: Engine) -> None:
    @event.listens_for(engine, "checkout")
    def checkout(_dbapi: Any, entry: ConnectionPoolEntry, _proxy: Any) -> None:
        entry.info["checked_out_at"] = time.monotonic()
        entry.info["connection_span"] = start_span("database.connection", kind=SpanKind.CLIENT)

    @event.listens_for(engine, "checkin")
    def checkin(_dbapi: Any, entry: ConnectionPoolEntry) -> None:
        span = entry.info.pop("connection_span", None)
        if span is not None:
            span.end()
        started = entry.info.pop("checked_out_at", time.monotonic())
        if time.monotonic() - started >= 1:
            logger.warning("database.connection_held")


def _instrument_queries(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def before(
        _conn: Connection,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        context: ExecutionContext,
        _many: bool,
    ) -> None:
        span = start_span("database.query", kind=SpanKind.CLIENT)
        # Only the operation is captured: SQL and parameters can contain credentials.
        span.set_attribute("db.system.name", engine.dialect.name)
        verb = statement.split(maxsplit=1)[0].upper() if statement.strip() else ""
        span.set_attribute("db.operation.name", verb if verb in SQL_OPERATIONS else "OTHER")
        context._trace_span = span  # type: ignore[attr-defined]

    @event.listens_for(engine, "after_cursor_execute")
    def after(
        _conn: Connection,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        context: ExecutionContext,
        _many: bool,
    ) -> None:
        span = getattr(context, "_trace_span", None)
        if span is not None:
            span.end()

    @event.listens_for(engine, "handle_error")
    def error(context: ExceptionContext) -> None:
        span = getattr(context.execution_context, "_trace_span", None)
        if span is not None:
            record_error(span, context.original_exception)
            span.end()
        logger.error(
            "database.error\ntype: %s\ndisconnect: %s",
            type(context.original_exception).__name__,
            context.is_disconnect,
        )
