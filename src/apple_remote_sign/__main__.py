"""Run Uvicorn with the same JSON format in the supervisor and application."""

import argparse
import logging

import uvicorn
from fastapi import FastAPI

from apple_remote_sign.config import Settings
from apple_remote_sign.observability import LOGGING_CONFIG, configure_logging


def load_app() -> FastAPI:
    # Reload workers also need to report factory/configuration errors as JSON.
    try:
        from apple_remote_sign.app import create_app

        return create_app()
    except Exception:
        logging.getLogger(__name__).exception("service.start_failed")
        raise SystemExit(1) from None


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Apple Remote Sign API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--forwarded-allow-ips", default=None)
    args = parser.parse_args()
    try:
        settings = Settings()  # type: ignore[call-arg]
        uvicorn.run(
            "apple_remote_sign.__main__:load_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            forwarded_allow_ips=args.forwarded_allow_ips,
            workers=1,
            timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
            log_config=LOGGING_CONFIG,
        )
    except Exception:
        logging.getLogger(__name__).exception("service.start_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
