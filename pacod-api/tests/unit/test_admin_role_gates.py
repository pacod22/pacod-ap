"""Tests for the admin-API authorization gates.

The agent-prompts feature splits access: any admin may READ a prompt, only a
superuser may EDIT/RESET it. Dev tokens grant superuser to everyone, so the
negative path (a plain admin denied edit) is only verifiable with synthetic
users — which is exactly what these tests use. The dependency functions are
called directly with an explicit `user=`, bypassing the Depends(require_auth)
default, so no request stack is needed.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from admin_api.dependencies import require_admin, require_superuser
from app.guards.auth import AuthenticatedUser


def _user(*roles: str) -> AuthenticatedUser:
    return AuthenticatedUser(user_id="u1", email="u1@example.local", roles=tuple(roles))


class TestRequireSuperuser:
    async def test_superuser_allowed_to_edit(self):
        u = _user("admin", "superuser", "user")
        assert await require_superuser(u) is u

    async def test_plain_admin_denied_edit(self):
        with pytest.raises(HTTPException) as exc:
            await require_superuser(_user("admin", "user"))
        assert exc.value.status_code == 403

    async def test_plain_user_denied_edit(self):
        with pytest.raises(HTTPException) as exc:
            await require_superuser(_user("user"))
        assert exc.value.status_code == 403


class TestRequireAdmin:
    async def test_admin_can_read(self):
        u = _user("admin", "user")
        assert await require_admin(u) is u

    async def test_superuser_can_read(self):
        # Superuser is a strict superset — never locked out of an admin page.
        u = _user("superuser")
        assert await require_admin(u) is u

    async def test_plain_user_denied_read(self):
        with pytest.raises(HTTPException) as exc:
            await require_admin(_user("user"))
        assert exc.value.status_code == 403


# Who may grant superuser, change a superuser's account or remove the last admin is pacod-id's
# decision now: authz/vectors/pacod.yaml pins the grant rules (test_authz_manifest.py).
