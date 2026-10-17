"""Admin routes for LLM templates (global, reusable LLM call-parameter sets).

GET    /api/v1/llm-config/templates              — templates + usages + models + capabilities
POST   /api/v1/llm-config/templates              — create
PATCH  /api/v1/llm-config/templates/{id}         — update (live: every role using it;
                                                  superuser-only while the editor assistant uses it)
DELETE /api/v1/llm-config/templates/{id}         — delete an unused template (409 when in use)
GET    /api/v1/llm-config/templates/{id}/usages  — which profile roles reference it

Nested under /llm-config so the frontend's admin-path prefix already routes it.
Profile roles pick a template on the LLM-config, Review-process and
editor-assistant panels; see app/services/llm_templates.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import (
    LLMTemplateCreate,
    LLMTemplateOut,
    LLMTemplatesRead,
    LLMTemplateUpdate,
    LLMTemplateUsage,
)
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services import llm_templates as svc
from app.services.gateway_models import available_models
from app.services.llm_capabilities import ignored_params, model_capabilities

router = APIRouter(prefix="/api/v1/llm-config/templates", tags=["llm-templates"])
log = get_logger(__name__)


def _out(row: dict, usages: list[dict]) -> LLMTemplateOut:
    return LLMTemplateOut(
        **{k: v for k, v in row.items() if k in LLMTemplateOut.model_fields},
        usages=[
            LLMTemplateUsage(**{k: u[k] for k in ("profile_id", "group", "role")}) for u in usages
        ],
        ignored_params=ignored_params(row["model"], row),
    )


async def _guard_assistant_template(template_id: str, caller: AuthenticatedUser) -> None:
    """The editor assistant's LLM config is superuser-only; editing or deleting a
    template it uses would change it through the live link, so that is too."""
    if caller.is_superuser():
        return
    if any(u["group"] == "assistant" for u in await svc.template_usages(template_id)):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "superuser_required",
                "message": "This template is used by the editor assistant (superuser-only)",
            },
        )


async def _validate_model(model: str) -> None:
    available = await available_models()
    if available and model not in available:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_model", "message": f"{model!r} is not a registered model"},
        )


@router.get("", response_model=LLMTemplatesRead)
async def list_llm_templates(_: AuthenticatedUser = Depends(require_admin)):
    rows = await svc.list_templates()
    try:
        usages = await svc.template_usages()
    except Exception as e:  # pragma: no cover - a list must not 500 on the usage scan
        log.warning("admin_llm_templates_usages_failed", error=str(e))
        usages = []
    by_tpl: dict[str, list[dict]] = {}
    for u in usages:
        by_tpl.setdefault(u["template_id"], []).append(u)
    models = await available_models()
    caps = {m: model_capabilities(m).to_dict() for m in {*models, *(r["model"] for r in rows)}}
    return LLMTemplatesRead(
        templates=[_out(r, by_tpl.get(r["id"], [])) for r in rows],
        available_models=models,
        capabilities=caps,
    )


@router.post("", response_model=LLMTemplateOut, status_code=201)
async def create_llm_template(
    body: LLMTemplateCreate,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    template_id = (body.id or "").strip() or svc.slugify(body.name)
    if not svc.valid_id(template_id):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_template_id",
                "message": "Template id may contain only lowercase letters, digits and -",
            },
        )
    await _validate_model(body.model)
    data = body.model_dump(exclude={"id"})
    data["id"] = template_id
    data["description"] = data.get("description") or ""
    data["thinking_budget_tokens"] = data.get("thinking_budget_tokens") or 0
    try:
        row = await svc.create_template(data, updated_by=caller.user_id, redis=redis)
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail={"code": "template_exists", "message": f"Template {template_id!r} exists"},
        ) from None
    log.info("admin_llm_template_created", template_id=template_id, caller=caller.user_id)
    return _out(row, [])


@router.patch("/{template_id}", response_model=LLMTemplateOut)
async def update_llm_template(
    template_id: str,
    body: LLMTemplateUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    await _guard_assistant_template(template_id, caller)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    # Required columns cannot be cleared.
    for required in ("name", "model", "max_output_tokens"):
        if required in changes and changes[required] is None:
            changes.pop(required)
    if "description" in changes and changes["description"] is None:
        changes["description"] = ""
    if "thinking_budget_tokens" in changes and changes["thinking_budget_tokens"] is None:
        changes["thinking_budget_tokens"] = 0
    if "model" in changes:
        await _validate_model(changes["model"])
    row = await svc.update_template(template_id, changes, updated_by=caller.user_id, redis=redis)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    log.info(
        "admin_llm_template_updated",
        template_id=template_id,
        fields=sorted(changes),
        caller=caller.user_id,
    )
    return _out(row, await svc.template_usages(template_id))


@router.delete("/{template_id}", status_code=204)
async def delete_llm_template(
    template_id: str,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    await _guard_assistant_template(template_id, caller)
    try:
        deleted = await svc.delete_template(template_id, redis=redis)
    except svc.TemplateInUse as e:
        log.warning("admin_llm_template_delete_in_use", template_id=template_id)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "template_in_use",
                "message": "The template is still assigned to LLM roles",
                "usages": e.usages,
            },
        ) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    log.info("admin_llm_template_deleted", template_id=template_id, caller=caller.user_id)
    return Response(status_code=204)


@router.get("/{template_id}/usages", response_model=list[LLMTemplateUsage])
async def llm_template_usages(template_id: str, _: AuthenticatedUser = Depends(require_admin)):
    return [
        LLMTemplateUsage(**{k: u[k] for k in ("profile_id", "group", "role")})
        for u in await svc.template_usages(template_id)
    ]
