# -*- coding: utf-8 -*-
"""
Auth middleware: protect /api/v1/* when admin auth is enabled.
"""

from __future__ import annotations

import logging
import ipaddress
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth import COOKIE_NAME, is_auth_enabled, verify_session
from src.webui_security import (
    current_webui_bound_host,
    is_insecure_public_api_allowed,
    is_public_bind_host,
    public_auth_guard_message,
)

logger = logging.getLogger(__name__)

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/health",
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _path_exempt(path: str) -> bool:
    """Check if path is exempt from auth."""
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


def _is_loopback_host(host: str | None) -> bool:
    normalized = (host or "").strip()
    if not normalized:
        return False
    if normalized.lower() in {"localhost", "[::1]", "::1"}:
        return True

    normalized = normalized.strip("[]")
    normalized = normalized.split("%", 1)[0]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_loopback_client(request: Request) -> bool:
    return _is_loopback_host(request.client.host if request.client else None)


def _get_effective_bound_host(request: Request) -> str:
    """Return runtime bind host from request scope when available."""
    server_host: str = ""
    server = request.scope.get("server")
    if isinstance(server, tuple) and len(server) > 0:
        candidate = server[0]
        if isinstance(candidate, str):
            server_host = candidate.strip()
    if server_host:
        return server_host

    url_host = request.url.hostname
    if url_host:
        return str(url_host)

    return current_webui_bound_host()


class AuthMiddleware(BaseHTTPMiddleware):
    """Require valid session for /api/v1/* when auth is enabled."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        if _path_exempt(path):
            return await call_next(request)

        if not is_auth_enabled():
            bound_host = _get_effective_bound_host(request)
            if (
                path.startswith("/api/v1/")
                and (
                    is_public_bind_host(bound_host)
                    or (not bound_host and not _is_loopback_client(request))
                )
                and not is_insecure_public_api_allowed()
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "admin_auth_required",
                        "message": public_auth_guard_message(bound_host),
                    },
                )
            return await call_next(request)

        if not path.startswith("/api/v1/"):
            return await call_next(request)

        cookie_val = request.cookies.get(COOKIE_NAME)
        if not cookie_val or not verify_session(cookie_val):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Login required",
                },
            )

        return await call_next(request)


def add_auth_middleware(app):
    """Add auth middleware to protect API routes.

    The middleware is always registered; whether auth is enforced is determined
    at request time by is_auth_enabled() so the decision stays consistent across
    any runtime configuration reload.
    """
    app.add_middleware(AuthMiddleware)
