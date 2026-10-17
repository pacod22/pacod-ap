"""AuthGuard with pacod-id: real token checks against a test key, the feed from a snapshot.

The access tokens are RS256 ``at+jwt`` signed with a key generated here; pacod-id's JWKS is
served by the HTTP transport of the identity client, so the vendored JwtVerifier runs as in
production. The copy of pacod-id's bindings is loaded from a snapshot (``Copy.of``).
"""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pacod_id.cache import Copy
from pacod_id.client import IdConfig

import app.services.identity as identity_mod
from app.core.exceptions import AuthenticationError, IdentityUnavailableError
from app.guards.auth import AuthGuard

ISSUER = "http://id.test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update(kid="k1", use="sig", alg="RS256")

ADMIN, SUPER, PLAIN, WEBUI = "usr_admin", "usr_super", "usr_plain", "sa_webui"
SNAPSHOT = {
    "seq": 7,
    "bindings": [
        {"id": "b1", "principal": ADMIN, "role": "pacod.admin", "path": ["platform"]},
        # A superuser without a separate admin binding still reaches the console.
        {"id": "b2", "principal": SUPER, "role": "pacod.superuser", "path": ["platform"]},
        {"id": "b3", "principal": WEBUI, "role": "pacod.webui_service", "path": ["platform"]},
    ],
    "disabled": ["usr_disabled"],
    "deleted": [],
    "revoked_sids": [{"sid": "sid_ended"}],
}


def token(sub: str, *, sid: str = "sid_1", aud: str = "pacod-api", **extra) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + 300,
        "sid": sid,
        "pid_kind": "user",
        "client_id": "pacod-web",
        "scope": "openid email profile",
        "email": f"{sub}@example.com",
        "name": sub,
        **extra,
    }
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1", "typ": "at+jwt"})


@pytest.fixture
def ident(monkeypatch):
    """An Identity whose pacod-id answers JWKS and introspection from here."""
    introspected: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oidc/jwks":
            return httpx.Response(200, json={"keys": [JWK]})
        if request.url.path == "/oidc/token":
            return httpx.Response(200, json={"access_token": "svc", "expires_in": 300})
        if request.url.path == "/internal/v1/introspect":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            return httpx.Response(200, json=introspected.get(form["token"], {"active": False}))
        return httpx.Response(404)

    config = IdConfig((ISSUER,), "http://id.internal", "pacod-api", "secret", "pacod", "pacod-api")
    ident = identity_mod.Identity(config)
    ident.client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ident.cache._store(Copy.of(SNAPSHOT))
    ident.introspected = introspected
    made: list[tuple] = []

    async def ensure_user(sub, email, name):
        made.append((sub, email, name))
        return True

    async def by_email(email):
        return {"person@example.com": "usr_person", "off@example.com": "usr_disabled"}.get(email)

    async def no_activity(*a, **k):
        return None

    monkeypatch.setattr(ident, "ensure_user", ensure_user)
    monkeypatch.setattr(ident, "user_id_by_email", by_email)
    monkeypatch.setattr("app.services.user_activity.record_activity", no_activity)
    monkeypatch.setattr(identity_mod, "_identity", ident)
    ident.made = made
    return ident


guard = AuthGuard(dev_mode=False)


async def test_roles_come_from_permissions_not_binding_names(ident):
    admin = await guard.authenticate(f"Bearer {token(ADMIN)}")
    assert admin.roles == ("user", "admin") and admin.is_admin() and not admin.is_superuser()
    sup = await guard.authenticate(f"Bearer {token(SUPER)}")
    assert set(sup.roles) == {"user", "admin", "superuser"}
    plain = await guard.authenticate(f"Bearer {token(PLAIN)}")
    assert plain.roles == ("user",)
    assert plain.session_id == "sid_1" and plain.email == f"{PLAIN}@example.com"


async def test_the_local_row_is_made_from_the_verified_claims(ident):
    await guard.authenticate(f"Bearer {token(PLAIN)}")
    assert ident.made == [(PLAIN, f"{PLAIN}@example.com", PLAIN)]


async def test_an_ended_session_or_a_disabled_account_is_refused(ident):
    with pytest.raises(AuthenticationError, match="session"):
        await guard.authenticate(f"Bearer {token(PLAIN, sid='sid_ended')}")
    with pytest.raises(AuthenticationError, match="disabled"):
        await guard.authenticate(f"Bearer {token('usr_disabled')}")


async def test_a_token_for_another_audience_or_issuer_is_not_taken(ident):
    with pytest.raises(AuthenticationError):
        await guard.authenticate(f"Bearer {token(ADMIN, aud='pacod-gw')}")
    with pytest.raises(AuthenticationError):
        await guard.authenticate(f"Bearer {token(ADMIN, iss='http://evil.test')}")


async def test_unreadable_keys_are_503_not_401(ident):
    ident.client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    ident.verifier._keys = {}
    ident.verifier._read_at = None
    with pytest.raises(IdentityUnavailableError):
        await guard.authenticate(f"Bearer {token(ADMIN)}")


async def test_the_webui_service_may_name_the_person_and_no_one_else(ident):
    ident.introspected["pidt_webui"] = {"active": True, "sub": WEBUI, "pid_kind": "service"}
    ident.introspected["pidt_other"] = {"active": True, "sub": "sa_other", "pid_kind": "service"}
    forwarded = {"X-OpenWebUI-User-Email": "person@example.com"}
    person = await guard.authenticate("Bearer pidt_webui", forwarded)
    assert person.user_id == "usr_person" and person.roles == ("user",)
    # Another service account's forwarded header is ignored: the call stays its own.
    other = await guard.authenticate("Bearer pidt_other", forwarded)
    assert other.user_id == "sa_other" and not other.is_admin()
    with pytest.raises(AuthenticationError):
        await guard.authenticate("Bearer pidt_revoked")
    with pytest.raises(AuthenticationError, match="forwarded address"):
        await guard.authenticate("Bearer pidt_webui", {"X-OpenWebUI-User-Email": "nobody@x.y"})
    # A person disabled at pacod-id is refused through Open-WebUI too.
    with pytest.raises(AuthenticationError, match="disabled"):
        await guard.authenticate("Bearer pidt_webui", {"X-OpenWebUI-User-Email": "off@example.com"})
    # Without a person forwarded the call is the service account's own (its model list).
    own = await guard.authenticate("Bearer pidt_webui")
    assert own.user_id == WEBUI and "webui_service" in own.roles and not own.is_admin()


async def test_a_dev_token_never_asks_pacod_id(monkeypatch):
    class Down:
        def __getattr__(self, name):
            raise AssertionError("a dev token must not reach pacod-id")

    monkeypatch.setattr(identity_mod, "_identity", Down())
    user = await AuthGuard(dev_mode=True).authenticate("Bearer dev-token-alice")
    assert user.user_id == "alice" and user.is_superuser()


async def test_without_pacod_id_only_the_former_tokens_are_checked(monkeypatch):
    monkeypatch.setattr(identity_mod, "_identity", None)
    with pytest.raises(AuthenticationError):
        await guard.authenticate("Bearer not.a.token")
