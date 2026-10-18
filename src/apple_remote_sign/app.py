from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from apple_remote_sign.api.admin import router as admin_router
from apple_remote_sign.api.auth import AuthenticationMiddleware
from apple_remote_sign.api.errors import install_error_handlers
from apple_remote_sign.api.health import router as health_router
from apple_remote_sign.api.oidc import build_oidc_client
from apple_remote_sign.api.oidc import router as oidc_router
from apple_remote_sign.api.routes import router
from apple_remote_sign.api.web_routes import router as web_router
from apple_remote_sign.config import Settings
from apple_remote_sign.observability import (
    RequestLoggingMiddleware,
    configure_logging,
    operation,
    set_log_secrets,
)
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.persistence.auth_repository import AuthRepository
from apple_remote_sign.recovery import RecoveryWorker
from apple_remote_sign.runtime import build_context
from apple_remote_sign.services.context import ServiceContext
from apple_remote_sign.tracing import build_tracer_provider, tracing_context


def create_app(settings: Settings | None = None, context: ServiceContext | None = None) -> FastAPI:
    configure_logging()
    settings = settings or Settings()  # type: ignore[call-arg]
    set_log_secrets(
        [
            settings.oidc_client_secret.get_secret_value(),
            settings.database_url.get_secret_value(),
            *settings.redis_secrets,
            *settings.distribution_secrets,
        ]
    )
    tracer_provider = build_tracer_provider(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        manager = nullcontext(context) if context is not None else build_context(settings)
        try:
            with tracing_context(tracer_provider), manager as services:
                with operation("startup.schema_check"):
                    services.repository.check_schema()
                services.locks.ping()
                app.state.context = services
                app.state.access_repository = AccessRepository(services.repository.sessions)
                app.state.auth_repository = AuthRepository(services.repository.sessions, settings)
                recovery = RecoveryWorker(services)
                recovery.start()
                app.state.ready = True
                try:
                    yield
                finally:
                    app.state.ready = False
                    recovery.close()
        finally:
            tracer_provider.shutdown()

    app = FastAPI(
        title="Apple Remote Sign",
        version="0.1.0",
        description=(
            "Sign and submit synchronously. Query Apple, staple and upload accepted artifacts "
            "to S3. Downloads redirect to authenticated CDN URLs."
        ),
        openapi_url="/openapi/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.ready = False
    app.state.tracer_provider = tracer_provider
    app.state.oidc_client = build_oidc_client(settings)
    app.add_middleware(AuthenticationMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "traceparent", "tracestate"],
        expose_headers=["Content-Disposition", "X-Request-ID", "X-Trace-ID"],
    )
    app.add_middleware(RequestLoggingMiddleware, tracer_provider=tracer_provider)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(router)
    app.include_router(oidc_router)
    app.include_router(web_router)
    app.include_router(admin_router)
    return app
