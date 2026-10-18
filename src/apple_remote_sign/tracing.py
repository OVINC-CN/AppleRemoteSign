"""Always-on tracing; spans are discarded unless an OTLP/gRPC endpoint is configured."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Span, SpanKind, StatusCode

from apple_remote_sign.config import Settings


def _provider(service_name: str) -> TracerProvider:
    provider = TracerProvider(
        sampler=ALWAYS_ON,
        resource=Resource.create({"service.name": service_name}),
        shutdown_on_exit=False,
    )
    # Tracing is mandatory, including when OTEL_SDK_DISABLED is set in the environment.
    provider._disabled = False
    return provider


_default_provider = _provider("apple-remote-sign")
_current_provider: ContextVar[TracerProvider | None] = ContextVar("tracer_provider", default=None)


def build_tracer_provider(settings: Settings) -> TracerProvider:
    provider = _provider(settings.otel_service_name)
    endpoint = (
        settings.otel_exporter_otlp_traces_endpoint.strip()
        or settings.otel_exporter_otlp_endpoint.strip()
    )
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider


@contextmanager
def tracing_context(provider: TracerProvider) -> Iterator[None]:
    token = _current_provider.set(provider)
    try:
        yield
    finally:
        _current_provider.reset(token)


def start_span(
    name: str, *, kind: SpanKind = SpanKind.INTERNAL, context: Context | None = None
) -> Span:
    return (
        (_current_provider.get() or _default_provider)
        .get_tracer("apple_remote_sign")
        .start_span(name, kind=kind, context=context)
    )


def record_error(span: Span, error: BaseException) -> None:
    # Exception messages/stacks can contain tokens, SQL parameters and signed URLs.
    span.set_attribute("error.type", type(error).__name__)
    span.add_event("exception", {"exception.type": type(error).__name__})
    span.set_status(StatusCode.ERROR)


@contextmanager
def operation(
    name: str, *, kind: SpanKind = SpanKind.INTERNAL, **details: object
) -> Iterator[Span]:
    span = start_span(name, kind=kind)
    for key, value in details.items():
        span.set_attribute(
            key, value if isinstance(value, str | bool | int | float) else str(value)
        )
    with trace.use_span(
        span, end_on_exit=True, record_exception=False, set_status_on_exception=False
    ):
        try:
            yield span
        except BaseException as exc:
            record_error(span, exc)
            raise
