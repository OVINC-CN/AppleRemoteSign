import asyncio
import signal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from apple_remote_sign import __main__ as entrypoint
from apple_remote_sign.app import create_app
from apple_remote_sign.domain import CoordinationError
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.persistence.models import Job
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip
from tests.fakes import TEST_APPLICATION_ID


@pytest.mark.parametrize("authorization", [None, "Bearer wrong"])
def test_readiness_checks_dependencies_without_authentication(client, redis, authorization):
    client.headers.pop("Authorization", None)
    headers = {"Authorization": authorization} if authorization else {}
    assert client.get("/ready", headers=headers).json() == {"status": "ok"}
    redis.state.available = False
    response = client.get("/ready", headers=headers)
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/health").status_code == 200
    redis.state.available = True
    assert client.get("/ready").status_code == 200


def test_database_outage_only_fails_readiness(client, context, monkeypatch):
    def unavailable():
        raise SQLAlchemyError("private-database-password")

    monkeypatch.setattr(context.repository, "check_connection", unavailable)
    response = client.get("/ready")
    assert response.status_code == 503
    assert "private-database-password" not in response.text
    assert client.get("/health").status_code == 200


def test_readiness_is_false_before_startup_and_after_shutdown(settings, context):
    app = create_app(settings, context)
    assert app.state.ready is False
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        app.state.ready = False
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200
        app.state.ready = True
    assert app.state.ready is False


def test_redis_unavailable_prevents_startup(settings, context, redis):
    redis.state.available = False
    app = create_app(settings, context)
    with pytest.raises(CoordinationError), TestClient(app):
        pytest.fail("An unavailable coordinator must not become ready")
    assert app.state.ready is False


def test_upload_outage_returns_503_without_creating_a_task(client, context, redis):
    redis.state.available = False
    response = client.post("/openapi/v1/jobs", files={"file": ("app-1.2.3.zip", dmg_zip())})
    assert response.status_code == 503 and response.json()["data"] is None
    assert "never-log-this" not in response.text
    with context.repository.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.parametrize("stage", ["sign", "submit"])
def test_lost_lease_returns_task_id_and_stops_future_writes(
    client, context, tool, redis, monkeypatch, stage
):
    original = getattr(tool, stage)

    def lose_lease(*args):
        result = original(*args)
        with context.repository.sessions() as session:
            identifier = session.scalar(select(Job.id))
        redis.state.expire(identifier)
        return result

    monkeypatch.setattr(tool, stage, lose_lease)
    response = client.post("/openapi/v1/jobs", files={"file": ("app-1.2.3.zip", dmg_zip())})
    assert response.status_code == 503
    data = response.json()["data"]
    stored = context.repository.get(data["id"])
    assert stored.status == "processing" and stored.error_message == ""
    assert data["submission_id"] == stored.submission_id
    assert [name for name, _ in tool.calls] == (["sign"] if stage == "sign" else ["sign", "submit"])


def test_delivery_loses_lease_without_marking_completed(client, context, apple, artifacts, redis):
    data = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "app-1.2.3.zip", BytesIO(dmg_zip())
    )
    apple.current = "Accepted"
    artifacts.before_upload = lambda: redis.state.expire(data.id)
    response = client.get(f"/openapi/v1/jobs/{data.id}")
    assert response.status_code == 503 and response.json()["data"]["id"] == data.id
    stored = context.repository.get(data.id)
    assert stored.status == "processing" and stored.artifact_s3_key is None


def test_configured_graceful_timeout_is_forwarded_to_uvicorn(settings, monkeypatch):
    settings.graceful_shutdown_timeout_seconds = 123
    run = Mock()
    monkeypatch.setattr(entrypoint, "Settings", lambda: settings)
    monkeypatch.setattr(entrypoint.uvicorn, "run", run)
    monkeypatch.setattr("sys.argv", ["apple-remote-sign"])
    entrypoint.main()
    assert run.call_args.kwargs["timeout_graceful_shutdown"] == 123
    assert run.call_args.kwargs["workers"] == 1


def test_sigterm_drain_keeps_request_lease_alive_until_response_completes(settings, context, redis):
    # Exercise Uvicorn's shutdown lifecycle with an in-memory request task. No
    # listener, subprocess, real Redis or application server is started by this test.
    context.locks = JobLocks(redis, ttl_seconds=1, renew_interval_seconds=0.01)
    app = create_app(settings, context)

    async def exercise():
        release = asyncio.Event()
        server = uvicorn.Server(uvicorn.Config(app, timeout_graceful_shutdown=3, log_config=None))
        server.servers = []
        server.lifespan = SimpleNamespace(shutdown=AsyncMock())

        async def request():
            with context.locks.hold("in-flight-request"):
                await release.wait()

        async with app.router.lifespan_context(app):
            task = asyncio.create_task(request())
            server.server_state.tasks.add(task)
            task.add_done_callback(server.server_state.tasks.discard)
            await asyncio.sleep(0)
            server.handle_exit(signal.SIGTERM, None)
            assert server.should_exit
            draining = asyncio.create_task(server.shutdown())
            try:
                assert await asyncio.to_thread(redis.state.renewed.wait, 2)
                assert not draining.done()
                assert app.state.ready is True
                assert redis.state.released == []
                server.lifespan.shutdown.assert_not_awaited()
            finally:
                release.set()
                await asyncio.wait_for(task, 2)
                await asyncio.wait_for(draining, 2)
            server.lifespan.shutdown.assert_awaited_once()
        assert app.state.ready is False
        assert redis.state.released == ["apple-remote-sign:lock:in-flight-request"]

    asyncio.run(exercise())
