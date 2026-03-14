"""FastAPI dependencies."""

import secrets
from collections.abc import AsyncGenerator

from fastapi import HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    token: str | None = Query(default=None, alias="token"),
) -> None:
    """Validate API key from Bearer header or ?token= query parameter.

    When ``settings.api_key`` is not set (None or empty string), authentication
    is disabled and all requests are allowed (backward-compatible open access).

    SSE clients (EventSource) cannot send custom headers, so the ``?token=``
    query parameter serves as a fallback authentication method.

    Uses ``secrets.compare_digest`` for timing-safe comparison to prevent
    leaking key content via timing side-channels.
    """
    if not settings.api_key:
        return  # No key configured — open access

    # Prefer Bearer token, fall back to query param
    provided_key: str | None = None
    if credentials and credentials.credentials:
        provided_key = credentials.credentials
    elif token:
        provided_key = token

    if not provided_key:
        raise HTTPException(
            status_code=401,
            detail="API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(provided_key, settings.api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
