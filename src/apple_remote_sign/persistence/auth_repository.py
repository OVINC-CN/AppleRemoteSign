import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apple_remote_sign.access import Principal
from apple_remote_sign.config import Settings
from apple_remote_sign.domain import ApiError
from apple_remote_sign.persistence.access_models import LoginTransaction, User, WebSession
from apple_remote_sign.persistence.access_repository import digest
from apple_remote_sign.persistence.models import utcnow


class LoginTransactionError(ApiError):
    def __init__(self, reason: str) -> None:
        super().__init__(400, "Login expired or invalid. Please sign in again.")
        self.reason = reason


class AuthRepository:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    def bind_user(self, username: str, issuer: str, subject: str) -> User:
        if username not in self.settings.allowed_usernames:
            raise ApiError(403, "This user is not allowed to sign in.")
        try:
            return self._bind_user(username, issuer, subject)
        except IntegrityError:
            raise ApiError(403, "Identity already bound; contact the administrator.") from None

    def _bind_user(self, username: str, issuer: str, subject: str) -> User:
        with self.sessions.begin() as session:
            user = session.scalar(select(User).where(User.username == username).with_for_update())
            if user is None:
                user = User(username=username)
                session.add(user)
            if user.subject is not None and (user.subject != subject or user.issuer != issuer):
                raise ApiError(403, "Username is already bound to another identity.")
            user.issuer = issuer
            user.subject = subject
            return user

    def create_session(self, user_id: str, old_token: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        with self.sessions.begin() as session:
            session.execute(delete(WebSession).where(WebSession.expires_at <= utcnow()))
            if old_token:
                session.execute(
                    delete(WebSession).where(WebSession.token_hash == digest(old_token))
                )
            session.add(
                WebSession(
                    token_hash=digest(token),
                    user_id=user_id,
                    csrf_token=secrets.token_urlsafe(32),
                    expires_at=utcnow() + timedelta(seconds=self.settings.session_ttl_seconds),
                )
            )
        return token

    def authenticate_session(self, token: str) -> Principal:
        with self.sessions() as session:
            stored = session.get(WebSession, digest(token))
            if stored is None or stored.expires_at <= utcnow():
                raise ApiError(401, "Please sign in.")
            user = session.get(User, stored.user_id)
            if user is None or user.username not in self.settings.allowed_usernames:
                raise ApiError(401, "Please sign in.")
            return Principal(
                user_id=user.id,
                username=user.username,
                is_admin=user.username in self.settings.oidc_admin_usernames,
                session_hash=stored.token_hash,
                csrf_token=stored.csrf_token,
            )

    def logout(self, session_hash: str) -> None:
        with self.sessions.begin() as session:
            session.execute(delete(WebSession).where(WebSession.token_hash == session_hash))

    def save_login(self, state: str, browser: str, data: str) -> None:
        with self.sessions.begin() as session:
            session.execute(delete(LoginTransaction).where(LoginTransaction.expires_at <= utcnow()))
            session.add(
                LoginTransaction(
                    state_hash=digest(state),
                    browser_hash=digest(browser),
                    data=data,
                    expires_at=utcnow() + timedelta(seconds=self.settings.oidc_login_ttl_seconds),
                )
            )

    def consume_login(self, state: str, browser: str) -> str:
        with self.sessions.begin() as session:
            transaction = session.get(LoginTransaction, digest(state))
            if transaction is None or transaction.expires_at <= utcnow():
                raise LoginTransactionError("login_expired")
            if not secrets.compare_digest(transaction.browser_hash, digest(browser)):
                raise LoginTransactionError("login_cookie_mismatch")
            # Conditional DELETE provides single-use semantics even without row-lock support.
            result = session.connection().execute(
                delete(LoginTransaction).where(
                    LoginTransaction.state_hash == digest(state),
                    LoginTransaction.expires_at > utcnow(),
                )
            )
            consumed = result.rowcount == 1
            if not consumed:
                raise LoginTransactionError("login_expired")
            return transaction.data
