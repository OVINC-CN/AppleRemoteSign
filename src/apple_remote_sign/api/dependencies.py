from typing import cast

from fastapi import Request

from apple_remote_sign.access import Principal
from apple_remote_sign.persistence.access_repository import AccessRepository
from apple_remote_sign.services.context import ServiceContext


def principal(request: Request) -> Principal:
    return cast(Principal, request.state.principal)


def context(request: Request) -> ServiceContext:
    return cast(ServiceContext, request.app.state.context)


def access(request: Request) -> AccessRepository:
    return cast(AccessRepository, request.app.state.access_repository)
