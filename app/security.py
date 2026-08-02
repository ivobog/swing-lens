from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

from fastapi import HTTPException, Request
from fastapi import status as http_status
from starlette.middleware.trustedhost import TrustedHostMiddleware

LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
LOOPBACK_APP_HOSTS = {"127.0.0.1", "::1", "localhost"}
PUBLIC_BIND_HOSTS = {"0.0.0.0", "::", ""}
UNSAFE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

ROUTE_CLASS_PUBLIC_LOCAL = "PUBLIC_LOCAL"
ROUTE_CLASS_LOCAL_ADMIN = "LOCAL_ADMIN"
ROUTE_CLASS_INTERNAL = "INTERNAL"
ROUTE_CLASS_EXEMPT = "EXEMPT"

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class UnsafeRouteClassification:
    category: str
    reason: str
    csrf_required: bool = False
    local_admin_required: bool = False


def unsafe_route(
    category: str,
    *,
    reason: str,
    csrf_required: bool = False,
    local_admin_required: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(endpoint: Callable[P, R]) -> Callable[P, R]:
        endpoint.swinglens_unsafe_route = UnsafeRouteClassification(
            category=category,
            reason=reason,
            csrf_required=csrf_required,
            local_admin_required=local_admin_required,
        )
        return endpoint

    return decorate


def issue_local_admin_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def install_trusted_host_middleware(app: Any, app_host: str) -> None:
    allowed_hosts = sorted(
        {
            "testserver",
            "testclient",
            "localhost",
            "127.0.0.1",
            "[::1]",
            "::1",
            app_host,
        }
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


def is_local_admin_host(host: str | None) -> bool:
    return host in LOCAL_ADMIN_HOSTS


def local_admin_csrf_token(request: Request) -> str:
    token = getattr(request.app.state, "local_admin_csrf_token", None)
    if not isinstance(token, str) or not token:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Local admin CSRF token is unavailable.",
        )
    return token


def require_local_admin(
    request: Request,
    *,
    enabled: bool,
    disabled_message: str,
    local_only_message: str,
    csrf_message: str | None = None,
    structured_code: str | None = None,
    csrf_required: bool = False,
) -> None:
    if not enabled:
        raise _guard_error(
            http_status.HTTP_404_NOT_FOUND,
            disabled_message,
            structured_code,
        )
    host = request.client.host if request.client is not None else None
    if not is_local_admin_host(host):
        raise _guard_error(
            http_status.HTTP_403_FORBIDDEN,
            local_only_message,
            structured_code,
        )
    if csrf_required:
        expected = local_admin_csrf_token(request)
        supplied = request.headers.get("x-csrf-token")
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise _guard_error(
                http_status.HTTP_403_FORBIDDEN,
                csrf_message or "Local admin CSRF token is required.",
                structured_code,
            )


def _guard_error(status_code: int, message: str, structured_code: str | None) -> HTTPException:
    detail: str | dict[str, str]
    if structured_code:
        detail = {"code": structured_code, "message": message}
    else:
        detail = message
    return HTTPException(status_code=status_code, detail=detail)
