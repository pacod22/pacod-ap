"""FastAPI dependencies for the admin API."""

from __future__ import annotations

from functools import lru_cache

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, IdentityUnavailableError
from app.guards.auth import AuthenticatedUser, AuthGuard
from app.services.embeddings_client import EmbeddingsClient
from app.services.identity import get_identity
from app.services.vector_store import VectorStore


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache(maxsize=1)
def get_qdrant_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=get_settings().qdrant_url, prefer_grpc=False)


@lru_cache(maxsize=1)
def get_embeddings_client() -> EmbeddingsClient:
    return EmbeddingsClient()


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    return VectorStore()


@lru_cache(maxsize=1)
def get_ingestion_client() -> httpx.AsyncClient:
    """Shared httpx client for delegating ingestion requests to the ingestion service."""
    return httpx.AsyncClient(base_url=get_settings().ingestion_url, timeout=300.0)


_http_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_auth_guard() -> AuthGuard:
    s = get_settings()
    return AuthGuard(dev_mode=s.app_env == "development" and s.auth_allow_dev_tokens)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
) -> AuthenticatedUser:
    """FastAPI dependency: authenticate the bearer token or raise 401."""
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        user = await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e
    except IdentityUnavailableError as e:
        raise HTTPException(status_code=503, detail={"code": e.code}) from e
    # Admin decisions on a copy of pacod-id's bindings older than ID_MAX_STALENESS are
    # refused (a removed admin must not keep acting). Dev tokens never ask pacod-id.
    identity = get_identity()
    if identity is not None and user.session_id is not None and not identity.feed_fresh:
        raise HTTPException(status_code=503, detail={"code": "identity_unavailable"})
    return user


async def require_admin(user: AuthenticatedUser = Depends(require_auth)) -> AuthenticatedUser:
    """FastAPI dependency: require admin access.

    Superusers are a strict superset of admins (they additionally edit agent
    prompts), so this reader gate admits both — a superuser is never locked out of
    a page a regular admin can see.
    """
    if not (user.is_admin() or user.is_superuser()):
        raise HTTPException(
            status_code=403, detail={"code": "forbidden", "message": "Admin role required"}
        )
    return user


async def require_superuser(user: AuthenticatedUser = Depends(require_auth)) -> AuthenticatedUser:
    """FastAPI dependency: require the superuser role (edit agent phase prompts).

    Regular admins get read-only access to the prompts panel; only superusers may
    mutate a prompt. This is the authoritative gate — the frontend merely hides the
    edit controls for non-superusers.
    """
    if not user.is_superuser():
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Superuser role required to edit prompts"},
        )
    return user


# Who may grant superuser, change a superuser's account or remove the last admin is decided by
# pacod-id now (its grant rules and protected roles, authz/pacod.yaml), on every change the
# user pages make through admin_api/services/identity_admin.py.
