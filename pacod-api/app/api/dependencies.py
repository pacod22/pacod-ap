"""FastAPI dependency providers — singletons across the process."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis

from app.agent.graph import AgentDeps, AgentRunner
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, IdentityUnavailableError
from app.guards.auth import AuthenticatedUser, AuthGuard
from app.guards.language import LanguageGuard
from app.guards.quota import QuotaGuard
from app.guards.topic import TopicGuard
from app.services.embeddings_client import EmbeddingsClient
from app.services.llm_client import LLMClient
from app.services.mcp_client import MCPClient
from app.services.vector_store import VectorStore
from app.services.web_search import WebSearch
from app.tools.kb_retrieval import KBRetriever
from app.tools.reference_facts import ReferenceFactsTools


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    return LLMClient()


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    s = get_settings()
    return Redis.from_url(s.redis_url, decode_responses=True)


@lru_cache(maxsize=1)
def get_auth_guard() -> AuthGuard:
    s = get_settings()
    return AuthGuard(dev_mode=s.app_env == "development" and s.auth_allow_dev_tokens)


@lru_cache(maxsize=1)
def get_language_guard() -> LanguageGuard:
    s = get_settings()
    return LanguageGuard(min_confidence=s.language_detection_min_confidence)


@lru_cache(maxsize=1)
def get_quota_guard() -> QuotaGuard:
    return QuotaGuard(redis=get_redis())


@lru_cache(maxsize=1)
def get_topic_guard() -> TopicGuard:
    return TopicGuard(llm=get_llm_client(), redis=get_redis())


@lru_cache(maxsize=1)
def get_embeddings_client() -> EmbeddingsClient:
    return EmbeddingsClient()


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    return VectorStore()


@lru_cache(maxsize=1)
def get_mcp_client() -> MCPClient:
    return MCPClient()


@lru_cache(maxsize=1)
def get_web_search() -> WebSearch:
    return WebSearch()


_http_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    auth: AuthGuard = Depends(get_auth_guard),
) -> AuthenticatedUser:
    """FastAPI dependency: authenticate the bearer token or raise 401."""
    authorization = f"Bearer {credentials.credentials}" if credentials else None
    try:
        return await auth.authenticate(authorization)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail={"code": e.code}) from e
    except IdentityUnavailableError as e:
        raise HTTPException(status_code=503, detail={"code": e.code}) from e


async def require_admin(user: AuthenticatedUser = Depends(require_auth)) -> AuthenticatedUser:
    """FastAPI dependency: require the authenticated user to hold the admin role."""
    if not user.is_admin():
        raise HTTPException(
            status_code=403, detail={"code": "forbidden", "message": "Admin role required"}
        )
    return user


@lru_cache(maxsize=1)
def get_agent_runner() -> AgentRunner:
    deps = AgentDeps(
        llm=get_llm_client(),
        kb=KBRetriever(get_embeddings_client(), get_vector_store(), get_redis()),
        facts=ReferenceFactsTools(get_mcp_client()),
        web=get_web_search(),
    )
    return AgentRunner(deps)
