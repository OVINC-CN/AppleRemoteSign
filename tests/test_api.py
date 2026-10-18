from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest

from tests.archive_helpers import dmg_zip, read_dmg


def upload(client, filename="sample-v1.2.3.zip", content=b"original"):
    return client.post("/openapi/v1/jobs", files={"file": (filename, dmg_zip(content))})


@pytest.mark.parametrize(
    "filename",
    [
        "sample-1.2.3.zip",
        "sample-v1.2.3.zip",
        "sample-v1.2.3-alpha.1.zip",
        "sample-v1.2.3-beta.2.zip",
        "sample-v1.2.3-rc.3.zip",
        "sample-v12.34.567-nightly+build.9.ZIP",
        "应用安装包 1.2.3.zip",
        ".hidden-1.2.3.zip",
        " sample-v1.2.3.zip",
        "sample-1.2.3..zip",
        "sample-1.2.3 .zip",
        "a" * 246 + "1.2.3.zip",
        "测" * 82 + "1.2.3.zip",
    ],
)
def test_full_flow_and_immutable_inputs(client, context, tool, apple, artifacts, filename):
    response = upload(client, filename)
    assert response.status_code == 200
    assert set(response.json()) == {"data", "error_message"}
    data = response.json()["data"]
    assert set(data) == {"id", "status", "submission_id"}
    assert data["status"] == "processing"
    assert data["submission_id"]
    assert apple.calls == []  # Submission must never query Apple's status.
    job_id = data["id"]
    assert client.get(f"/openapi/v1/jobs/{job_id}/artifact").status_code == 409
    assert apple.calls == []  # Download must not trigger status refresh either.
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == "processing"
    assert len(apple.calls) == 1
    apple.current = "Accepted"
    result = client.get(f"/openapi/v1/jobs/{job_id}")
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "completed"
    file = client.get(f"/openapi/v1/jobs/{job_id}/artifact")
    assert file.status_code == 302
    assert file.content == b""
    assert file.headers["cache-control"] == "no-store"
    job = context.repository.get(job_id)
    directory = f"{job.application_id}/{job.created_at:%Y/%m/%d}/{job_id}"
    assert job.filename == filename
    assert job.artifact_s3_key == f"app-dist/{directory}/{filename}"
    assert unquote(urlsplit(file.headers["location"]).path) == "/" + job.artifact_s3_key
    assert read_dmg(artifacts.objects[job.artifact_s3_key]) == b"original|signed|stapled"
    assert artifacts.calls == [job.artifact_s3_key]
    for stage in ("original", "submitted", "artifact"):
        path = getattr(job, f"{stage}_path")
        assert path == f"{directory}/{stage}/{filename}"
        assert list(context.storage.resolve(path).parent.iterdir()) == [
            context.storage.require_file(path)
        ]
    assert all(
        path.name == (filename if operation == "submit" else "sample.dmg")
        for operation, path in tool.calls
    )
    assert context.storage.require_file(job.original_path).read_bytes() == dmg_zip()
    assert (
        read_dmg(context.storage.require_file(job.submitted_path).read_bytes())
        == b"original|signed"
    )
    assert len({job.original_sha256, job.submitted_sha256, job.artifact_sha256}) == 3
    client.get(f"/openapi/v1/jobs/{job_id}")
    assert len(apple.calls) == 3  # Every explicit query refreshes once, including completed jobs.
    assert [name for name, _ in tool.calls].count("staple") == 1


@pytest.mark.parametrize("header", [None, "", "Bearer wrong", "Basic test-token"])
def test_health_does_not_require_authentication(client, header):
    del client.headers["Authorization"]
    headers = {} if header is None else {"Authorization": header}
    response = client.get("/health", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("header", [None, "", "Bearer wrong", "Basic test-token"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/openapi/v1/jobs"),
        ("GET", "/openapi/v1/jobs/00000000-0000-0000-0000-000000000000"),
        ("GET", "/openapi/v1/jobs/00000000-0000-0000-0000-000000000000/artifact"),
        ("GET", "/openapi/v1/openapi.json"),
    ],
)
def test_authentication(client, header, method, path):
    del client.headers["Authorization"]
    headers = {} if header is None else {"Authorization": header}
    response = client.request(method, path, headers=headers, content=b"not multipart")
    assert response.status_code == 401
    assert response.json()["data"] is None
    assert response.headers["www-authenticate"] == "Bearer"


def test_validation_missing_job_and_schema(client):
    for response, code in [
        (client.post("/openapi/v1/jobs"), 422),
        (client.get("/openapi/v1/jobs/not-a-uuid"), 422),
        (client.get(f"/openapi/v1/jobs/{uuid4()}"), 404),
        (client.get("/docs"), 404),
        (client.get("/openapi/v1/jobs/"), 404),
    ]:
        assert response.status_code == code
        assert set(response.json()) == {"data", "error_message"}
        assert response.json()["error_message"]
    schema = client.get("/openapi/v1/openapi.json").json()
    assert schema["openapi"].startswith("3.")
    assert all(path.startswith("/openapi/v1/") for path in schema["paths"])
    assert schema["components"]["schemas"]["JobStatus"]["enum"] == [
        "preparing",
        "processing",
        "completed",
        "failed",
    ]
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    errors = schema["paths"]["/openapi/v1/jobs"]["post"]["responses"]["422"]
    assert errors["content"]["application/json"]["schema"]["$ref"].endswith("/Envelope")
    download = schema["paths"]["/openapi/v1/jobs/{job_id}/artifact"]["get"]["responses"]
    assert "200" not in download
    assert download["302"]["headers"]["Location"]["schema"]["format"] == "uri"


def test_signing_failure_retains_task(client, context, tool, apple):
    tool.fail_sign = True
    response = upload(client)
    assert response.status_code == 500
    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["submission_id"] is None
    assert "invalid signature input" in response.json()["error_message"]
    assert (
        context.storage.require_file(context.repository.get(data["id"]).original_path).read_bytes()
        == dmg_zip()
    )
    assert client.get(f"/openapi/v1/jobs/{data['id']}").status_code == 500
    assert [name for name, _ in tool.calls] == ["sign"]
    assert apple.calls == []


def test_submission_failure_preserves_id_and_does_not_resubmit(client, context, tool, apple):
    tool.fail_submit = True
    response = upload(client)
    assert response.status_code == 500
    job = response.json()["data"]
    assert context.repository.get(job["id"]).submission_id == job["submission_id"]
    apple.current = "Accepted"
    assert client.get(f"/openapi/v1/jobs/{job['id']}").json()["data"]["status"] == "completed"
    assert [name for name, _ in tool.calls].count("submit") == 1


@pytest.mark.parametrize("failed_stage", ["submit", "staple"])
def test_in_progress_does_not_erase_a_local_failure(client, context, tool, apple, failed_stage):
    tool.fail_submit = failed_stage == "submit"
    data = upload(client).json()["data"]
    if failed_stage == "staple":
        apple.current = "Accepted"
        tool.fail_staple = True
        assert client.get(f"/openapi/v1/jobs/{data['id']}").status_code == 500
    failed = context.repository.get(data["id"])
    assert failed.status == "failed" and failed.error_message
    apple.current = "In Progress"
    response = client.get(f"/openapi/v1/jobs/{data['id']}")
    assert response.status_code == 500
    assert response.json()["data"]["status"] == "failed"
    saved = context.repository.get(data["id"])
    assert saved.error_message == failed.error_message
    assert saved.last_checked_at is not None
    assert saved.diagnostics == failed.diagnostics
    assert [name for name, _ in tool.calls].count("submit") == 1


def test_in_progress_does_not_regress_a_completed_artifact(client, apple):
    data = upload(client).json()["data"]
    apple.current = "Accepted"
    assert client.get(f"/openapi/v1/jobs/{data['id']}").json()["data"]["status"] == "completed"
    apple.current = "In Progress"
    assert client.get(f"/openapi/v1/jobs/{data['id']}").json()["data"]["status"] == "completed"
    assert client.get(f"/openapi/v1/jobs/{data['id']}/artifact").status_code == 302


@pytest.mark.parametrize("status", ["Invalid", "Rejected"])
def test_rejected_notarization_returns_500_and_caches_log(client, apple, status):
    data = upload(client).json()["data"]
    apple.current = status
    response = client.get(f"/openapi/v1/jobs/{data['id']}")
    assert response.status_code == 500
    assert response.json()["data"]["status"] == "failed"
    assert "hardened runtime" in response.json()["error_message"]
    client.get(f"/openapi/v1/jobs/{data['id']}")
    assert len(apple.calls) == 2
    assert len(apple.logs) == 1


def test_apple_error_preserves_state_and_last_success_time(client, context, apple):
    data = upload(client).json()["data"]
    before = context.repository.get(data["id"])
    apple.error = "Connection failed: Bearer secret-token"
    response = client.get(f"/openapi/v1/jobs/{data['id']}")
    assert response.status_code == 502
    assert response.json()["data"]["status"] == "processing"
    assert "secret-token" not in response.text
    job = context.repository.get(data["id"])
    assert job.status == before.status
    assert job.last_checked_at == before.last_checked_at


def test_staple_retry_starts_from_immutable_submission(client, context, tool, apple, artifacts):
    data = upload(client).json()["data"]
    apple.current = "Accepted"
    tool.fail_staple = True
    assert client.get(f"/openapi/v1/jobs/{data['id']}").status_code == 500
    assert client.get(f"/openapi/v1/jobs/{data['id']}/artifact").status_code == 500
    tool.fail_staple = False
    assert client.get(f"/openapi/v1/jobs/{data['id']}").status_code == 200
    artifact = client.get(f"/openapi/v1/jobs/{data['id']}/artifact")
    assert artifact.status_code == 302
    job = context.repository.get(data["id"])
    assert read_dmg(artifacts.objects[job.artifact_s3_key]) == b"original|signed|stapled"
    assert (
        read_dmg(context.storage.require_file(job.submitted_path).read_bytes())
        == b"original|signed"
    )
    assert [name for name, _ in tool.calls].count("submit") == 1


@pytest.mark.parametrize(
    "filename", ["../../something-v1.2.3.zip", r"C:\uploads\something-v1.2.3.zip"]
)
def test_filename_is_not_a_path_and_zip_original_is_preserved(client, context, tool, filename):
    data = upload(client, filename, b"installer").json()["data"]
    job = context.repository.get(data["id"])
    assert job.filename == "something-v1.2.3.zip"
    assert context.storage.require_file(job.original_path).read_bytes() == dmg_zip(b"installer")
    assert tool.calls[0][1].name == "sample.dmg"
    assert tool.calls[1][1].name == "something-v1.2.3.zip"


@pytest.mark.parametrize("filename", [".", "..", "a" * 252 + ".zip", "测" * 84 + ".zip"])
def test_invalid_filename_is_rejected_instead_of_renamed(client, context, tool, filename):
    response = upload(client, filename)
    assert response.status_code == 422
    assert response.json()["data"] is None
    assert "Filename" in response.json()["error_message"]
    assert tool.calls == []
    assert not context.storage.root.exists()


@pytest.mark.parametrize("value", ["bytes=0-1", "bytes=9999-", "malformed"])
def test_download_redirects_to_cdn_even_with_range(client, apple, value):
    job = upload(client).json()["data"]
    apple.current = "Accepted"
    client.get(f"/openapi/v1/jobs/{job['id']}")
    response = client.get(f"/openapi/v1/jobs/{job['id']}/artifact", headers={"Range": value})
    assert response.status_code == 302
    assert response.content == b""
    assert "accept-ranges" not in response.headers
