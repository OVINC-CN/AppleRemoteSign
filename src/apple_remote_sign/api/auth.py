import logging
import secrets
from uuid import UUID

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apple_remote_sign.access import Principal
from apple_remote_sign.config import Settings
from apple_remote_sign.domain import ApiError
from apple_remote_sign.observability import actor, operation
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.persistence.auth_repository import AuthRepository

logger = logging.getLogger(__name__)


class AuthenticationMiddleware:
    """Authenticate and authorize uploads before multipart parsing reads the body."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            with operation("authentication"):
                await self.authenticate(request)
        except ApiError as exc:
            logger.warning(
                "authentication.denied\nstatus: %d\nreason: %s",
                exc.status_code,
                exc,
            )
            headers = {"Cache-Control": "no-store"}
            if exc.status_code == 401 and request.url.path.startswith("/openapi/"):
                headers["WWW-Authenticate"] = "Bearer"
            await JSONResponse(
                {"data": None, "error_message": str(exc)},
                status_code=exc.status_code,
                headers=headers,
            )(scope, receive, send)
            return
        except Exception:
            logger.exception("authentication.failed")
            await JSONResponse(
                {"data": None, "error_message": "Authentication service unavailable."},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return

        async def no_cache(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value)
                    for name, value in message["headers"]
                    if name.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, no_cache)

    async def authenticate(self, request: Request) -> None:
        path = request.url.path
        if path in {"/api/v1/auth/login", "/api/v1/auth/callback"}:
            return
        machine = path.startswith("/openapi/v1")
        if not machine and not path.startswith("/api/v1"):
            return
        access: AccessRepository = request.app.state.access_repository
        if machine:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not token or len(token) > 256:
                raise ApiError(401, "Invalid or missing Bearer token.")
            principal = await run_in_threadpool(access.authenticate_key, token)
        else:
            auth: AuthRepository = request.app.state.auth_repository
            token = request.cookies.get(self.settings.cookie_name, "")
            if not token or len(token) > 256:
                raise ApiError(401, "Please sign in.")
            principal = await run_in_threadpool(auth.authenticate_session, token)
            self.check_web_request(request, principal)
        request.state.principal = principal
        actor.set(
            f"user_id: {principal.user_id or '-'}\nkey_id: {principal.key_id or '-'}\n"
            f"application_id: {principal.application_id or '-'}"
        )
        with operation("authorization"):
            await run_in_threadpool(
                self.authorize_resource, path, request.method, principal, access
            )

    def check_web_request(self, request: Request, principal: Principal) -> None:
        if request.url.path.startswith("/api/v1/admin/") and not principal.is_admin:
            raise ApiError(403, "Administrator access required.")
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        origin = request.headers.get("origin", "")
        csrf = request.headers.get("x-csrf-token", "")
        if origin not in self.settings.allowed_origins or not secrets.compare_digest(
            csrf.encode(), (principal.csrf_token or "").encode()
        ):
            raise ApiError(403, "Invalid request origin or CSRF token.")

    @staticmethod
    def authorize_resource(
        path: str, method: str, principal: Principal, access: AccessRepository
    ) -> None:
        parts = path.strip("/").split("/")
        resource = 3 if len(parts) > 2 and parts[2] == "admin" else 2
        if len(parts) <= resource + 1:
            return
        kind, identifier = parts[resource : resource + 2]
        try:
            identifier = str(UUID(identifier))
        except ValueError:
            return  # Let the typed route produce its usual 422 response.
        if kind == "jobs":
            access.require_job(identifier, principal)
        elif kind == "applications":
            signing = method == "POST" and parts[-1] == "jobs"
            access.require_application(identifier, principal, signing=signing)
