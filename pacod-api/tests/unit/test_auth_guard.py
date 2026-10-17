"""Tests for AuthGuard — the dev-token path (pacod-id's tokens: test_identity_guard.py)."""

from __future__ import annotations

import pytest

from app.core.exceptions import AuthenticationError
from app.guards.auth import AuthGuard

# ---------------------------------------------------------------------------
# Dev-token mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuthGuardDevMode:
    async def test_dev_token_accepted(self):
        guard = AuthGuard(dev_mode=True)
        user = await guard.authenticate("Bearer dev-token-alice")
        assert user.user_id == "alice"
        assert user.email == "alice@example.local"

    async def test_dev_token_carries_admin_and_user_roles(self):
        guard = AuthGuard(dev_mode=True)
        user = await guard.authenticate("Bearer dev-token-alice")
        assert "admin" in user.roles
        assert "user" in user.roles
        # Dev tokens also grant superuser so prompt editing works in dev.
        assert "superuser" in user.roles

    async def test_dev_token_is_admin(self):
        guard = AuthGuard(dev_mode=True)
        user = await guard.authenticate("Bearer dev-token-alice")
        assert user.is_admin()
        assert user.is_superuser()

    async def test_dev_token_case_insensitive_scheme(self):
        guard = AuthGuard(dev_mode=True)
        user = await guard.authenticate("bearer dev-token-bob")
        assert user.user_id == "bob"

    async def test_missing_header_rejected(self):
        guard = AuthGuard(dev_mode=True)
        with pytest.raises(AuthenticationError):
            await guard.authenticate(None)

    async def test_malformed_header_rejected(self):
        guard = AuthGuard(dev_mode=True)
        with pytest.raises(AuthenticationError):
            await guard.authenticate("Basic dGVzdDp0ZXN0")

    async def test_empty_token_rejected(self):
        guard = AuthGuard(dev_mode=True)
        with pytest.raises(AuthenticationError):
            await guard.authenticate("Bearer ")

    async def test_dev_token_without_user_suffix_rejected(self):
        guard = AuthGuard(dev_mode=True)
        with pytest.raises(AuthenticationError):
            await guard.authenticate("Bearer dev-token-")

    async def test_dev_token_rejected_in_prod_mode(self):
        guard = AuthGuard(dev_mode=False)
        with pytest.raises(AuthenticationError):
            await guard.authenticate("Bearer dev-token-alice")


# ---------------------------------------------------------------------------
# Anything else: pacod-id's tokens are tested in test_identity_guard.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_pacod_id_a_non_dev_token_is_refused(monkeypatch):
    import app.services.identity as identity

    monkeypatch.setattr(identity, "_identity", None)
    for token in ("garbage", "a.b.c", "pidt_x"):
        with pytest.raises(AuthenticationError):
            await AuthGuard(dev_mode=True).authenticate(f"Bearer {token}")
