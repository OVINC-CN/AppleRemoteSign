import json
import logging
import secrets
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from apple_remote_sign.api.dependencies import principal
from apple_remote_sign.config import Settings
from apple_remote_sign.domain import ApiError
from apple_remote_sign.observability import operation
from apple_remote_sign.persistence.access_repository import digest
from apple_remote_sign.persistence.auth_repository import AuthRepository, LoginTransactionError

router = APIRouter(prefix="/api/v1/auth", include_in_schema=False)
logger = logging.getLogger(__name__)
SIGNED_ALGORITHMS = {
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
    "PS384",
    "PS512",
    "EdDSA",
}


def build_oidc_client(settings: Settings) -> Any:
    from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]

    oauth = OAuth()
    return oauth.register(
        "oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        server_metadata_url=settings.oidc_issuer_url.rstrip("/")
        + "/.well-known/openid-configuration",
        client_kwargs={
            "scope": settings.oidc_scopes,
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": settings.oidc_token_endpoint_auth_method,
            "timeout": settings.oidc_timeout_seconds,
        },
    )


async def validate_provider(client: Any, settings: Settings) -> None:
    with operation("oidc.metadata"):
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != settings.oidc_issuer_url:
            raise ValueError("Issuer mismatch")
        algorithms = metadata.get("id_token_signing_alg_values_supported", ["RS256"])
        supported = [algorithm for algorithm in algorithms if algorithm in SIGNED_ALGORITHMS]
        if not supported:
            raise ValueError("Provider must support asymmetric signed ID tokens")
        metadata["id_token_signing_alg_values_supported"] = supported


def login_cookie_name(settings: Settings, state: str) -> str:
    return f"{settings.cookie_name}_oidc_{digest(state)}"


def set_cookie(
    response: Response, settings: Settings, token: str, *, state: str | None = None
) -> None:
    response.set_cookie(
        login_cookie_name(settings, state) if state is not None else settings.cookie_name,
        token,
        max_age=settings.oidc_login_ttl_seconds
        if state is not None
        else settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        domain=settings.cookie_domain,
        samesite="lax" if state is not None else settings.cookie_samesite,
        path="/",
    )


def clear_cookie(response: Response, settings: Settings, *, state: str | None = None) -> None:
    response.delete_cookie(
        login_cookie_name(settings, state) if state is not None else settings.cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax" if state is not None else settings.cookie_samesite,
    )


@router.get("/login")
async def login(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    client = request.app.state.oidc_client
    auth: AuthRepository = request.app.state.auth_repository
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    request.scope["session"] = {}
    try:
        request.state.phase = "oidc.authorization_redirect"
        await validate_provider(client, settings)
        response = cast(
            Response,
            await client.authorize_redirect(
                request,
                settings.oidc_redirect_uri,
                state=state,
            ),
        )
        await run_in_threadpool(auth.save_login, state, browser, json.dumps(request.session))
    except Exception as exc:
        logger.warning("oidc.login.failed\nerror: %s", type(exc).__name__)
        raise ApiError(502, "Identity provider is unavailable. Please try again.") from None
    set_cookie(response, settings, browser, state=state)
    logger.info(
        "oidc.login.created\ntransaction: %s\ncookie_secure: %s\ncookie_samesite: lax",
        digest(state)[:12],
        settings.cookie_secure,
    )
    return response


async def verified_username(request: Request) -> tuple[str, str]:
    settings: Settings = request.app.state.settings
    client = request.app.state.oidc_client
    request.state.phase = "oidc.metadata"
    await validate_provider(client, settings)
    request.state.phase = "oidc.token_exchange"
    with operation("oidc.token_exchange"):
        token = await client.authorize_access_token(
            request,
            claims_options={"iss": {"essential": True, "value": settings.oidc_issuer_url}},
            leeway=30,
        )
    claims = token.get("userinfo")
    if not token.get("id_token") or not claims:
        raise ValueError("Missing ID token")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
        raise ValueError("Invalid subject")
    username = claims.get(settings.oidc_username_claim)
    if username is None:
        request.state.phase = "oidc.userinfo"
        with operation("oidc.userinfo"):
            info = await client.userinfo(token=token)
        if info.get("sub") != subject:
            raise ValueError("UserInfo subject mismatch")
        username = info.get(settings.oidc_username_claim)
    if not isinstance(username, str) or not 1 <= len(username) <= 255:
        raise ValueError("Missing username claim")
    return username, subject


@router.get("/callback")
async def callback(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    auth: AuthRepository = request.app.state.auth_repository
    destination = settings.frontend_url.rstrip("/")
    state = request.query_params.get("state", "")
    request.state.phase = "oidc.transaction"
    try:
        if not state or len(state) > 256:
            raise LoginTransactionError("login_expired")
        browser = request.cookies.get(login_cookie_name(settings, state), "")
        if not browser or len(browser) > 256:
            raise LoginTransactionError("login_cookie_missing")
        data = await run_in_threadpool(auth.consume_login, state, browser)
        request.scope["session"] = json.loads(data)
        username, subject = await verified_username(request)
        request.state.phase = "oidc.identity_binding"
        user = await run_in_threadpool(auth.bind_user, username, settings.oidc_issuer_url, subject)
        token = await run_in_threadpool(
            auth.create_session,
            user.id,
            request.cookies.get(settings.cookie_name),
        )
        response = RedirectResponse(destination + "/jobs", status_code=303)
        set_cookie(response, settings, token)
        logger.info("session.login\nuser_id: %s", user.id)
    except Exception as exc:
        reason = "login_failed"
        if isinstance(exc, LoginTransactionError):
            reason = exc.reason
        elif isinstance(exc, ApiError) and exc.status_code == 403:
            reason = "access_denied"
        logger.warning(
            "oidc.callback.failed\nreason: %s\nphase: %s\nerror: %s",
            reason,
            request.state.phase,
            type(exc).__name__,
        )
        response = RedirectResponse(destination + "/login?error=" + reason, status_code=303)
    if state and len(state) <= 256:
        clear_cookie(response, settings, state=state)
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    auth: AuthRepository = request.app.state.auth_repository
    await run_in_threadpool(auth.logout, principal(request).session_hash or "")
    logger.info("session.logout\nuser_id: %s", principal(request).user_id)
    response = JSONResponse({"data": None, "error_message": ""})
    clear_cookie(response, request.app.state.settings)
    return response
