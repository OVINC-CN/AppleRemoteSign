import asyncio
import json
import logging
import re
import sys
from contextlib import contextmanager, nullcontext
from io import StringIO

import httpx
import pytest
from starlette.responses import PlainTextResponse

from apple_remote_sign import observability
from apple_remote_sign.redaction import Redactor
from tests.archive_helpers import dmg_zip


@contextmanager
def captured_logs():
    observability.configure_logging()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(observability.JsonFormatter())
    logger = logging.getLogger()
    original = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original)
        handler.close()


def entries(stream):
    result = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert result
    for entry in result:
        assert set(entry) == {"timestamp", "level", "message", "trace_id", "span_id"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", entry["timestamp"])
        if entry["trace_id"]:
            assert re.fullmatch(r"[0-9a-f]{32}", entry["trace_id"])
            assert re.fullmatch(r"[0-9a-f]{16}", entry["span_id"])
            assert int(entry["trace_id"], 16) and int(entry["span_id"], 16)
        else:
            assert entry["span_id"] == ""
        assert not re.search(r"\b(?:request_id|trace_id|span_id)(?:=|:)", entry["message"])
    return result


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR])
@pytest.mark.parametrize("traced", [False, True])
@pytest.mark.parametrize("identity", ["", "user_id: user\nkey_id: key\napplication_id: app"])
def test_json_formatter_has_structured_trace_fields_and_redacts_exceptions(
    monkeypatch, level, traced, identity
):
    monkeypatch.setattr(observability, "_redactor", Redactor(["test-password"]))
    try:
        raise RuntimeError("签名失败\nBearer a-secret test-password")
    except RuntimeError:
        record = logging.getLogger("format-test").makeRecord(
            "format-test",
            level,
            __file__,
            1,
            "多行\n消息 %s\nreason: %s",
            ("✓", "words stay together"),
            sys.exc_info(),
            extra={"password": "do-not-log-this", "other": "extra-field"},
        )
    actor_token = observability.actor.set(identity)
    try:
        with observability.operation("format-test") if traced else nullcontext() as span:
            encoded = observability.JsonFormatter().format(record)
    finally:
        observability.actor.reset(actor_token)
    value = entries(StringIO(encoded))[0]
    assert value["level"] == logging.getLevelName(level)
    assert "RuntimeError" in value["message"] and "Traceback" in value["message"]
    prefix = f"{identity}\n" if identity else ""
    assert value["message"].startswith(
        prefix + "多行\n消息 ✓\nreason: words stay together\nTraceback"
    )
    assert len(encoded.splitlines()) == 1
    assert value["trace_id"] == (f"{span.get_span_context().trace_id:032x}" if traced else "")
    assert value["span_id"] == (f"{span.get_span_context().span_id:016x}" if traced else "")
    for secret in ["a-secret", "test-password", "do-not-log-this", "extra-field"]:
        assert secret not in encoded


def test_requests_have_correlated_logs_without_query_headers_or_body(client):
    with captured_logs() as stream:
        response = client.get(
            "/api/v1/me?code=secret-code&state=secret-state",
            headers={"Authorization": "Bearer secret-token", "Cookie": "ars_session=secret-cookie"},
        )
    assert response.status_code == 401
    messages = [entry["message"] for entry in entries(stream)]
    rid = response.headers["x-request-id"]
    trace_id = response.headers["x-trace-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id) and int(trace_id, 16)
    assert all(rid not in message for message in messages)
    assert all(entry["trace_id"] == trace_id for entry in entries(stream))
    assert "authentication.denied\nstatus: 401\nreason: Please sign in." in messages
    assert all(entry["level"] == "WARNING" for entry in entries(stream))
    for secret in ["secret-code", "secret-state", "secret-token", "secret-cookie"]:
        assert secret not in stream.getvalue()


def test_signing_failure_and_refresh_are_traceable(client, tool):
    tool.fail_submit = True
    with captured_logs() as stream:
        result = client.post("/openapi/v1/jobs", files={"file": ("failed-1.2.3.zip", dmg_zip())})
        job_id = result.json()["data"]["id"]
        response = client.get(f"/openapi/v1/jobs/{job_id}")
    assert result.status_code == response.status_code == 500
    logged = entries(stream)
    created = next(entry["message"] for entry in logged if "job.created" in entry["message"])
    assert created.startswith("user_id: -\nkey_id: ")
    assert f"\njob.created\njob_id: {job_id}\napplication_id: " in created
    assert created.endswith("\nsource: openapi")
    assert all("=" not in entry["message"].split("\nreason: ", 1)[0] for entry in logged)
    messages = "\n".join(entry["message"] for entry in logged)
    for event in [
        "job.created",
        "job.signed",
        "job.submission_id_saved",
        "job.failed",
        "stage: submit",
        f"job_id: {job_id}",
    ]:
        assert event in messages
    assert {entry["trace_id"] for entry in entries(stream)} == {
        result.headers["x-trace-id"],
        response.headers["x-trace-id"],
    }
    for removed in [".started", ".completed", ".finished", "duration_ms", "request.pending"]:
        assert removed not in messages


def test_unexpected_exception_has_a_redacted_stack_and_trace_id(client):
    @client.app.get("/test-failure")
    def fail():
        raise RuntimeError("Bearer unexpected-secret")

    with captured_logs() as stream:
        response = client.get("/test-failure")
    assert response.status_code == 500
    errors = [entry for entry in entries(stream) if "request.failed" in entry["message"]]
    assert len(errors) == 1
    assert response.headers["x-request-id"] not in stream.getvalue()
    assert response.headers["x-trace-id"] == errors[0]["trace_id"]
    unhandled = next(entry for entry in entries(stream) if "request.unhandled" in entry["message"])
    assert unhandled["trace_id"] == errors[0]["trace_id"]
    assert unhandled["span_id"] == errors[0]["span_id"]
    assert unhandled["message"] == "request.unhandled\ntype: RuntimeError"
    assert "request.failed\nmethod: GET\npath: /test-failure\nTraceback" in errors[0]["message"]
    assert "Traceback" in errors[0]["message"] and "RuntimeError" in errors[0]["message"]
    assert "unexpected-secret" not in stream.getvalue()


def test_slow_requests_remain_quiet_and_restore_context():
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(scope, receive, send):
            entered.set()
            await release.wait()
            await PlainTextResponse("done")(scope, receive, send)

        app = observability.RequestLoggingMiddleware(slow)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(client.get("/slow"))
            await entered.wait()
            assert stream.getvalue() == ""
            release.set()
            response = await pending
        assert observability.actor.get() == ""
        return response

    with captured_logs() as stream:
        response = asyncio.run(exercise())
    assert response.status_code == 200
    assert int(response.headers["x-trace-id"], 16)
    assert stream.getvalue() == ""


def test_healthy_requests_and_unchanged_refreshes_remain_quiet(client):
    job = client.post("/openapi/v1/jobs", files={"file": ("test-1.2.3.zip", dmg_zip())}).json()[
        "data"
    ]
    with captured_logs() as stream:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/openapi/v1/openapi.json").status_code == 200
        assert client.get(f"/openapi/v1/jobs/{job['id']}").status_code == 200
    assert stream.getvalue() == ""


def test_redis_outage_does_not_leak_connection_credentials(client, redis):
    with captured_logs() as stream:
        redis.state.available = False
        response = client.post("/openapi/v1/jobs", files={"file": ("app-1.2.3.zip", dmg_zip())})
    assert response.status_code == 503
    assert "never-log-this" not in response.text
    assert "never-log-this" not in stream.getvalue()


def test_log_redaction_covers_database_passwords_and_generated_api_keys():
    text = "mysql+pymysql://user:password@db/app ars_" + "s" * 43
    cleaned = Redactor().clean(text)
    assert "password" not in cleaned and "s" * 43 not in cleaned
