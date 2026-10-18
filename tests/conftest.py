from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apple_remote_sign.app import create_app
from apple_remote_sign.archives import SigningArchive
from apple_remote_sign.config import Settings
from apple_remote_sign.integrations.cdn import CdnSigner
from apple_remote_sign.locks import JobLocks
from apple_remote_sign.persistence.access_models import ApiKey, Application
from apple_remote_sign.persistence.access_repository import digest
from apple_remote_sign.persistence.models import Base
from apple_remote_sign.persistence.repository import JobRepository
from apple_remote_sign.redaction import Redactor
from apple_remote_sign.services.context import ServiceContext
from apple_remote_sign.storage import FileStorage
from tests.fake_redis import FakeRedis
from tests.fakes import TEST_APPLICATION_ID, FakeApple, FakeArtifacts, FakeTool


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="mysql+pymysql://test:test@localhost/test",
        redis_url="redis://localhost:6379/3",
        oidc_issuer_url="https://identity.example.com",
        oidc_client_id="test-client",
        oidc_client_secret="test-client-secret",
        oidc_redirect_uri="http://testserver/api/v1/auth/callback",
        oidc_admin_usernames=["admin"],
        oidc_allowed_usernames=["alice", "bob"],
        frontend_url="http://localhost:5173",
        cookie_secure=False,
        apple_api_key_file=tmp_path / "mounted keys" / "custom-key.json",
        data_dir=tmp_path / "data",
        rcodesign_work_dir=tmp_path,
        s3_bucket="test-artifacts",
        cdn_base_url="https://cdn.example.com",
        cdn_sign_key="test-cdn-secret",
    )


@pytest.fixture
def tool() -> FakeTool:
    return FakeTool()


@pytest.fixture
def apple() -> FakeApple:
    return FakeApple()


@pytest.fixture
def artifacts(settings: Settings) -> FakeArtifacts:
    return FakeArtifacts(settings.s3_prefix)


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def context(
    settings: Settings,
    tmp_path: Path,
    tool: FakeTool,
    apple: FakeApple,
    artifacts: FakeArtifacts,
    redis: FakeRedis,
) -> Iterator[ServiceContext]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    # Test-only database creation. Production startup only reads schema / updates task records.
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(Application(id=TEST_APPLICATION_ID, identifier="test.app", name="Test app"))
        session.flush()
        session.add(
            ApiKey(
                application_id=TEST_APPLICATION_ID,
                name="Test key",
                prefix="test",
                token_hash=digest("test-token"),
            )
        )
    yield ServiceContext(
        JobRepository(sessions),
        FileStorage(settings.data_dir, settings.max_upload_bytes),
        tool,
        apple,
        JobLocks(redis),
        Redactor(["test-token", *settings.distribution_secrets]),
        artifacts,
        CdnSigner(settings),
        SigningArchive(settings.max_extracted_bytes, settings.max_archive_entries),
    )
    engine.dispose()


@pytest.fixture
def client(settings: Settings, context: ServiceContext) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings, context), raise_server_exceptions=False, follow_redirects=False
    ) as client:
        client.headers["Authorization"] = "Bearer test-token"
        yield client
