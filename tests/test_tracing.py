import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import Mock

import grpc
import httpx
import pytest
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
    TraceServiceServicer,
    add_TraceServiceServicer_to_server,
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign import tracing
from apple_remote_sign.app import create_app
from apple_remote_sign.integrations.process import CommandError, CommandRunner
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.observability import RequestLoggingMiddleware, operation
from apple_remote_sign.persistence.database import instrument_engine
from apple_remote_sign.redaction import Redactor
from tests.archive_helpers import dmg_zip


@pytest.fixture
def spans(settings):
    settings.otel_exporter_otlp_endpoint = ""
    settings.otel_exporter_otlp_traces_endpoint = ""
    provider = tracing.build_tracer_provider(settings)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with tracing.tracing_context(provider):
        yield provider, exporter
    provider.shutdown()


def capture_requests(client):
    exporter = InMemorySpanExporter()
    client.app.state.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_task_lease_outliving_content_lease_restores_trace_context(spans, redis):
    _, exporter = spans
    locks = JobLocks(redis)
    with operation("request") as request:
        with ExitStack() as tasks:
            with locks.hold("content"):
                tasks.enter_context(locks.hold("task"))
            with operation("processing"):
                pass
        assert trace.get_current_span() is request
    assert not trace.get_current_span().get_span_context().is_valid
    finished = exporter.get_finished_spans()
    held = [span for span in finished if span.name == "lock.hold"]
    assert len(held) == 2
    assert all(span.parent.span_id == request.get_span_context().span_id for span in held)
    assert all(span.end_time > span.start_time for span in held)


def test_missing_endpoint_discards_spans_but_cannot_disable_tracing(settings, monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    exporter = Mock(side_effect=AssertionError("No exporter should be created"))
    processor = Mock(side_effect=AssertionError("No export queue should be created"))
    monkeypatch.setattr(tracing, "OTLPSpanExporter", exporter)
    monkeypatch.setattr(tracing, "BatchSpanProcessor", processor)
    settings.otel_exporter_otlp_endpoint = ""
    settings.otel_exporter_otlp_traces_endpoint = ""
    provider = tracing.build_tracer_provider(settings)
    try:
        with tracing.tracing_context(provider), operation("test") as span:
            assert span.is_recording()
            assert span.get_span_context().is_valid
            assert span.get_span_context().trace_flags.sampled
        assert span.end_time > span.start_time
    finally:
        provider.shutdown()
    exporter.assert_not_called()
    processor.assert_not_called()


@pytest.mark.parametrize(
    ("base", "specific", "expected"),
    [
        (" http://collector:4317/ ", "", "http://collector:4317/"),
        ("collector:4317", "", "collector:4317"),
        ("https://collector:4320", "", "https://collector:4320"),
        ("http://unused:4317", " http://collector:4319 ", "http://collector:4319"),
    ],
)
def test_configured_endpoint_batches_and_flushes_on_shutdown(
    settings, context, monkeypatch, base, specific, expected
):
    from fastapi.testclient import TestClient

    exporter = InMemorySpanExporter()
    shutdown = Mock(wraps=exporter.shutdown)
    monkeypatch.setattr(exporter, "shutdown", shutdown)
    factory = Mock(return_value=exporter)
    monkeypatch.setattr(tracing, "OTLPSpanExporter", factory)
    settings.otel_exporter_otlp_endpoint = base
    settings.otel_exporter_otlp_traces_endpoint = specific
    settings.otel_service_name = "test-signer"
    with TestClient(create_app(settings, context)) as client:
        response = client.get("/health")
    factory.assert_called_once_with(endpoint=expected)
    shutdown.assert_called_once()
    request = next(span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER)
    assert f"{request.context.trace_id:032x}" == response.headers["x-trace-id"]
    assert request.resource.attributes["service.name"] == "test-signer"


@pytest.mark.parametrize(
    "endpoint_setting", ["otel_exporter_otlp_endpoint", "otel_exporter_otlp_traces_endpoint"]
)
def test_otlp_grpc_export_reaches_configured_collector(settings, monkeypatch, endpoint_setting):
    received = []

    class Collector(TraceServiceServicer):
        def Export(self, request, context):
            received.append(request)
            return ExportTraceServiceResponse()

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "token=test-apm-token")
    settings.otel_exporter_otlp_endpoint = ""
    settings.otel_exporter_otlp_traces_endpoint = ""
    with ThreadPoolExecutor(max_workers=1) as executor:
        server = grpc.server(executor)
        add_TraceServiceServicer_to_server(Collector(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            setattr(settings, endpoint_setting, f"http://127.0.0.1:{port}")
            provider = tracing.build_tracer_provider(settings)
            try:
                with tracing.tracing_context(provider), operation("otlp-test") as span:
                    trace_id = span.get_span_context().trace_id
            finally:
                provider.shutdown()
        finally:
            server.stop(grace=0).wait(timeout=5)
    assert len(received) == 1
    resource_spans = received[0].resource_spans[0]
    attributes = {item.key: item.value.string_value for item in resource_spans.resource.attributes}
    assert attributes["service.name"] == settings.otel_service_name
    assert attributes["token"] == "test-apm-token"
    exported = resource_spans.scope_spans[0].spans[0]
    assert exported.name == "otlp-test"
    assert int.from_bytes(exported.trace_id) == trace_id
    assert exported.end_time_unix_nano > exported.start_time_unix_nano


@pytest.mark.parametrize("flags", ["00", "01"])
def test_incoming_trace_context_is_continued_even_if_parent_is_unsampled(client, flags):
    exporter = capture_requests(client)
    trace_id, parent_id = "1" * 32, "2" * 16
    response = client.get(
        "/api/v1/me?code=secret-code",
        headers={"traceparent": f"00-{trace_id}-{parent_id}-{flags}", "tracestate": "test=value"},
    )
    assert response.status_code == 401
    assert response.headers["x-trace-id"] == trace_id
    spans = exporter.get_finished_spans()
    server = next(span for span in spans if span.kind == SpanKind.SERVER)
    assert server.parent.span_id == int(parent_id, 16)
    assert server.context.trace_state.get("test") == "value"
    assert server.attributes["http.response.status_code"] == 401
    assert all(span.context.trace_id == int(trace_id, 16) for span in spans)
    assert all(span.context.trace_flags.sampled for span in spans)
    assert "secret-code" not in "".join(span.to_json() for span in spans)


def test_invalid_trace_context_still_creates_trace_id_visible_to_browser(client, settings):
    exporter = capture_requests(client)
    response = client.get(
        "/health",
        headers={"traceparent": "invalid", "Origin": settings.frontend_url},
    )
    assert response.status_code == 200
    assert int(response.headers["x-trace-id"], 16)
    assert "X-Trace-ID" in response.headers["access-control-expose-headers"]
    server = next(span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER)
    assert server.parent is None
    assert f"{server.context.trace_id:032x}" == response.headers["x-trace-id"]


def test_signing_spans_follow_request_into_sync_workers_and_record_failure(client, tool):
    exporter = capture_requests(client)
    tool.fail_submit = True
    response = client.post("/openapi/v1/jobs", files={"file": ("test-1.2.3.zip", dmg_zip())})
    spans = exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    assert response.status_code == 500
    assert {"authentication", "authorization", "upload.validate", "job.sign", "job.submit"} <= (
        by_name.keys()
    )
    assert all(span.context.trace_id == int(response.headers["x-trace-id"], 16) for span in spans)
    assert all(span.end_time > span.start_time for span in spans)
    assert by_name["job.submit"].status.status_code == StatusCode.ERROR
    assert by_name["POST /openapi/v1/jobs"].status.status_code == StatusCode.ERROR
    assert not trace.get_current_span().get_span_context().is_valid


def test_streaming_request_span_includes_full_response_and_restores_context(spans):
    provider, exporter = spans

    async def exercise():
        async def stream(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            assert exporter.get_finished_spans() == ()
            await send({"type": "http.response.body", "body": b"last"})
            assert exporter.get_finished_spans() == ()

        app = RequestLoggingMiddleware(stream, tracer_provider=provider)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/stream?token=secret", content=b"upload-secret")
        assert not trace.get_current_span().get_span_context().is_valid
        return response

    response = asyncio.run(exercise())
    (span,) = exporter.get_finished_spans()
    assert response.content == b"firstlast"
    assert span.attributes["http.request.body.size"] == len(b"upload-secret")
    assert span.attributes["http.response.body.size"] == len(response.content)
    assert span.attributes["http.response.complete"] is True
    assert "secret" not in span.to_json()


def test_database_spans_capture_duration_and_errors_without_sql_or_parameters(spans):
    _, exporter = spans
    engine = create_engine("sqlite://")
    instrument_engine(engine)
    try:
        with operation("database-test") as parent, engine.connect() as connection:
            assert connection.scalar(text("SELECT :token"), {"token": "private-token"}) == (
                "private-token"
            )
            with pytest.raises(SQLAlchemyError):
                connection.execute(text("SELECT * FROM secret_table WHERE token = 'private-token'"))
            assert connection.scalar(text("/*private-comment*/ SELECT 1")) == 1
    finally:
        engine.dispose()
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "database-test",
        "database.connect",
        "database.connection",
        "database.query",
    }
    queries = [span for span in spans if span.name == "database.query"]
    assert len(queries) == 3
    assert queries[0].status.status_code == StatusCode.UNSET
    assert queries[1].status.status_code == StatusCode.ERROR
    assert queries[1].attributes["error.type"] == "OperationalError"
    assert queries[2].attributes["db.operation.name"] == "OTHER"
    assert all(span.context.trace_id == parent.get_span_context().trace_id for span in spans)
    assert all(span.end_time > span.start_time for span in spans)
    encoded = "".join(span.to_json() for span in spans)
    for secret in ["private-token", "secret_table", "SELECT :token", "private-comment"]:
        assert secret not in encoded


def test_command_failure_spans_do_not_capture_arguments_or_output(spans, tmp_path):
    _, exporter = spans
    runner = CommandRunner(tmp_path, Redactor())
    with pytest.raises(CommandError):
        runner.run([sys.executable, "-c", "raise RuntimeError('private-token')"], timeout=2)
    (span,) = exporter.get_finished_spans()
    assert span.name == "command.run" and span.status.status_code == StatusCode.ERROR
    assert "private-token" not in span.to_json()
