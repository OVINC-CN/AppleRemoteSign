import hashlib
import logging
import secrets
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from apple_remote_sign.access import Principal
from apple_remote_sign.domain import ApiError
from apple_remote_sign.persistence.access_models import ApiKey, Application, ApplicationGrant, User
from apple_remote_sign.persistence.models import Job, utcnow

logger = logging.getLogger(__name__)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccessRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def application_query(principal: Principal) -> Select[tuple[Application]]:
        query = select(Application)
        if principal.is_admin:
            return query
        query = query.where(Application.enabled.is_(True))
        if principal.key_id:
            return query.where(Application.id == principal.application_id)
        return query.join(ApplicationGrant).where(ApplicationGrant.user_id == principal.user_id)

    def applications(self, principal: Principal) -> Sequence[Application]:
        with self.sessions() as session:
            return session.scalars(
                self.application_query(principal).order_by(Application.created_at.desc())
            ).all()

    def require_application(
        self, application_id: str, principal: Principal, *, signing: bool = False
    ) -> Application:
        with self.sessions() as session:
            app = session.scalar(
                self.application_query(principal).where(Application.id == application_id)
            )
            if app is None or (signing and not app.enabled):
                raise ApiError(404, "Application not found or unavailable.")
            return app

    def require_job(self, job_id: str, principal: Principal) -> Job:
        with self.sessions() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ApiError(404, "Job not found.")
            self.require_application(job.application_id, principal)
            return job

    def authenticate_key(self, token: str) -> Principal:
        with self.sessions.begin() as session:
            key = session.scalar(select(ApiKey).where(ApiKey.token_hash == digest(token)))
            app = session.get(Application, key.application_id) if key else None
            if key is None or key.revoked_at or app is None or not app.enabled:
                raise ApiError(401, "Invalid or missing Bearer token.")
            key.last_used_at = utcnow()
            return Principal(application_id=app.id, key_id=key.id)

    def create_application(self, identifier: str, name: str) -> Application:
        app = Application(identifier=identifier, name=name)
        try:
            with self.sessions.begin() as session:
                session.add(app)
        except IntegrityError:
            raise ApiError(409, "Identifier already exists.") from None
        logger.info("application.created\napplication_id: %s", app.id)
        return app

    def update_application(
        self, app_id: str, name: str | None, enabled: bool | None
    ) -> Application:
        with self.sessions.begin() as session:
            app = session.get(Application, app_id)
            if app is None:
                raise ApiError(404, "Application not found.")
            if name is not None:
                app.name = name
            if enabled is not None:
                app.enabled = enabled
        logger.info("application.updated\napplication_id: %s\nenabled: %s", app.id, app.enabled)
        return app

    def usernames(self, app_id: str) -> Sequence[str]:
        with self.sessions() as session:
            return session.scalars(
                select(User.username)
                .join(ApplicationGrant)
                .where(ApplicationGrant.application_id == app_id)
                .order_by(User.username)
            ).all()

    def set_users(self, app_id: str, usernames: list[str]) -> None:
        with self.sessions.begin() as session:
            # Serialize replacements so concurrent administrators cannot merge grants.
            session.execute(select(Application).where(Application.id == app_id).with_for_update())
            session.execute(
                delete(ApplicationGrant).where(ApplicationGrant.application_id == app_id)
            )
            for username in sorted(set(usernames)):
                user = session.scalar(select(User).where(User.username == username))
                if user is None:
                    user = User(username=username)
                    session.add(user)
                    session.flush()
                session.add(ApplicationGrant(application_id=app_id, user_id=user.id))
        logger.info(
            "application.grants_replaced\napplication_id: %s\ncount: %d",
            app_id,
            len(set(usernames)),
        )

    def keys(self, app_id: str) -> Sequence[ApiKey]:
        with self.sessions() as session:
            return session.scalars(
                select(ApiKey)
                .where(ApiKey.application_id == app_id)
                .order_by(ApiKey.created_at.desc())
            ).all()

    def create_key(self, app_id: str, name: str) -> tuple[ApiKey, str]:
        token = "ars_" + secrets.token_urlsafe(32)
        key = ApiKey(application_id=app_id, name=name, prefix=token[:12], token_hash=digest(token))
        with self.sessions.begin() as session:
            session.add(key)
        logger.info("key.created\napplication_id: %s\nkey_id: %s", app_id, key.id)
        return key, token

    def revoke_key(self, app_id: str, key_id: str) -> None:
        with self.sessions.begin() as session:
            key = session.get(ApiKey, key_id)
            if key is None or key.application_id != app_id:
                raise ApiError(404, "Key not found.")
            key.revoked_at = key.revoked_at or utcnow()
        logger.info("key.revoked\napplication_id: %s\nkey_id: %s", app_id, key_id)
