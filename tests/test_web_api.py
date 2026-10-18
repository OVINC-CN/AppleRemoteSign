from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from apple_remote_sign.app import create_app
from apple_remote_sign.domain import ApiError
from apple_remote_sign.persistence.access_models import ApiKey, WebSession
from apple_remote_sign.persistence.access_repository import AccessRepository, digest
from apple_remote_sign.persistence.auth_repository import AuthRepository
from apple_remote_sign.persistence.models import utcnow
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip
from tests.fakes import TEST_APPLICATION_ID


def login_as(client, context, settings, username):
    auth = AuthRepository(context.repository.sessions, settings)
    user = auth.bind_user(username, settings.oidc_issuer_url, f"subject-{username}")
    token = auth.create_session(user.id)
    client.cookies.clear()
    client.cookies.set(settings.cookie_name, token)
    client.headers["Origin"] = settings.frontend_url
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
    return token


@pytest.fixture
def web(settings, context):
    with TestClient(create_app(settings, context), raise_server_exceptions=False) as client:
        login_as(client, context, settings, "admin")
        yield client


@pytest.mark.parametrize(
    "path", ["/openapi/v1/jobs", f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs"]
)
@pytest.mark.parametrize(
    "filename",
    [
        "release.zip",
        "release-v1.2.zip",
        "release-v1x2x3.zip",
        "/releases/1.2.3/release.zip",
        r"C:\releases\v1.2.3\release.zip",
    ],
)
def test_missing_version_is_rejected_before_reading_upload(
    web, context, tool, apple, monkeypatch, path, filename
):
    monkeypatch.setattr(
        context.storage, "fingerprint", lambda *_: pytest.fail("Invalid filename read upload")
    )
    response = web.post(
        path,
        headers={"Authorization": "Bearer test-token"},
        files={"file": (filename, dmg_zip())},
    )
    assert response.status_code == 422
    assert response.json()["data"] is None
    assert "version number (X.Y.Z)" in response.json()["error_message"]
    assert web.get("/api/v1/jobs").json()["data"]["total"] == 0
    assert not context.storage.root.exists()
    assert tool.calls == [] and apple.calls == []


def test_admin_app_and_key_lifecycle(web, context):
    response = web.post(
        "/api/v1/admin/applications", json={"identifier": "com.demo", "name": "Demo"}
    )
    assert response.status_code == 201
    app_id = response.json()["data"]["id"]
    assert (
        web.post(
            "/api/v1/admin/applications", json={"identifier": "com.demo", "name": "Again"}
        ).status_code
        == 409
    )
    assert (
        web.patch(
            f"/api/v1/admin/applications/{app_id}", json={"identifier": "changed"}
        ).status_code
        == 422
    )
    prefix = f"/api/v1/admin/applications/{app_id}"
    assert web.put(prefix + "/users", json={"usernames": ["alice"]}).status_code == 200
    assert web.get(prefix + "/users").json()["data"] == ["alice"]
    assert web.put(prefix + "/users", json={"usernames": ["outsider"]}).status_code == 422
    response = web.post(prefix + "/keys", json={"name": "CI"})
    assert response.status_code == 201
    key = response.json()["data"]
    token = key["token"]
    assert token.startswith("ars_") and len(token) > 40
    listing = web.get(prefix + "/keys")
    assert token not in listing.text and "token_hash" not in listing.text
    with context.repository.sessions() as session:
        stored = session.get(ApiKey, key["key"]["id"])
        assert stored.token_hash == digest(token)
    response = web.post(
        "/openapi/v1/jobs",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("demo-1.2.3.zip", dmg_zip())},
    )
    assert response.status_code == 200
    job_id = response.json()["data"]["id"]
    stored = context.repository.get(job_id)
    assert stored.application_id == app_id
    assert stored.submitted_by_key_id == key["key"]["id"]
    assert web.post(prefix + f"/keys/{key['key']['id']}/revoke").status_code == 200
    assert (
        web.get(
            f"/openapi/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    assert web.get(prefix + "/keys").json()["data"][0]["revoked_at"]


def test_app_permissions_history_upload_download_and_revoke(web, context, settings, apple):
    access = AccessRepository(context.repository.sessions)
    other = access.create_application("hidden", "Hidden")
    visible = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "visible-1.2.3.zip", BytesIO(dmg_zip())
    )
    hidden = SubmissionService(context, other.id).submit("hidden-1.2.3.zip", BytesIO(dmg_zip()))
    access.set_users(TEST_APPLICATION_ID, ["alice"])
    login_as(web, context, settings, "alice")
    assert len(web.get("/api/v1/applications").json()["data"]) == 1
    page = web.get("/api/v1/jobs?page_size=1").json()["data"]
    assert page["total"] == 1 and page["items"][0]["id"] == visible.id
    assert web.get(f"/api/v1/jobs?application_id={other.id}").status_code == 404
    assert web.get("/api/v1/jobs?filename=hidden").json()["data"]["total"] == 0
    assert web.get(f"/api/v1/jobs/{hidden.id}").status_code == 404
    assert (
        web.patch(f"/api/v1/jobs/{hidden.id}", json={"display_name": "Not allowed"}).status_code
        == 404
    )
    for method, suffix in [("POST", "/refresh"), ("GET", "/artifact-url")]:
        assert web.request(method, f"/api/v1/jobs/{hidden.id}{suffix}").status_code == 404
    assert (
        web.post(f"/api/v1/applications/{other.id}/jobs", content=b"not multipart").status_code
        == 404
    )
    assert web.get("/api/v1/admin/users").status_code == 403
    before = len(apple.calls)
    assert web.get(f"/api/v1/jobs/{visible.id}").status_code == 200
    assert len(apple.calls) == before
    response = web.post(
        f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs",
        files={"file": ("new-1.2.3.zip", dmg_zip(b"new"))},
    )
    assert response.status_code == 200
    job_id = response.json()["data"]["id"]
    assert context.repository.get(job_id).submitted_by_user_id
    apple.current = "Accepted"
    assert web.post(f"/api/v1/jobs/{job_id}/refresh").status_code == 200
    download = web.get(f"/api/v1/jobs/{job_id}/artifact-url")
    assert download.status_code == 200
    assert "eo_sign=" in download.json()["data"]["url"]
    # The obsolete web redirect is removed even for an accessible, completed job.
    assert web.get(f"/api/v1/jobs/{job_id}/artifact", follow_redirects=False).status_code == 404
    detail = web.get(f"/api/v1/jobs/{job_id}").json()["data"]
    assert detail["source"] == "用户 · alice" and detail["created_at"].endswith("Z")
    access.set_users(TEST_APPLICATION_ID, [])
    assert web.get(f"/api/v1/jobs/{job_id}/artifact-url").status_code == 404
    assert web.patch(f"/api/v1/jobs/{job_id}", json={"display_name": "Revoked"}).status_code == 404
    assert web.get("/api/v1/jobs").json()["data"]["total"] == 0


def test_job_rename_preserves_files_delivery_and_deduplication(
    web, context, apple, artifacts, tool
):
    service = SubmissionService(context, TEST_APPLICATION_ID)
    original_name = "desktop-v1.2.3.zip"
    content = dmg_zip()
    job_id = service.submit(original_name, BytesIO(content)).id
    path = f"/api/v1/jobs/{job_id}"
    before = context.repository.get(job_id)
    assert web.get(path).json()["data"]["display_name"] == original_name
    assert web.get("/api/v1/jobs").json()["data"]["items"][0]["display_name"] == original_name
    calls = (list(tool.calls), list(apple.calls))
    response = web.patch(path, json={"display_name": "  桌面发布 100%_稳定版  "})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["display_name"] == "桌面发布 100%_稳定版"
    assert response.json()["data"]["filename"] == original_name
    assert (tool.calls, apple.calls) == calls
    stored = context.repository.get(job_id)
    assert stored.display_name == "桌面发布 100%_稳定版"
    for field in ("filename", "original_path", "submitted_path", "original_sha256", "status"):
        assert getattr(stored, field) == getattr(before, field)
    context.repository.create(TEST_APPLICATION_ID, "unrelated100AA-v1.2.3.zip")
    for search in ("桌面发布", "desktop-v1.2.3", "100%_"):
        page = web.get("/api/v1/jobs", params={"filename": search}).json()["data"]
        assert page["total"] == 1 and page["items"][0]["id"] == job_id
    assert web.get("/api/v1/jobs", params={"filename": "100%_other"}).json()["data"]["total"] == 0
    assert service.submit(original_name, BytesIO(content)).id == job_id
    assert context.repository.get(job_id).display_name == "桌面发布 100%_稳定版"
    apple.current = "Accepted"
    assert web.post(path + "/refresh").status_code == 200
    completed = context.repository.get(job_id)
    assert completed.display_name == "桌面发布 100%_稳定版"
    directory = f"{completed.application_id}/{completed.created_at:%Y/%m/%d}/{job_id}"
    assert completed.artifact_path == f"{directory}/artifact/{original_name}"
    assert completed.artifact_s3_key.endswith("/" + original_name)
    assert completed.artifact_s3_key in artifacts.objects
    download = web.get(path + "/artifact-url")
    assert download.status_code == 200
    assert original_name in download.json()["data"]["url"]
    assert web.patch(path, json={"display_name": "正式发布"}).status_code == 200
    assert context.repository.get(job_id).artifact_s3_key == completed.artifact_s3_key
    assert context.repository.get(job_id).artifact_sha256 == completed.artifact_sha256


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "a" * 256},
        {"display_name": "bad\x00name"},
        {"display_name": "bad\nname"},
        {"display_name": None},
        {"display_name": "New name", "filename": "changed.zip"},
    ],
)
def test_invalid_rename_cannot_change_the_job(web, context, body):
    job = context.repository.create(TEST_APPLICATION_ID, "original-v1.2.3.zip")
    assert web.patch(f"/api/v1/jobs/{job.id}", json=body).status_code == 422
    stored = context.repository.get(job.id)
    assert stored.display_name is None and stored.filename == job.filename


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"X-CSRF-Token": "wrong"}])
def test_rename_requires_csrf(web, context, headers):
    job = context.repository.create(TEST_APPLICATION_ID, "original-v1.2.3.zip")
    response = web.patch(
        f"/api/v1/jobs/{job.id}", json={"display_name": "New name"}, headers=headers
    )
    assert response.status_code == 403
    assert context.repository.get(job.id).display_name is None


def test_rename_requires_a_web_session(web, context):
    job = context.repository.create(TEST_APPLICATION_ID, "original-v1.2.3.zip")
    web.cookies.clear()
    response = web.patch(
        f"/api/v1/jobs/{job.id}",
        json={"display_name": "New name"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 401
    assert context.repository.get(job.id).display_name is None


def test_rename_does_not_queue_behind_a_running_job(web, context):
    job = context.repository.create(TEST_APPLICATION_ID, "active-v1.2.3.zip")
    with ThreadPoolExecutor(max_workers=1) as pool, context.locks.hold(job.id):
        response = pool.submit(
            web.patch, f"/api/v1/jobs/{job.id}", json={"display_name": "New name"}
        ).result(timeout=2)
    assert response.status_code == 409
    assert context.repository.get(job.id).display_name is None
    assert web.patch(f"/api/v1/jobs/{job.id}", json={"display_name": "New name"}).status_code == 200


def test_rename_during_creation_is_busy_then_succeeds_after_submission(web, context, monkeypatch):
    create = context.repository.create

    def create_and_check_busy(*args, **kwargs):
        job = create(*args, **kwargs)
        response = web.patch(f"/api/v1/jobs/{job.id}", json={"display_name": "Early rename"})
        assert response.status_code == 409
        assert context.repository.get(job.id).display_name is None
        return job

    monkeypatch.setattr(context.repository, "create", create_and_check_busy)
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "early-v1.2.3.zip", BytesIO(dmg_zip())
    )
    response = web.patch(f"/api/v1/jobs/{job.id}", json={"display_name": "Early rename"})
    assert response.status_code == 200
    assert context.repository.get(job.id).display_name == "Early rename"


def test_keys_and_deduplication_are_scoped_to_applications(web, context):
    access = AccessRepository(context.repository.sessions)
    second = access.create_application("second", "Second")
    _, token = access.create_key(second.id, "CI")
    first = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "same-1.2.3.zip", BytesIO(dmg_zip())
    )
    auth = {"Authorization": f"Bearer {token}"}
    for method, suffix in [("GET", ""), ("GET", "/artifact")]:
        response = web.request(method, f"/openapi/v1/jobs/{first.id}{suffix}", headers=auth)
        assert response.status_code == 404
        assert first.id not in response.text
    result = web.post(
        "/openapi/v1/jobs", headers=auth, files={"file": ("same-1.2.3.zip", dmg_zip())}
    )
    second_id = result.json()["data"]["id"]
    assert second_id != first.id
    assert context.repository.get(second_id).application_id == second.id
    assert (
        web.post(
            "/openapi/v1/jobs", headers=auth, files={"file": ("same-1.2.3.zip", dmg_zip())}
        ).json()["data"]["id"]
        == second_id
    )


def test_disabled_application_blocks_signing_keys_and_regular_users(web, context, settings):
    access = AccessRepository(context.repository.sessions)
    access.set_users(TEST_APPLICATION_ID, ["alice"])
    access.update_application(TEST_APPLICATION_ID, None, False)
    response = web.post(f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs", content=b"invalid")
    assert response.status_code == 404
    assert web.get("/api/v1/applications").json()["data"][0]["enabled"] is False
    assert (
        web.get(
            "/openapi/v1/openapi.json", headers={"Authorization": "Bearer test-token"}
        ).status_code
        == 401
    )
    login_as(web, context, settings, "alice")
    assert web.get("/api/v1/applications").json()["data"] == []


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"X-CSRF-Token": "wrong"}])
def test_csrf_is_checked_before_multipart(web, headers, tool):
    response = web.post(
        f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs", headers=headers, content=b"broken"
    )
    assert response.status_code == 403 and tool.calls == []


def test_session_logout_expiration_and_allowlist_removal(web, context, settings):
    token = login_as(web, context, settings, "alice")
    assert web.post("/api/v1/auth/logout").status_code == 200
    web.cookies.set(settings.cookie_name, token)
    assert web.get("/api/v1/me").status_code == 401
    token = login_as(web, context, settings, "alice")
    with context.repository.sessions.begin() as session:
        stored = session.get(WebSession, digest(token))
        stored.expires_at = utcnow() - timedelta(seconds=1)
    assert web.get("/api/v1/me").status_code == 401
    login_as(web, context, settings, "alice")
    settings.oidc_allowed_usernames = []
    assert web.get("/api/v1/me").status_code == 401
    login_as(web, context, settings, "admin")
    assert web.get("/api/v1/me").json()["data"]["is_admin"]


def test_cors_preflight_and_unauthenticated_responses(web, settings):
    response = web.options(
        "/api/v1/applications",
        headers={
            "Origin": settings.frontend_url,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.frontend_url
    assert response.headers["access-control-allow-credentials"] == "true"
    web.cookies.clear()
    response = web.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == settings.frontend_url
    response = web.get("/api/v1/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_identity_binding_cannot_be_taken_over(context, settings):
    access = AccessRepository(context.repository.sessions)
    access.set_users(TEST_APPLICATION_ID, ["alice"])
    auth = AuthRepository(context.repository.sessions, settings)
    user = auth.bind_user("alice", settings.oidc_issuer_url, "original")
    assert auth.bind_user("alice", settings.oidc_issuer_url, "original").id == user.id
    with pytest.raises(ApiError):
        auth.bind_user("alice", settings.oidc_issuer_url, "attacker")
    with pytest.raises(ApiError):
        auth.bind_user("bob", settings.oidc_issuer_url, "original")
    with pytest.raises(ApiError):
        auth.bind_user("unknown", settings.oidc_issuer_url, "unknown")


def test_failed_job_detail_is_readable_without_refresh(web, context, tool, apple):
    tool.fail_sign = True
    result = web.post(
        f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs",
        files={"file": ("fail-1.2.3.zip", dmg_zip())},
    )
    assert result.status_code == 500
    response = web.get("/api/v1/jobs/" + result.json()["data"]["id"])
    assert response.status_code == 200 and response.json()["data"]["status"] == "failed"
    response = web.patch(
        "/api/v1/jobs/" + result.json()["data"]["id"], json={"display_name": "Failed release"}
    )
    assert response.status_code == 200 and response.json()["data"]["status"] == "failed"
    assert response.json()["data"]["display_name"] == "Failed release"
    assert apple.calls == []


def test_refresh_does_not_queue_behind_a_running_job(web, context, apple):
    job = SubmissionService(context, TEST_APPLICATION_ID).submit(
        "active-1.2.3.zip", BytesIO(dmg_zip())
    )
    with ThreadPoolExecutor(max_workers=1) as pool, context.locks.hold(job.id):
        response = pool.submit(web.post, f"/api/v1/jobs/{job.id}/refresh").result(timeout=2)
    assert response.status_code == 409
    assert response.json()["data"]["id"] == job.id
    assert apple.calls == []
    assert web.post(f"/api/v1/jobs/{job.id}/refresh").status_code == 200
    assert len(apple.calls) == 1


def test_failed_upload_stays_failed_in_refresh_and_history(web, tool, apple):
    tool.fail_submit = True
    result = web.post(
        f"/api/v1/applications/{TEST_APPLICATION_ID}/jobs",
        files={"file": ("upload-timeout-1.2.3.zip", dmg_zip())},
    )
    assert result.status_code == 500
    job_id = result.json()["data"]["id"]
    assert apple.current == "In Progress"
    assert web.post(f"/api/v1/jobs/{job_id}/refresh").status_code == 500
    detail = web.get(f"/api/v1/jobs/{job_id}").json()["data"]
    history = web.get("/api/v1/jobs?status=failed").json()["data"]
    assert detail["status"] == "failed" and "S3 transfer failed" in detail["error_message"]
    assert history["total"] == 1 and history["items"][0]["status"] == "failed"


def test_key_auth_and_cookie_auth_cannot_substitute_for_each_other(web):
    assert web.get("/openapi/v1/openapi.json").status_code == 401
    web.cookies.clear()
    assert (
        web.get("/api/v1/admin/users", headers={"Authorization": "Bearer test-token"}).status_code
        == 401
    )


def test_schema_checks_all_new_tables(context):
    engine = context.repository.sessions.kw["bind"]
    WebSession.__table__.drop(engine)
    with pytest.raises(RuntimeError, match=r"0001_init.sql"):
        context.repository.check_schema()
