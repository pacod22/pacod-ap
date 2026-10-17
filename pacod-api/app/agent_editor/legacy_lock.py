"""Ownership of settings between the agent editor and the legacy config pages.

Once an agent is editor-managed (``profiles.published_version_id`` set), the
settings its published version sets explicitly are owned by the editor: the
legacy pages show the overlay value (what turns actually run with) and reject a
write that would *change* one (409 ``managed_by_agent_editor``). Comparing
values — not key presence — keeps a page that PATCHes its whole form usable:
re-sending the shown (owned) value is a no-op, not a conflict. Inherited
(``None``) settings stay fully editable on the legacy pages.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.agent_editor.compile import CompiledOverlay
from app.agent_editor.effective import load_compiled
from app.services.gateway_models import remap_model
from app.services.profiles import get_profile

MANAGED_CODE = "managed_by_agent_editor"


async def published_overlay(profile_id: str, redis=None) -> CompiledOverlay | None:
    """The compiled overlay of the agent's published version, or None when the
    agent is not editor-managed."""
    profile = await get_profile(profile_id, redis=redis)
    if profile is None or profile.published_version_id is None:
        return None
    return await load_compiled(profile.published_version_id, redis)


def _same(a, b) -> bool:
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return list(a) == list(b)
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


def conflicts(updates: dict, owned: dict) -> list[str]:
    """Fields an update would change although the editor owns them (compared
    with the value the page shows — retired model ids remapped)."""
    return sorted(k for k, v in updates.items() if k in owned and not _same(v, _shown(k, owned[k])))


def _shown(key: str, value):
    return remap_model(value) if isinstance(value, str) and key.endswith("_model") else value


def raise_if_conflicts(updates: dict, owned: dict | None) -> None:
    fields = conflicts(updates, owned or {})
    if fields:
        raise HTTPException(
            status_code=409,
            detail={
                "code": MANAGED_CODE,
                "message": "These settings are managed by the Agent editor: " + ", ".join(fields),
                "fields": fields,
            },
        )


def without_owned(updates: dict, owned: dict | None) -> dict:
    """Validate an update against the editor's ownership, then drop the owned
    keys: re-sending a shown (owned) value is allowed, but it must NOT be
    written into app_settings — the base layer would silently take the
    version's value and a later rollback to a version that inherits it would
    no longer restore the original."""
    raise_if_conflicts(updates, owned)
    if not owned:
        return updates
    return {k: v for k, v in updates.items() if k not in owned}


def with_owned(effective: dict, owned: dict | None) -> dict:
    """The effective values as turns see them (owned overlay values applied,
    retired model ids remapped exactly as the resolver does)."""
    if not owned:
        return effective
    out = dict(effective)
    for k, v in owned.items():
        if k in out:
            if isinstance(v, tuple):
                v = list(v)
            elif isinstance(v, str) and k.endswith("_model"):
                v = remap_model(v)
            out[k] = v
    return out
