import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from apple_remote_sign.archives import SigningArchive
from apple_remote_sign.config import Settings
from apple_remote_sign.integrations.apple import AppleNotaryClient
from apple_remote_sign.integrations.cdn import CdnSigner
from apple_remote_sign.integrations.credentials import AppleCredentials
from apple_remote_sign.integrations.process import CommandRunner
from apple_remote_sign.integrations.rcodesign import Rcodesign
from apple_remote_sign.integrations.redis import build_redis_client
from apple_remote_sign.integrations.s3 import S3Artifacts, build_s3_client
from apple_remote_sign.integrations.wecom import WeComNotifier
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.observability import operation, set_log_secrets
from apple_remote_sign.persistence.database import build_engine
from apple_remote_sign.persistence.repository import JobRepository
from apple_remote_sign.redaction import Redactor
from apple_remote_sign.services.context import ServiceContext
from apple_remote_sign.storage import FileStorage


def validate_files(settings: Settings) -> str:
    files = [
        settings.signing_certificate_file,
        settings.signing_password_file,
        settings.apple_api_key_file,
    ]
    for path in files:
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.R_OK):
            raise RuntimeError(f"Required mounted file is missing, relative or unreadable: {path}")
    if not settings.rcodesign_bin.is_absolute() or not os.access(settings.rcodesign_bin, os.X_OK):
        raise RuntimeError("RCODESIGN_BIN must point to an executable absolute path.")
    password = settings.signing_password_file.read_text().splitlines()
    if not password:
        raise RuntimeError(
            "Signing password file needs a first line (use a newline for an empty password)."
        )
    return password[0]


@contextmanager
def build_context(settings: Settings) -> Iterator[ServiceContext]:
    with operation("startup.credentials"):
        password = validate_files(settings)
        credentials = AppleCredentials.from_json(settings.apple_api_key_file.read_bytes())
    url = make_url(settings.database_url.get_secret_value())
    if url.drivername != "mysql+pymysql":
        raise RuntimeError("DATABASE_URL must use mysql+pymysql.")
    secret_values = [
        settings.database_url.get_secret_value(),
        settings.oidc_client_secret.get_secret_value(),
        password,
        url.password or "",
        credentials.encoded_private_key,
        *settings.redis_secrets,
        *settings.distribution_secrets,
        *settings.wecom_secrets,
    ]
    redactor = Redactor(secret_values)
    set_log_secrets(secret_values)
    with ExitStack() as stack:
        redis = build_redis_client(settings)
        stack.callback(redis.close)
        locks = JobLocks(redis)
        locks.ping()
        engine = build_engine(url, settings)
        stack.callback(engine.dispose)
        sessions = sessionmaker(engine, expire_on_commit=False)
        http = stack.enter_context(httpx.Client(timeout=settings.apple_timeout_seconds))
        apple = AppleNotaryClient(http, credentials)
        runner = CommandRunner(settings.rcodesign_work_dir, redactor)
        s3 = build_s3_client(settings)
        stack.callback(s3.close)
        notifier = None
        if settings.wecom_webhook_url is not None:
            notification_http = stack.enter_context(
                httpx.Client(timeout=settings.wecom_timeout_seconds)
            )
            notifier = WeComNotifier(notification_http, settings.wecom_webhook_url)
        yield ServiceContext(
            repository=JobRepository(sessions),
            storage=FileStorage(settings.data_dir, settings.max_upload_bytes),
            tool=Rcodesign(settings, runner),
            apple=apple,
            locks=locks,
            redactor=redactor,
            artifacts=S3Artifacts(s3, settings.s3_bucket, settings.s3_prefix),
            cdn=CdnSigner(settings),
            archives=SigningArchive(settings.max_extracted_bytes, settings.max_archive_entries),
            notifier=notifier,
        )
