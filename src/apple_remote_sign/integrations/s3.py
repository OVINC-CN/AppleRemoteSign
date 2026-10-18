from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

import boto3
from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from apple_remote_sign.config import Settings
from apple_remote_sign.domain import OperationError

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


class ArtifactStore(Protocol):
    def key_for(self, directory: str, filename: str) -> str: ...

    def upload(self, path: Path, key: str, filename: str, sha256: str) -> None: ...

    def exists(self, key: str) -> bool: ...


def artifact_key(prefix: str, directory: str, filename: str) -> str:
    key = f"{prefix}{directory}/{filename}"
    if len(key.encode("utf-8")) > 1024:
        raise OperationError("S3 artifact key exceeds 1024 UTF-8 bytes; shorten S3_PREFIX.")
    return key


def content_disposition(filename: str) -> str:
    return "attachment; filename*=UTF-8''" + quote(filename, safe="")


def build_s3_client(settings: Settings) -> S3Client:
    session = boto3.Session(
        aws_access_key_id=(
            settings.aws_access_key_id.get_secret_value() if settings.aws_access_key_id else None
        ),
        aws_secret_access_key=(
            settings.aws_secret_access_key.get_secret_value()
            if settings.aws_secret_access_key
            else None
        ),
        aws_session_token=(
            settings.aws_session_token.get_secret_value() if settings.aws_session_token else None
        ),
    )
    return session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        config=Config(
            signature_version="s3v4",
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            retries={"mode": "standard", "total_max_attempts": 3},
            s3={"addressing_style": settings.s3_addressing_style},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


class S3Artifacts:
    def __init__(self, client: S3Client, bucket: str, prefix: str) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    def key_for(self, directory: str, filename: str) -> str:
        return artifact_key(self.prefix, directory, filename)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return False
            # SDK exceptions may contain signed URLs and credentials. Only expose their type.
            raise OperationError(f"S3 artifact lookup failed ({type(exc).__name__}).") from None
        return True

    def upload(self, path: Path, key: str, filename: str, sha256: str) -> None:
        try:
            self.client.upload_file(
                str(path),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": "application/zip",
                    "ContentDisposition": content_disposition(filename),
                    "Metadata": {"sha256": sha256},
                },
                Config=TransferConfig(max_concurrency=4),
            )
        except (BotoCoreError, ClientError, S3UploadFailedError) as exc:
            # SDK exceptions may contain signed URLs and credentials. Only expose their type.
            raise OperationError(f"S3 artifact upload failed ({type(exc).__name__}).") from None
