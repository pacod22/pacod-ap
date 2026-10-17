"""Bearer-token authentication — the one place a caller becomes an ``AuthenticatedUser``.

Accepted, in this order:

  1. ``dev-token-<user_id>`` — development only (``APP_ENV=development`` and
     ``AUTH_ALLOW_DEV_TOKENS=true``, checked by the callers). Answered before anything talks
     to pacod-id, so local admin pages and the smoke tests work with pacod-id down.
  2. ``pidt_…`` — a pacod-id service account's token, introspected (cached briefly). Only the
     Open-WebUI service account (role ``pacod.webui_service``, permission
     ``attribution.forward``) may name the person behind its call, through Open-WebUI's
     forwarded ``X-OpenWebUI-User-Email`` header; the call is then that person's.
  3. A pacod-id access token (JWT, ``aud=pacod-api``): signature and issuer checked, then the
     copy of pacod-id's feed — an ended session or a disabled account is refused (401)
     within seconds. The person's local row is made on first sight (``ensure_user``) and
     their roles are derived from their bindings.

The guard contract is the same either way: return AuthenticatedUser or raise.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.core.exceptions import AuthenticationError, IdentityUnavailableError
from app.core.logging import get_logger
from app.core.request_context import note_user

log = get_logger(__name__)

# Open-WebUI's forwarded user headers (ENABLE_FORWARD_USER_INFO_HEADERS=true).
FORWARDED_EMAIL = "x-openwebui-user-email"
FORWARDED_NAME = "x-openwebui-user-name"


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    # The pacod-id SSO session of the access token (its `sid`); None for dev and service tokens.
    session_id: str | None = None
    # The verified pacod-id access token itself, for the admin facades' token exchange
    # (admin_api/services/identity_admin.py). Never logged or compared.
    access_token: str | None = field(default=None, repr=False, compare=False)

    def is_admin(self) -> bool:
        return "admin" in self.roles

    def is_superuser(self) -> bool:
        """Superusers may edit agent phase prompts; regular admins are read-only."""
        return "superuser" in self.roles


def _looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2


class AuthGuard:
    """Pluggable auth. Dev mode accepts dev-token-<user_id>."""

    def __init__(self, dev_mode: bool = True) -> None:
        self.dev_mode = dev_mode

    async def authenticate(
        self, authorization_header: str | None, headers: Mapping[str, str] | None = None
    ) -> AuthenticatedUser:
        if not authorization_header or not authorization_header.lower().startswith("bearer "):
            raise AuthenticationError("Missing or malformed Authorization header")

        token = authorization_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthenticationError("Empty bearer token")

        if self.dev_mode and token.startswith("dev-token-"):
            user_id = token[len("dev-token-") :]
            if not user_id:
                raise AuthenticationError("Dev token missing user_id suffix")
            # Dev tokens are implicitly admin + superuser so that all admin_api
            # routes (including prompt editing) work in dev.
            note_user(user_id)
            return AuthenticatedUser(
                user_id=user_id,
                email=f"{user_id}@example.local",
                roles=("admin", "superuser", "user"),
            )

        from app.services.identity import get_identity

        identity = get_identity()
        if identity is not None and token.startswith("pidt_"):
            return await self._service_token(identity, token, headers or {})
        if identity is not None and _looks_like_jwt(token):
            user = await self._pacod_id_token(identity, token)
            if user is not None:
                return user
        raise AuthenticationError("The token is not valid here")

    async def _service_token(self, identity, token: str, headers: Mapping[str, str]):
        from pacod_id.client import ApiError

        try:
            answer = await identity.introspect(token)
        except ApiError as e:
            raise IdentityUnavailableError(f"pacod-id cannot check the token: {e}") from e
        if answer is None or answer.get("pid_kind") != "service":
            raise AuthenticationError("The service token is not valid here")
        sub = str(answer["sub"])
        if identity.is_disabled(sub):
            raise AuthenticationError("The service account is disabled")
        forwarded = {k.lower(): v for k, v in headers.items()}
        email = (forwarded.get(FORWARDED_EMAIL) or "").strip()
        if email and identity.allowed(sub, "pacod.attribution.forward", kind="service"):
            # Open-WebUI acting for a person: the call is theirs (their quota, their history).
            # Its own user id is Open-WebUI's, not pacod-id's — the person is found by address.
            user_id = await identity.user_id_by_email(email)
            if user_id is None:
                raise AuthenticationError("No account with the forwarded address")
            if identity.is_disabled(user_id):
                raise AuthenticationError("The account is disabled")
            note_user(user_id)
            return AuthenticatedUser(user_id=user_id, email=email, roles=("user",))
        note_user(sub)
        return AuthenticatedUser(user_id=sub, roles=identity.roles_for(sub, kind="service"))

    async def _pacod_id_token(self, identity, token: str) -> AuthenticatedUser | None:
        from pacod_id.verify import InvalidToken, KeysUnavailable

        try:
            claims = await identity.verify(token)
        except KeysUnavailable as e:
            raise IdentityUnavailableError("pacod-id's keys cannot be read") from e
        except InvalidToken:
            return None
        if identity.session_ended(claims):
            raise AuthenticationError("The session has ended")
        if identity.is_disabled(claims.sub):
            raise AuthenticationError("The account is disabled")
        try:
            await identity.ensure_user(claims.sub, claims.email, claims.name)
        except Exception as e:  # a DB fault must not read as "unauthenticated"
            log.warning("identity_ensure_user_failed", user_id=claims.sub, error=str(e))
        if identity.first_sight_of_session(claims.sid):
            # A sign-in, as this API sees it: the login activity and users.last_login_at.
            from app.services.user_activity import record_activity

            await record_activity(claims.sub, "login", detail={"client": claims.client_id})
            await identity.note_sign_in(claims.sub)
        note_user(claims.sub)
        return AuthenticatedUser(
            user_id=claims.sub,
            email=claims.email,
            roles=identity.roles_for(claims.sub),
            session_id=claims.sid,
            access_token=token,
        )
