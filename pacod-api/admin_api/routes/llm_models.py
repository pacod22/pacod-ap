"""Admin route for the models the platform may use — read-only.

GET /api/v1/llm-models — the models the gateway offers this platform, and the console's address

The gateway (pacod-gw) owns the model repository, the prices, the provider credentials and the
models' life cycle; the platform's workspace grants decide what the app may call. Adding,
disabling or pricing a model happens in the gateway console, not here, and takes effect there
at once — there is no export file and no restart any more.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from admin_api.dependencies import require_admin
from admin_api.schemas import LlmModelRead, LlmModelsResponse
from app.core.config import get_settings
from app.guards.auth import AuthenticatedUser
from app.services.gateway_models import gateway_model_list

router = APIRouter(prefix="/api/v1/llm-models", tags=["llm-models"])


@router.get("", response_model=LlmModelsResponse)
async def list_llm_models(_: AuthenticatedUser = Depends(require_admin)) -> LlmModelsResponse:
    rows, reachable = await gateway_model_list()
    return LlmModelsResponse(
        models=[LlmModelRead(id=r["id"], owned_by=r.get("owned_by")) for r in rows],
        console_url=get_settings().gateway_console_url or None,
        reachable=reachable,
    )
