from contextlib import closing
from email.message import Message
from email.utils import collapse_rfc2231_value
from unittest.mock import Mock

import pytest
from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import EndpointConnectionError
from botocore.stub import ANY, Stubber
from pydantic import SecretStr

from apple_remote_sign.config import Settings
from apple_remote_sign.domain import OperationError
from apple_remote_sign.integrations import s3
from apple_remote_sign.integrations.s3 import S3Artifacts, artifact_key, build_s3_client


@pytest.fixture
def s3_client(settings):
    settings.aws_access_key_id = SecretStr("test-access-id")
    settings.aws_secret_access_key = SecretStr("test-access-secret")
    with closing(build_s3_client(settings)) as client:
        yield client


def test_client_configures_bounded_retries_timeouts_and_sigv4(settings, s3_client):
    config = s3_client.meta.config
    assert config.signature_version == "s3v4"
    assert config.connect_timeout == 10
    assert config.read_timeout == 60
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert config.region_name == "us-east-1"


def test_compatible_endpoint_and_temporary_credentials(settings):
    settings.s3_endpoint_url = "http://127.0.0.1:9000"
    settings.s3_addressing_style = "path"
    settings.s3_region = "custom-region"
    settings.aws_access_key_id = SecretStr("test-access-id")
    settings.aws_secret_access_key = SecretStr("test-access-secret")
    settings.aws_session_token = SecretStr("test-session-token")
    with closing(build_s3_client(settings)) as client:
        assert client.meta.endpoint_url == "http://127.0.0.1:9000"
        assert client.meta.config.s3["addressing_style"] == "path"
        assert client.meta.region_name == "custom-region"
        assert client._request_signer._credentials.token == "test-session-token"


def test_dotenv_credentials_are_passed_to_sdk(settings, tmp_path, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / "distribution.env"
    env.write_text(
        "AWS_ACCESS_KEY_ID=dotenv-access\nAWS_SECRET_ACCESS_KEY=dotenv-secret\n"
        "AWS_SESSION_TOKEN=dotenv-token\n"
    )
    values = settings.model_dump(
        exclude={"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}
    )
    with closing(build_s3_client(Settings(_env_file=env, **values))) as client:
        credentials = client._request_signer._credentials
        assert credentials.access_key == "dotenv-access"
        assert credentials.secret_key == "dotenv-secret"
        assert credentials.token == "dotenv-token"


def test_unconfigured_credentials_use_sdk_provider_chain(settings, tmp_path, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    credentials = tmp_path / "credentials"
    credentials.write_text(
        "[artifact-test]\naws_access_key_id = profile-key\naws_secret_access_key = profile-secret\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_PROFILE", "artifact-test")
    with closing(build_s3_client(settings)) as client:
        assert client._request_signer._credentials.access_key == "profile-key"
        assert client._request_signer._credentials.method == "shared-credentials-file"


@pytest.mark.parametrize("filename", ["sample.zip", '应用 1.0%?#".zip'])
def test_upload_preserves_download_name_and_hash_metadata(tmp_path, filename):
    client = Mock()
    path = tmp_path / filename
    path.write_bytes(b"final artifact")
    store = S3Artifacts(client, "artifacts", "custom/")
    key = store.key_for("app/2026/01/02/job", filename)
    store.upload(path, key, filename, "sha256")
    args, kwargs = client.upload_file.call_args
    assert args == (str(path), "artifacts", "custom/app/2026/01/02/job/" + filename)
    assert kwargs["ExtraArgs"]["ContentType"] == "application/zip"
    assert kwargs["ExtraArgs"]["Metadata"] == {"sha256": "sha256"}
    assert "ACL" not in kwargs["ExtraArgs"]
    assert kwargs["Config"].max_concurrency == 4
    headers = Message()
    headers["Content-Disposition"] = kwargs["ExtraArgs"]["ContentDisposition"]
    assert headers.get_content_disposition() == "attachment"
    assert collapse_rfc2231_value(headers.get_param("filename", header="Content-Disposition")) == (
        filename
    )


def test_managed_multipart_upload_finishes_before_return(s3_client, tmp_path, monkeypatch):
    path = tmp_path / "artifact.zip"
    path.write_bytes(b"z" * (9 * 1024**2))
    # Serialize parts so Stubber can assert their exact order without making network requests.
    monkeypatch.setattr(s3, "TransferConfig", lambda **_kwargs: TransferConfig(max_concurrency=1))
    params = {
        "Bucket": "artifacts",
        "Key": "release.zip",
        "ContentType": "application/zip",
        "ContentDisposition": "attachment; filename*=UTF-8''release.zip",
        "Metadata": {"sha256": "hash"},
    }
    with Stubber(s3_client) as stub:
        stub.add_response("create_multipart_upload", {"UploadId": "upload"}, params)
        for number in (1, 2):
            stub.add_response(
                "upload_part",
                {"ETag": f"part-{number}"},
                {
                    "Bucket": "artifacts",
                    "Key": "release.zip",
                    "UploadId": "upload",
                    "PartNumber": number,
                    "Body": ANY,
                },
            )
        stub.add_response(
            "complete_multipart_upload",
            {"ETag": "completed"},
            {
                "Bucket": "artifacts",
                "Key": "release.zip",
                "UploadId": "upload",
                "MultipartUpload": {
                    "Parts": [
                        {"ETag": "part-1", "PartNumber": 1},
                        {"ETag": "part-2", "PartNumber": 2},
                    ]
                },
            },
        )
        S3Artifacts(s3_client, "artifacts", "").upload(path, "release.zip", "release.zip", "hash")
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "error",
    [
        S3UploadFailedError("https://s3.example.com?secret=never-log-this"),
        EndpointConnectionError(endpoint_url="https://s3.example.com?secret=never-log-this"),
    ],
)
def test_sdk_errors_are_operation_errors_without_credentials(tmp_path, error):
    client = Mock()
    client.upload_file.side_effect = error
    with pytest.raises(OperationError, match="S3 artifact upload failed") as raised:
        S3Artifacts(client, "bucket", "").upload(tmp_path / "file.zip", "file.zip", "file.zip", "h")
    assert "never-log-this" not in str(raised.value)


def test_existing_artifact_is_checked_with_its_original_key(s3_client):
    key = 'custom/app/2026/01/02/job/应用 1.0%?#".zip'
    with Stubber(s3_client) as stub:
        stub.add_response("head_object", {}, {"Bucket": "artifacts", "Key": key})
        assert S3Artifacts(s3_client, "artifacts", "changed-prefix/").exists(key)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_missing_artifact_does_not_exist(s3_client, code):
    with Stubber(s3_client) as stub:
        stub.add_client_error(
            "head_object",
            service_error_code=code,
            http_status_code=404,
            expected_params={"Bucket": "artifacts", "Key": "release.zip"},
        )
        assert not S3Artifacts(s3_client, "artifacts", "").exists("release.zip")
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("code", "status"),
    [("403", 403), ("AccessDenied", 403), ("InternalError", 500), ("NoSuchBucket", 404)],
)
def test_lookup_failure_is_not_reported_as_a_missing_artifact(s3_client, code, status):
    with Stubber(s3_client) as stub:
        stub.add_client_error(
            "head_object",
            service_error_code=code,
            service_message="https://s3.example.com?secret=never-log-this",
            http_status_code=status,
            expected_params={"Bucket": "artifacts", "Key": "release.zip"},
        )
        with pytest.raises(OperationError, match="S3 artifact lookup failed") as raised:
            S3Artifacts(s3_client, "artifacts", "").exists("release.zip")
        assert "never-log-this" not in str(raised.value)
        stub.assert_no_pending_responses()


def test_lookup_connection_failure_is_an_operation_error_without_credentials():
    client = Mock()
    client.head_object.side_effect = EndpointConnectionError(
        endpoint_url="https://s3.example.com?secret=never-log-this"
    )
    with pytest.raises(OperationError, match="S3 artifact lookup failed") as raised:
        S3Artifacts(client, "artifacts", "").exists("release.zip")
    assert "never-log-this" not in str(raised.value)


def test_s3_key_limit_counts_utf8_bytes():
    with pytest.raises(OperationError, match="1024 UTF-8 bytes"):
        artifact_key("测" * 340 + "/", "app/2026/01/02/job", "file.zip")
