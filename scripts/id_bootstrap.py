#!/usr/bin/env python3
"""Bootstrap the platform's own accounts at pacod-id.

    python3 -I scripts/id_bootstrap.py [--public-url …] [--issuer …] [--internal-url …]
                                       [--app-url …]               (make id-bootstrap)

Today: Open-WebUI's service account. Open-WebUI calls the app's OpenAI-compatible API with
one token and forwards the person in X-OpenWebUI-User-* headers; the API believes those
headers only from an account holding ``pacod.webui_service`` (permission
``attribution.forward``). This makes the service account ``pacod-webui`` with that role and
a ``pidt_`` token, written to pacod-api/.env.gateway as PACOD_WEBUI_SERVICE_TOKEN (0600),
which compose hands to Open-WebUI as its OPENAI_API_KEY. Idempotent: a stored token that
pacod-id still takes is kept.

It acts as the bootstrap admin (PID_BOOTSTRAP_ADMIN_* in deploy/secrets/id.env): a browserless
sign-in as the app's own client pacod-web (its secret is in id.env too), then the token
exchanged — as the API's client pacod-api — for a pacod-id admin token, exactly as the admin
console's facades do. Local defaults: the issuer http://127.0.0.1:8070 and the internal
listener http://127.0.0.1:8071 (docker-compose.local.yml publishes it on loopback only).
Standard library only; prints names, never a secret.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gw_bootstrap import GATEWAY_ENV, ID_ENV, fail, read_env, write_env  # noqa: E402

SERVICE_ACCOUNT = "pacod-webui"
ROLE = "pacod.webui_service"
# The app as the browser reaches it: pacod-web's registered callback is <app>/auth/callback.
DEFAULT_APP_URL = "http://localhost:8080"


def say(msg: str) -> None:
    print(f"id-bootstrap: {msg}", flush=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def _basic(client_id: str, secret: str) -> str:
    pair = f"{urllib.parse.quote(client_id, safe='')}:{urllib.parse.quote(secret, safe='')}"
    return "Basic " + base64.b64encode(pair.encode()).decode()


class Http:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def go(self, method: str, url: str, *, body=None, form=None, headers=None):
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            r = self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as e:
            r = e
        except urllib.error.URLError as e:
            fail(f"{method} {url}: {e.reason}")
        status = getattr(r, "status", None) or r.code
        raw = r.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return status, parsed, r.headers


def sign_in(
    h: Http,
    public_url: str,
    issuer: str,
    email: str,
    password: str,
    web_secret: str,
    redirect_uri: str,
) -> str:
    """An access token of the bootstrap admin for the app (client pacod-web, code flow + PKCE)."""
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    state = secrets.token_hex(16)
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": "pacod-web",
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": secrets.token_hex(8),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    _, _, headers = h.go("GET", f"{public_url}/oidc/authorize?{q}")
    to = urllib.parse.urljoin(public_url, headers.get("Location", ""))
    flow = urllib.parse.parse_qs(urllib.parse.urlparse(to).query).get("flow", [None])[0]
    if not flow:
        fail(f"pacod-id at {public_url} did not start a sign-in")
    status, step, _ = h.go(
        "POST",
        f"{public_url}/ui-api/v1/flows/{flow}/password",
        body={"email": email, "password": password},
        headers={"Origin": issuer},
    )
    if not isinstance(step, dict) or step.get("next") != "redirect":
        fail(f"signing in as the bootstrap admin failed ({status}): {step}")
    back = urllib.parse.parse_qs(urllib.parse.urlparse(step["to"]).query)
    if back.get("state", [None])[0] != state:
        fail("pacod-id answered the sign-in with another state")
    status, tokens, _ = h.go(
        "POST",
        f"{public_url}/oidc/token",
        form={
            "grant_type": "authorization_code",
            "code": back["code"][0],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        headers={"Authorization": _basic("pacod-web", web_secret)},
    )
    if not isinstance(tokens, dict) or "access_token" not in tokens:
        fail(f"pacod-id's token endpoint answered {status}")
    return tokens["access_token"]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="id_bootstrap.py", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--public-url", default="http://127.0.0.1:8070", help="pacod-id's public listener"
    )
    p.add_argument("--issuer", help="the realm's issuer (default: --public-url)")
    p.add_argument(
        "--app-url",
        default=DEFAULT_APP_URL,
        help="the app's public origin; pacod-web's callback is <app-url>/auth/callback",
    )
    p.add_argument(
        "--internal-url", default="http://127.0.0.1:8071", help="pacod-id's internal listener"
    )
    a = p.parse_args(argv)
    issuer = (a.issuer or a.public_url).rstrip("/")
    public, internal = a.public_url.rstrip("/"), a.internal_url.rstrip("/")
    ident = read_env(ID_ENV)
    need = (
        "PID_BOOTSTRAP_ADMIN_EMAIL",
        "PID_BOOTSTRAP_ADMIN_PASSWORD",
        "PACOD_WEB_CLIENT_SECRET",
        "PACOD_API_CLIENT_SECRET",
    )
    missing = [n for n in need if not ident.get(n)]
    if missing:
        fail(f"{', '.join(missing)} missing in deploy/secrets/id.env: run make secrets-init")
    h = Http()
    api_basic = _basic("pacod-api", ident["PACOD_API_CLIENT_SECRET"])

    # A token pacod-id still takes is kept (introspected as the API's client).
    stored = read_env(GATEWAY_ENV).get("PACOD_WEBUI_SERVICE_TOKEN")
    if stored:
        _, svc, _ = h.go(
            "POST",
            f"{internal}/oidc/token",
            form={"grant_type": "client_credentials"},
            headers={"Authorization": api_basic},
        )
        status, answer, _ = h.go(
            "POST",
            f"{internal}/internal/v1/introspect",
            form={"token": stored},
            headers={"Authorization": f"Bearer {svc['access_token']}"},
        )
        if status == 200 and isinstance(answer, dict) and answer.get("active"):
            say(f"{SERVICE_ACCOUNT}: PACOD_WEBUI_SERVICE_TOKEN works")
            return
        say(f"{SERVICE_ACCOUNT}: the stored token is not active ({status}); making a new one")

    user_token = sign_in(
        h,
        public,
        issuer,
        ident["PID_BOOTSTRAP_ADMIN_EMAIL"],
        ident["PID_BOOTSTRAP_ADMIN_PASSWORD"],
        ident["PACOD_WEB_CLIENT_SECRET"],
        a.app_url.rstrip("/") + "/auth/callback",
    )
    status, exchanged, _ = h.go(
        "POST",
        f"{internal}/oidc/token",
        form={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": user_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": "pacod-id",
        },
        headers={"Authorization": api_basic},
    )
    if not isinstance(exchanged, dict) or "access_token" not in exchanged:
        fail(f"the token exchange answered {status}: {exchanged}")
    admin = {"Authorization": f"Bearer {exchanged['access_token']}"}

    def call(method: str, path: str, body=None, ok=(200, 201, 204)):
        status, data, _ = h.go(method, f"{internal}/admin/v1{path}", body=body, headers=admin)
        if status not in ok:
            fail(f"{method} {path} answered {status}: {data}")
        return data

    listed = call("GET", "/service-accounts")
    items = (
        listed.get("service_accounts") or listed.get("items") or []
        if isinstance(listed, dict)
        else listed
    )
    account = next((s for s in items if s.get("name") == SERVICE_ACCOUNT), None)
    if account is None:
        account = call("POST", "/service-accounts", {"name": SERVICE_ACCOUNT})
        say(f"{SERVICE_ACCOUNT}: service account made ({account['id']})")
    bindings = call("GET", f"/bindings?principal={account['id']}")
    held = (
        bindings.get("bindings") or bindings.get("items") or []
        if isinstance(bindings, dict)
        else bindings
    )
    if not any(b.get("role") == ROLE for b in held):
        call("POST", "/bindings", {"principal": account["id"], "role": ROLE, "scope": "platform"})
        say(f"{SERVICE_ACCOUNT}: bound to {ROLE}")
    made = call("POST", f"/service-accounts/{account['id']}/tokens", {})
    write_env(GATEWAY_ENV, {"PACOD_WEBUI_SERVICE_TOKEN": made["token"]})
    say(
        f"{SERVICE_ACCOUNT}: token made, stored as PACOD_WEBUI_SERVICE_TOKEN in pacod-api/.env.gateway"
    )


if __name__ == "__main__":
    main()
