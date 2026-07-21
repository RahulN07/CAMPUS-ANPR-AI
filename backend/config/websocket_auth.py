"""JWT authentication middleware for ANPR WebSocket connections."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


ANPR_WEBSOCKET_SUBPROTOCOL = "anpr.v1"
MAX_WEBSOCKET_TOKEN_LENGTH = 4096

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def extract_jwt_from_subprotocols(scope: Scope) -> str | None:
    """
    Read a JWT from ``Sec-WebSocket-Protocol`` without putting credentials in
    the URL. The browser sends protocols as ``["anpr.v1", accessToken]``.
    """

    subprotocols = scope.get("subprotocols") or []

    for value in subprotocols:
        if not isinstance(value, str):
            continue

        candidate = value.strip()
        if not candidate or candidate == ANPR_WEBSOCKET_SUBPROTOCOL:
            continue

        if len(candidate) > MAX_WEBSOCKET_TOKEN_LENGTH:
            continue

        # A compact JWT contains header, payload, and signature segments.
        if candidate.count(".") == 2:
            return candidate

    return None


@database_sync_to_async
def authenticate_access_token(raw_token: str):
    """Validate a SimpleJWT access token and load its active user."""

    authenticator = JWTAuthentication()
    validated_token = authenticator.get_validated_token(raw_token)
    return authenticator.get_user(validated_token)


class JwtSubprotocolAuthMiddleware:
    """
    Populate ``scope['user']`` from a SimpleJWT access token.

    Invalid or missing tokens are represented by ``AnonymousUser``. The
    consumer remains responsible for closing unauthorized connections with a
    WebSocket-specific close code.
    """

    def __init__(self, inner: ASGIApp):
        self.inner = inner

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        scoped = dict(scope)
        raw_token = extract_jwt_from_subprotocols(scoped)

        if raw_token is None:
            scoped["user"] = scoped.get("user") or AnonymousUser()
            scoped["jwt_auth_error"] = "missing_token"
        else:
            try:
                scoped["user"] = await authenticate_access_token(raw_token)
                scoped["jwt_auth_error"] = ""
            except (
                AuthenticationFailed,
                InvalidToken,
                TokenError,
            ):
                scoped["user"] = AnonymousUser()
                scoped["jwt_auth_error"] = "invalid_token"

        await self.inner(scoped, receive, send)