import os
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, inspect, text
from sqlalchemy.orm import sessionmaker

from apple_remote_sign.domain import ApiError
from apple_remote_sign.persistence.access_models import (
    ApiKey,
    Application,
    ApplicationGrant,
    LoginTransaction,
    User,
    WebSession,
)
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.persistence.auth_repository import AuthRepository
from apple_remote_sign.persistence.models import Base, Job
from apple_remote_sign.persistence.repository import JobRepository
from apple_remote_sign.recovery import RecoveryWorker
from apple_remote_sign.services.query import QueryService
from apple_remote_sign.services.submission import SubmissionService
from tests.archive_helpers import dmg_zip, read_dmg

pytestmark = pytest.mark.mysql


@pytest.fixture
def mysql_engine():
    url = os.getenv("TEST_MYSQL_URL")
    if not url:
        pytest.skip(
            "Set TEST_MYSQL_URL after explicitly importing 0001_init.sql into a test database."
        )
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    assert engine.url.database.endswith("_test"), "Use a disposable database ending in _test"
    with engine.connect() as connection:
        grants = "\n".join(row[0] for row in connection.execute(text("SHOW GRANTS")))
    assert "ALL PRIVILEGES" not in grants.upper()
    assert "CREATE" not in grants.upper()
    yield engine
    engine.dispose()


def test_real_mysql_initial_schema_columns_and_indexes_match_models(mysql_engine):
    inspector = inspect(mysql_engine)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables)
    for table in Base.metadata.sorted_tables:
        columns = inspector.get_columns(table.name)
        assert {column["name"]: column["nullable"] for column in columns} == {
            column.name: column.nullable for column in table.columns
        }
        primary_key = inspector.get_pk_constraint(table.name)
        assert primary_key["constrained_columns"] == [
            column.name for column in table.primary_key.columns
        ]
        indexes = {index["name"]: index for index in inspector.get_indexes(table.name)}
        for index in table.indexes:
            assert indexes[index.name]["column_names"] == [column.name for column in index.columns]
            assert bool(indexes[index.name]["unique"]) == index.unique


def test_real_mysql_manually_initialized_schema_with_dml_only_account(
    context, apple, artifacts, mysql_engine
):
    engine = mysql_engine
    columns = inspect(engine).get_columns("jobs")
    assert [column["name"] for column in columns if "status" in column["name"]] == ["status"]
    context.repository = JobRepository(sessionmaker(engine, expire_on_commit=False))
    context.repository.check_schema()
    RecoveryWorker(context).scan()
    access = AccessRepository(context.repository.sessions)
    app = access.create_application(f"test-{uuid4()}", "MySQL test")
    try:
        service = SubmissionService(context, app.id)
        data = service.submit("mysql-1.2.3.zip", BytesIO(dmg_zip(b"original")))
        assert data.status == "processing"
        reused = service.submit("mysql-1.2.3.zip", BytesIO(dmg_zip(b"original")))
        assert reused.id == data.id and reused.status == "processing"
        apple.current = "Accepted"
        query = QueryService(context, app.id)
        assert query.query(data.id).status == "completed"
        stored = context.repository.get(data.id)
        assert stored.original_sha256 != stored.artifact_sha256
        assert stored.submission_id == data.submission_id and stored.last_checked_at is not None
        assert stored.artifact_s3_key in query.artifact(data.id)
        assert read_dmg(artifacts.objects[stored.artifact_s3_key]) == b"original|signed|stapled"
        assert service.submit("mysql-1.2.3.zip", BytesIO(dmg_zip(b"original"))).id == data.id
        ids = {data.id}
        for filename in (
            "renamed-1.2.3.zip",
            "MySQL-1.2.3.zip",
            "mysql-1.2.3 .zip",
            "mýsql-1.2.3.zip",
        ):
            renamed = service.submit(filename, BytesIO(dmg_zip(b"original")))
            assert renamed.id not in ids
            ids.add(renamed.id)
            assert context.repository.get(renamed.id).filename == filename
            assert service.submit(filename, BytesIO(dmg_zip(b"original"))).id == renamed.id
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Job).where(Job.application_id == app.id))
            connection.execute(delete(Application).where(Application.id == app.id))


def test_real_mysql_authorization_sessions_and_case_sensitive_identity(mysql_engine, settings):
    sessions = sessionmaker(mysql_engine, expire_on_commit=False)
    access = AccessRepository(sessions)
    suffix = str(uuid4())
    username = "alice-" + suffix
    settings.oidc_allowed_usernames = [username, username.upper()]
    auth = AuthRepository(sessions, settings)
    app = access.create_application("app-" + suffix, "App")
    other = access.create_application("APP-" + suffix, "Other")
    state = "state-" + suffix
    user_ids = []
    try:
        access.set_users(app.id, [username])
        user = auth.bind_user(username, settings.oidc_issuer_url, suffix)
        user_ids.append(user.id)
        other_user = auth.bind_user(username.upper(), settings.oidc_issuer_url, "other-" + suffix)
        user_ids.append(other_user.id)
        token = auth.create_session(user.id)
        principal = auth.authenticate_session(token)
        assert [item.id for item in access.applications(principal)] == [app.id]
        key, raw_key = access.create_key(app.id, "CI")
        assert access.authenticate_key(raw_key).application_id == app.id
        access.revoke_key(app.id, key.id)
        with pytest.raises(ApiError):
            access.authenticate_key(raw_key)
        auth.save_login(state, "browser", "{}")
        assert auth.consume_login(state, "browser") == "{}"
        with pytest.raises(ApiError):
            auth.consume_login(state, "browser")
        access.set_users(app.id, [])
        assert access.applications(principal) == []
        auth.logout(principal.session_hash)
        with pytest.raises(ApiError):
            auth.authenticate_session(token)
    finally:
        with mysql_engine.begin() as connection:
            connection.execute(delete(WebSession).where(WebSession.user_id.in_(user_ids)))
            connection.execute(
                delete(ApplicationGrant).where(
                    ApplicationGrant.application_id.in_([app.id, other.id])
                )
            )
            connection.execute(delete(ApiKey).where(ApiKey.application_id.in_([app.id, other.id])))
            connection.execute(delete(Application).where(Application.id.in_([app.id, other.id])))
            connection.execute(delete(User).where(User.username.in_([username, username.upper()])))
            from apple_remote_sign.persistence.access_repository import digest

            connection.execute(
                delete(LoginTransaction).where(LoginTransaction.state_hash == digest(state))
            )
