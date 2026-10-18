import hashlib
import stat
from io import BytesIO
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from apple_remote_sign.persistence.models import Job
from tests.archive_helpers import APP, BINARY, RESOURCE, app_entries, dmg_zip, make_zip, read_dmg


@pytest.mark.parametrize(
    "filename",
    ["DEEIX Chat-v1.2.3.app.zip", "../../应用-1.2.3.ZIP", r"C:\uploads\bundle-v1.2.3.zip"],
)
def test_app_zip_full_flow_preserves_original_and_submits_signed_archive(
    client, context, tool, apple, artifacts, filename
):
    original = make_zip()
    response = client.post("/openapi/v1/jobs", files={"file": (filename, original)})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    job_id = data["id"]
    job = context.repository.get(job_id)
    expected_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    assert job.filename == expected_name
    assert job.original_sha256 == hashlib.sha256(original).hexdigest()
    assert job.size_bytes == len(original)
    assert apple.calls == []
    assert [operation for operation, _ in tool.calls] == ["sign", "submit"]
    assert tool.calls[0][1].name == APP
    assert tool.calls[1][1].name == expected_name
    submitted = context.storage.require_file(job.submitted_path).read_bytes()
    with ZipFile(BytesIO(submitted)) as archive:
        assert archive.read(BINARY) == b"original executable|signed"
        assert archive.read(RESOURCE) == b"framework resource"
    assert context.storage.require_file(job.original_path).read_bytes() == original
    assert client.get(f"/openapi/v1/jobs/{job_id}/artifact").status_code == 409
    apple.current = "Accepted"
    assert client.get(f"/openapi/v1/jobs/{job_id}").json()["data"]["status"] == "completed"
    download = client.get(f"/openapi/v1/jobs/{job_id}/artifact")
    assert download.status_code == 302
    job = context.repository.get(job_id)
    with ZipFile(BytesIO(artifacts.objects[job.artifact_s3_key])) as archive:
        assert archive.testzip() is None
        assert archive.read(BINARY) == b"original executable|signed|stapled"
        link = archive.getinfo(f"{APP}/Contents/Frameworks/Example.framework/Resources")
        assert link.external_attr >> 16 == 0o120777
    assert context.storage.require_file(job.submitted_path).read_bytes() == submitted
    assert tool.calls[-1][1].name == APP
    assert not list(context.storage.root.rglob(".app-*"))
    # Repeated queries and uploads do not sign, submit or staple again.
    client.get(f"/openapi/v1/jobs/{job_id}")
    duplicate = client.post("/openapi/v1/jobs", files={"file": (filename, original)})
    assert duplicate.json()["data"]["id"] == job_id
    assert duplicate.json()["data"]["status"] == "completed"
    assert [operation for operation, _ in tool.calls] == ["sign", "submit", "staple"]


def test_app_staple_retry_uses_signed_zip_after_partial_failure(
    client, context, tool, apple, artifacts
):
    response = client.post("/openapi/v1/jobs", files={"file": ("app-v1.2.3.zip", make_zip())})
    job_id = response.json()["data"]["id"]
    job = context.repository.get(job_id)
    signed = context.storage.require_file(job.submitted_path).read_bytes()
    apple.current = "Accepted"
    tool.fail_staple = True
    failed = client.get(f"/openapi/v1/jobs/{job_id}")
    assert failed.status_code == 500
    assert failed.json()["data"]["status"] == "failed"
    assert client.get(f"/openapi/v1/jobs/{job_id}/artifact").status_code == 500
    assert context.storage.require_file(job.submitted_path).read_bytes() == signed
    assert not list(context.storage.root.rglob(".app-*"))
    tool.fail_staple = False
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == 200
    downloaded = client.get(f"/openapi/v1/jobs/{job_id}/artifact")
    assert downloaded.status_code == 302
    job = context.repository.get(job_id)
    with ZipFile(BytesIO(artifacts.objects[job.artifact_s3_key])) as archive:
        assert archive.read(BINARY) == b"original executable|signed|stapled"
    assert [operation for operation, _ in tool.calls] == ["sign", "submit", "staple", "staple"]


@pytest.mark.parametrize(
    "filename, content, message",
    [
        ("DEEIX Chat.app", b"", "Only ZIP uploads are supported"),
        ("DEEIX Chat.APP", b"not a bundle", "Only ZIP uploads are supported"),
        ("image.dmg", b"image", "Only ZIP uploads are supported"),
        ("data.tar.gz", b"gzip", "Only ZIP uploads are supported"),
        ("empty-v1.2.3.zip", b"", "Uploaded file is empty"),
        ("renamed-v1.2.3.zip", b"not a zip", "not a valid ZIP archive"),
        ("broken-v1.2.3.zip", b"PK\x03\x04broken", "ZIP is corrupt"),
        ("valid.zip.exe", make_zip(), "Only ZIP uploads are supported"),
    ],
)
def test_bad_upload_returns_422_without_creating_job_or_invoking_tools(
    client, context, tool, apple, filename, content, message
):
    response = client.post("/openapi/v1/jobs", files={"file": (filename, content)})
    assert response.status_code == 422
    assert response.json()["data"] is None
    assert message in response.json()["error_message"]
    assert tool.calls == []
    assert apple.calls == []
    with context.repository.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    assert not context.storage.root.exists()


def test_zip_signing_failure_keeps_original_and_does_not_submit(client, context, tool):
    tool.fail_sign = True
    original = make_zip()
    response = client.post("/openapi/v1/jobs", files={"file": ("app-v1.2.3.zip", original)})
    assert response.status_code == 500
    job = context.repository.get(response.json()["data"]["id"])
    assert job.status == "failed"
    assert job.submission_id is None
    assert context.storage.require_file(job.original_path).read_bytes() == original
    assert [operation for operation, _ in tool.calls] == ["sign"]
    assert not list(context.storage.root.rglob(".app-*"))


def test_zip_submission_failure_preserves_id_for_later_stapling(
    client, context, tool, apple, artifacts
):
    tool.fail_submit = True
    response = client.post("/openapi/v1/jobs", files={"file": ("app-v1.2.3.zip", make_zip())})
    assert response.status_code == 500
    data = response.json()["data"]
    assert data["submission_id"]
    apple.current = "Accepted"
    assert client.get(f"/openapi/v1/jobs/{data['id']}").status_code == 200
    result = client.get(f"/openapi/v1/jobs/{data['id']}/artifact")
    assert result.status_code == 302
    job = context.repository.get(data["id"])
    with ZipFile(BytesIO(artifacts.objects[job.artifact_s3_key])) as archive:
        assert archive.read(BINARY) == b"original executable|signed|stapled"
    assert [operation for operation, _ in tool.calls] == ["sign", "submit", "staple"]


def test_dmg_zip_preserves_inner_filename_and_uses_content_not_mime_type(
    client, context, tool, apple, artifacts
):
    name = "安装镜像 1.0.DMG"
    response = client.post(
        "/openapi/v1/jobs",
        files={"file": ("release-v1.2.3.ZIP", dmg_zip(filename=name), "text/plain")},
    )
    assert response.status_code == 200
    job_id = response.json()["data"]["id"]
    apple.current = "Accepted"
    assert client.get(f"/openapi/v1/jobs/{job_id}").status_code == 200
    download = client.get(f"/openapi/v1/jobs/{job_id}/artifact")
    assert download.status_code == 302
    job = context.repository.get(job_id)
    assert read_dmg(artifacts.objects[job.artifact_s3_key], name) == b"original|signed|stapled"
    assert [(operation, path.name) for operation, path in tool.calls] == [
        ("sign", name),
        ("submit", "release-v1.2.3.ZIP"),
        ("staple", name),
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [("installer.pkg", b"pkg", stat.S_IFREG | 0o644)],
        [("folder/image.dmg", b"image", stat.S_IFREG | 0o644)],
        [("empty.dmg", b"", stat.S_IFREG | 0o644)],
        [("directory.dmg/", b"", stat.S_IFDIR | 0o755)],
        [("one.dmg", b"1", stat.S_IFREG | 0o644), ("two.dmg", b"2", stat.S_IFREG | 0o644)],
        [*app_entries(), ("image.dmg", b"image", stat.S_IFREG | 0o644)],
    ],
)
def test_unsupported_or_multiple_zip_targets_are_rejected(client, context, tool, entries):
    response = client.post(
        "/openapi/v1/jobs", files={"file": ("input-v1.2.3.zip", make_zip(entries))}
    )
    assert response.status_code == 422
    assert response.json()["data"] is None
    assert "ZIP" in response.json()["error_message"]
    assert tool.calls == []
    assert not context.storage.root.exists()


def test_oversized_zip_is_rejected_before_creating_a_job(client, context, tool):
    context.storage.max_upload_bytes = 4
    response = client.post("/openapi/v1/jobs", files={"file": ("input-v1.2.3.zip", dmg_zip())})
    assert response.status_code == 422
    assert "MAX_UPLOAD_BYTES" in response.json()["error_message"]
    assert response.json()["data"] is None
    assert tool.calls == []
    assert not context.storage.root.exists()
