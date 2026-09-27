from __future__ import annotations

import secrets
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import iscoroutinefunction, signature
from typing import Any, ParamSpec, TypeVar

from fastapi import HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Match

from app.services.runtime_mutation_authority import (
    MutationCapability,
    RuntimeMutationAuthority,
    RuntimeMutationAuthorityError,
)
from app.settings import RuntimeMode, get_settings

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
    mutation_capability: MutationCapability
    operation: str
    csrf_required: bool = False
    local_admin_required: bool = False
    certification_root_creation: bool = False


@dataclass(frozen=True)
class HttpMutationRouteRecord:
    path: str
    methods: tuple[str, ...]
    classification: UnsafeRouteClassification | None

    @property
    def classified(self) -> bool:
        return self.classification is not None


_active_http_request: ContextVar[Request | None] = ContextVar(
    "swinglens_runtime_mutation_request", default=None
)


class RuntimeMutationContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = _active_http_request.set(request)
        try:
            classification, matched = _matched_mutation_classification(request)
            if matched:
                if classification is None:
                    settings = request.app.state.settings
                    if settings.runtime_mode is RuntimeMode.CERTIFICATION:
                        return _runtime_mutation_error_response(
                            RuntimeMutationAuthorityError(
                                "UNCLASSIFIED_CERTIFICATION_MUTATION",
                                "the mutating HTTP route has no runtime authority classification",
                            )
                        )
                else:
                    try:
                        _authorize_http_mutation(request, classification)
                    except HTTPException as exc:
                        return JSONResponse(
                            status_code=exc.status_code,
                            content={"detail": exc.detail},
                        )
            return await call_next(request)
        finally:
            _active_http_request.reset(token)


def http_mutation_route_registry(app: Any) -> tuple[HttpMutationRouteRecord, ...]:
    records: list[HttpMutationRouteRecord] = []
    for route in app.routes:
        methods = tuple(
            sorted(UNSAFE_HTTP_METHODS.intersection(getattr(route, "methods", set()) or set()))
        )
        if not methods:
            continue
        classification = getattr(
            getattr(route, "endpoint", None), "swinglens_unsafe_route", None
        )
        records.append(
            HttpMutationRouteRecord(
                path=str(getattr(route, "path", "")),
                methods=methods,
                classification=classification,
            )
        )
    return tuple(records)


def _matched_mutation_classification(
    request: Request,
) -> tuple[UnsafeRouteClassification | None, bool]:
    if request.method not in UNSAFE_HTTP_METHODS:
        return None, False
    for route in request.app.routes:
        match, child_scope = route.matches(request.scope)
        if match is not Match.FULL:
            continue
        endpoint = child_scope.get("endpoint", getattr(route, "endpoint", None))
        return getattr(endpoint, "swinglens_unsafe_route", None), True
    return None, False


def unsafe_route(
    category: str,
    *,
    reason: str,
    mutation_capability: MutationCapability,
    operation: str,
    csrf_required: bool = False,
    local_admin_required: bool = False,
    certification_root_creation: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(endpoint: Callable[P, R]) -> Callable[P, R]:
        classification = UnsafeRouteClassification(
            category=category,
            reason=reason,
            mutation_capability=mutation_capability,
            operation=operation,
            csrf_required=csrf_required,
            local_admin_required=local_admin_required,
            certification_root_creation=certification_root_creation,
        )
        endpoint_signature = signature(endpoint)

        def authorize(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            bound = endpoint_signature.bind_partial(*args, **kwargs)
            request = bound.arguments.get("request")
            if not isinstance(request, Request) and not hasattr(request, "app"):
                request = _active_http_request.get()
            _authorize_http_mutation(request, classification)

        if iscoroutinefunction(endpoint):

            @wraps(endpoint)
            async def async_wrapped(*args: P.args, **kwargs: P.kwargs):
                authorize(args, kwargs)
                return await endpoint(*args, **kwargs)

            wrapped = async_wrapped
        else:

            @wraps(endpoint)
            def sync_wrapped(*args: P.args, **kwargs: P.kwargs):
                authorize(args, kwargs)
                return endpoint(*args, **kwargs)

            wrapped = sync_wrapped
        wrapped.swinglens_unsafe_route = classification
        return wrapped

    return decorate


def _authorize_http_mutation(
    request: Request | Any | None,
    classification: UnsafeRouteClassification,
) -> None:
    settings = (
        getattr(getattr(request, "app", None), "state", None)
        if request is not None
        else None
    )
    settings = getattr(settings, "settings", None) or get_settings()
    mode = getattr(settings, "runtime_mode", RuntimeMode.NORMAL)
    if classification.mutation_capability is MutationCapability.READ_ONLY:
        return
    if mode is RuntimeMode.CERTIFICATION and (
        classification.mutation_capability is MutationCapability.NORMAL_ONLY
    ):
        raise _runtime_mutation_http_error(
            RuntimeMutationAuthorityError(
                "CERTIFICATION_MUTATION_FORBIDDEN",
                f"{classification.operation} is not authorized during certification",
            )
        )
    supplied_session = None
    if request is not None:
        supplied_session = getattr(request, "headers", {}).get(
            "x-swinglens-certification-session"
        )
    try:
        authority = RuntimeMutationAuthority.from_settings(
            settings,
            operation=classification.operation,
            supplied_certification_session_id=supplied_session,
        )
        authority.require_capability(
            classification.mutation_capability,
            certification_root_creation=classification.certification_root_creation,
        )
    except RuntimeMutationAuthorityError as exc:
        raise _runtime_mutation_http_error(exc) from exc


def _runtime_mutation_http_error(exc: RuntimeMutationAuthorityError) -> HTTPException:
    return HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail={"code": exc.code, "message": exc.message},
    )


def _runtime_mutation_error_response(exc: RuntimeMutationAuthorityError) -> JSONResponse:
    return JSONResponse(
        status_code=http_status.HTTP_409_CONFLICT,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


def issue_local_admin_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def install_trusted_host_middleware(app: Any, app_host: str) -> None:
    allowed_hosts = sorted(
        {
            "testserver",
            "testclient",
            "localhost",
            "127.0.0.1",
            "host.docker.internal",
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
