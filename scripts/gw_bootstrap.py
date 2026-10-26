#!/usr/bin/env python3
"""Bootstrap the platform's gateway (pacod-gw) for pacod-ap.

    python3 -I scripts/gw_bootstrap.py [--env dev|live]          (make gw-bootstrap)
    python3 -I scripts/gw_bootstrap.py --env dev --author        (re-author the dev config)

Standard library only, so it runs on any host with Python 3.11+ and no virtualenv. Idempotent:
every step checks what is there first. It prints names and ids, never a secret.

Where it runs: development on this host, which reaches the development gateway on its published
ports and pacod-id's issuer on 127.0.0.1:8070. A server publishes neither the data plane nor the
management API, which listens on pacod-admin-net only, so `make gw-bootstrap GW_ENV=live` runs
this script in a one-shot container on pacod-net and pacod-admin-net, where the live defaults
below are the gateway's names.

What it does, in order:

1. An admin credential for the management API. GW_ADMIN_TOKEN from pacod-api/.env.gateway when
   it still works. Otherwise, with --env dev only, it signs in at pacod-id as the bootstrap
   admin (PID_BOOTSTRAP_ADMIN_* in deploy/secrets/id.env) through the development client
   gw-smoke, and makes a platform-admin token (a pacod-id service account, `pidt_…`) for the
   next runs. With --env live, create one in the console (Platform, Access, Admin tokens; role
   platform_admin) and store it: make secret-set FILE=pacod-api/.env.gateway NAME=GW_ADMIN_TOKEN
2. The configuration: deploy/pacod-gw/platform-config.<env>.json through
   POST /system/config-import, as a dry run first; nothing is applied if anything is refused.
   Objects keep their ids, so a second import updates in place and never deletes.
3. Provider credentials, which an export never holds: the mock's fixed development secret, or
   each provider's key from deploy/secrets/providers.env (OPENAI_API_KEY, ANTHROPIC_API_KEY,
   GEMINI_API_KEY, DEEPSEEK_API_KEY, GLM_API_KEY). A provider that already has a current
   credential keeps it unless --rotate-credentials.
4. One gateway key (`pgw_…`) per service that calls the gateway, in the workspace pacod/pacod,
   checked with GET /v1/models and issued again when it no longer works. The workers' keys get
   a daily spend cap (a workspace limit on the key). Written to pacod-api/.env.gateway.
5. admin-api's management tokens, admin tokens with one role on the platform workspace:
   GATEWAY_REPORT_TOKEN (viewer: usage reports) and GATEWAY_ADMIN_TOKEN (workspace_admin:
   per-user quota overrides); and the workspace's ids, GATEWAY_ORG_ID / GATEWAY_WORKSPACE_ID.

--author converges the development gateway on the platform's objects through the API (one
mock provider per family, made from the family's preset; pacod's model ids with their live
capability presets and prices; organisation and workspace; grants; limits) and writes them to
platform-config.dev.json. --author-live makes platform-config.live.json from the same models
on the real providers. Both write only the platform's objects, never another organisation's or
gw-smoke's leftovers. That is how the two files were made.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookies
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_ENV = ROOT / "pacod-api" / ".env.gateway"
ID_ENV = ROOT / "deploy" / "secrets" / "id.env"
PROVIDERS_ENV = ROOT / "deploy" / "secrets" / "providers.env"
CONFIG = ROOT / "deploy" / "pacod-gw" / "platform-config.{env}.json"

DEFAULTS = {
    # The management API, the data plane and the issuer as this host reaches them.
    "dev": {
        "admin_url": "http://127.0.0.1:18081",
        "data_url": "http://127.0.0.1:18080",
        "issuer": "http://127.0.0.1:8070",
    },
    # As a container on pacod-net and pacod-admin-net reaches them (make gw-bootstrap runs it in
    # one). Live never signs in: it uses the stored GW_ADMIN_TOKEN.
    "live": {
        "admin_url": "http://pacod-gw-admin:8081",
        "data_url": "http://pacod-gw:8080",
        "issuer": None,
    },
}

ORG = "pacod"
WORKSPACE = "pacod"
# What a call carries in gateway.tags (plan, Phase 4): the axes of the app's own analytics.
TAG_KEYS = ["purpose", "site", "profile", "variant"]

# The services that call the gateway, the variable each one's key is stored under, and a
# daily spend cap in USD for the unattended ones (None: no cap; the users' limits apply).
SERVICES = [
    ("pacod-api", "GATEWAY_KEY_API", None),
    ("pacod-admin-api", "GATEWAY_KEY_ADMIN_API", None),
    ("pacod-loop", "GATEWAY_KEY_LOOP", 20.0),
    ("pacod-newsletter", "GATEWAY_KEY_NEWSLETTER", 5.0),
]

# Provider name -> where its credential comes from. The mock accepts any secret.
MOCK_SECRET = "mock-provider-development-secret"
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "glm": "GLM_API_KEY",
}

# The id the development client gw-smoke signs in with (pacod-id.local.toml).
SMOKE_CLIENT = "gw-smoke"
SMOKE_REDIRECT = "http://127.0.0.1:13999/callback"


def say(msg: str) -> None:
    print(f"gw-bootstrap: {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"gw-bootstrap: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---- env files ---------------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Replace or append NAME=value lines, keeping every other line; 0600."""
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines, done = [], set()
    for line in old:
        name = (
            line.split("=", 1)[0].strip()
            if "=" in line and not line.lstrip().startswith("#")
            else None
        )
        if name in updates:
            lines.append(f"{name}={updates[name]}")
            done.add(name)
        else:
            lines.append(line)
    if not old:
        lines.append(
            "# The platform's gateway credentials (scripts/gw_bootstrap.py). Never commit."
        )
    lines += [f"{n}={v}" for n, v in updates.items() if n not in done]
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# ---- HTTP ---------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def request(method: str, url: str, *, token: str | None = None, body=None, form=None, headers=None):
    """(status, parsed body or text, response headers). Never raises on an HTTP status."""
    data, hdrs = None, dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _opener.open(req, timeout=60) as r:
            status, raw, rh = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, raw, rh = e.code, e.read(), e.headers
    except urllib.error.URLError as e:
        fail(f"{method} {url}: {e.reason}")
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = text
    return status, parsed, rh


class Api:
    """The management API, /api/v1, as one principal."""

    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/") + "/api/v1"
        self.token = token

    def call(self, method: str, path: str, body=None, ok=(200, 201, 204)):
        status, data, _ = request(method, self.base + path, token=self.token, body=body)
        if status not in ok:
            fail(f"{method} {path} answered {status}: {short(data)}")
        return data

    def try_call(self, method: str, path: str, body=None):
        status, data, _ = request(method, self.base + path, token=self.token, body=body)
        return status, data

    def items(self, path: str) -> list[dict]:
        data = self.call("GET", path)
        if isinstance(data, dict):
            for key in ("items", "data", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
        return data if isinstance(data, list) else []

    def find(self, path: str, name: str) -> dict | None:
        return next((o for o in self.items(path) if o.get("name") == name), None)


def probe(method: str, url: str, token: str) -> int:
    """The status a credential gets, waiting out the gateway's 503 (identity_unavailable: its copy
    of pacod-id's feed is stale, e.g. while pacod-id or its database restarts) for up to a minute.
    Only 401 and 403 mean the credential itself is refused."""
    import time

    for _ in range(30):
        status, _, _ = request(method, url, token=token)
        if status not in (502, 503, 504):
            return status
        time.sleep(2)
    fail(f"{url} keeps answering {status}: is the gateway (and pacod-id) up?")


def refused(status: int) -> bool:
    if status in (401, 403):
        return True
    if status != 200:
        fail(f"unexpected answer {status} while checking a stored credential; nothing changed")
    return False


def short(data) -> str:
    text = data if isinstance(data, str) else json.dumps(data)
    return text[:300]


# ---- signing in ---------------------------------------------------------------------------


def oidc_sign_in(issuer: str, email: str, password: str) -> str:
    """An access token for the bootstrap admin: the code flow of the public development client
    gw-smoke walked without a browser, as pacod-gw's gw-smoke does (testkit/src/oidc.rs)."""
    issuer = issuer.rstrip("/")
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    state = secrets.token_hex(16)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": SMOKE_CLIENT,
            "redirect_uri": SMOKE_REDIRECT,
            "scope": "openid email profile offline_access",
            "state": state,
            "nonce": secrets.token_hex(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    status, _, headers = request("GET", f"{issuer}/oidc/authorize?{query}")
    to = headers.get("Location", "")
    flow = urllib.parse.parse_qs(urllib.parse.urlparse(urllib.parse.urljoin(issuer, to)).query).get(
        "flow", [None]
    )[0]
    if not flow:
        fail(
            f"pacod-id at {issuer} did not start a sign-in ({status}); is the gw-smoke client configured?"
        )
    jar = http.cookies.SimpleCookie()
    for value in headers.get_all("Set-Cookie") or []:
        jar.load(value)
    cookie = "; ".join(f"{k}={m.value}" for k, m in jar.items())
    status, step, _ = request(
        "POST",
        f"{issuer}/ui-api/v1/flows/{flow}/password",
        body={"email": email, "password": password},
        # The pages' API answers its own origin only.
        headers={"Cookie": cookie, "Origin": issuer},
    )
    if not isinstance(step, dict) or step.get("next") != "redirect":
        message = step.get("error", {}).get("message") if isinstance(step, dict) else step
        fail(f"signing in as the bootstrap admin failed ({status}): {message}")
    back = urllib.parse.parse_qs(
        urllib.parse.urlparse(urllib.parse.urljoin(issuer, step["to"])).query
    )
    if back.get("state", [None])[0] != state:
        fail("pacod-id answered the sign-in with another state")
    code = back.get("code", [None])[0] or fail("pacod-id's redirect carries no code")
    status, tokens, _ = request(
        "POST",
        f"{issuer}/oidc/token",
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SMOKE_REDIRECT,
            "code_verifier": verifier,
            "client_id": SMOKE_CLIENT,
        },
    )
    if not isinstance(tokens, dict) or "access_token" not in tokens:
        fail(f"pacod-id's token endpoint answered {status}: {short(tokens)}")
    return tokens["access_token"]


def admin_api(env: str, admin_url: str, issuer: str | None) -> Api:
    stored = read_env(GATEWAY_ENV).get("GW_ADMIN_TOKEN")
    if stored:
        status = probe("GET", f"{admin_url.rstrip('/')}/api/v1/system/status", stored)
        if not refused(status):
            say("admin: GW_ADMIN_TOKEN works")
            return Api(admin_url, stored)
        say(f"admin: GW_ADMIN_TOKEN is refused ({status}); making a new one")
    if env != "dev":
        fail(
            "no working GW_ADMIN_TOKEN. Create an admin token with the role platform_admin in the "
            "console (Platform, Access, Admin tokens) and store it: "
            "make secret-set FILE=pacod-api/.env.gateway NAME=GW_ADMIN_TOKEN"
        )
    ident = read_env(ID_ENV)
    email, password = (
        ident.get("PID_BOOTSTRAP_ADMIN_EMAIL"),
        ident.get("PID_BOOTSTRAP_ADMIN_PASSWORD"),
    )
    if not (email and password):
        fail(
            f"PID_BOOTSTRAP_ADMIN_EMAIL/_PASSWORD are not in {ID_ENV.relative_to(ROOT)}: run make secrets-init"
        )
    session = Api(admin_url, oidc_sign_in(issuer, email, password))
    # The core takes a fresh session once its copy of pacod-id's feed has it.
    for _ in range(30):
        status, _ = session.try_call("GET", "/system/status")
        if status == 200:
            break
        import time

        time.sleep(1)
    else:
        fail(
            f"the core does not take the bootstrap admin's session ({status}); is it a platform admin?"
        )
    token = make_admin_token(session, "pacod-ap-bootstrap", "platform_admin", {"type": "platform"})
    write_env(GATEWAY_ENV, {"GW_ADMIN_TOKEN": token})
    say("admin: made a platform-admin token, stored as GW_ADMIN_TOKEN")
    return Api(admin_url, token)


def make_admin_token(api: Api, name: str, role: str, scope: dict) -> str:
    """A new admin token (a pacod-id service account) with one role binding; returns its
    secret. An existing token of the same name is deleted first: its secret is unknown."""
    old = api.find("/admin-tokens", name)
    if old:
        api.call("DELETE", f"/admin-tokens/{old['id']}")
    made = api.call("POST", "/admin-tokens", {"name": name})
    api.call("POST", "/role-bindings", {"principal": made["id"], "role": role, "scope": scope})
    return made["secret"]


# ---- the platform's configuration ---------------------------------------------------------

# pacod's model ids (the names the app sends; unchanged from the LiteLLM era), the provider
# they belong to, the provider's own id, the gateway's capability preset and the price in USD
# per million input/output tokens (pacod's former price seed, app/services/model_pricing.py).
MODELS = [
    (
        "anthropic/claude-sonnet-5-5",
        "anthropic",
        "claude-sonnet-5-5",
        "anthropic-sonnet-5-5",
        2.0,
        10.0,
    ),
    ("anthropic/claude-opus-5", "anthropic", "claude-opus-5", "anthropic-adaptive", 5.0, 25.0),
    ("anthropic/claude-haiku-4-5", "anthropic", "claude-haiku-4-5", "anthropic-budget", 1.0, 5.0),
    ("openai/gpt-5.6-sol", "openai", "gpt-5.6-sol", "openai-reasoning", 4.0, 20.0),
    ("openai/gpt-5.6-luna", "openai", "gpt-5.6-luna", "openai-reasoning", 0.2, 1.2),
    ("gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview", "gemini-3-pro", 2.0, 12.0),
    ("gemini-3.8-flash", "gemini", "gemini-3.8-flash", "gemini-3-flash", 0.75, 3.75),
    ("gemini-3.5-flash-lite", "gemini", "gemini-3.5-flash-lite", "gemini-3-flash-lite", 0.3, 2.5),
    ("deepseek/deepseek-flash", "deepseek", "deepseek-flash", "deepseek", 0.3, 1.2),
    ("deepseek/deepseek-v4-pro", "deepseek", "deepseek-v4-pro", "deepseek", 1.32, 3.96),
    ("glm/glm-5.3", "glm", "glm-5.3", "glm-5", 1.4, 4.4),
    ("glm/glm-4.7", "glm", "glm-4.7", "glm-4", 0.6, 2.2),
    ("glm/glm-4.5-air", "glm", "glm-4.5-air", "glm-4", 0.2, 1.1),
]
# The scripted provider of pacod-gw's development stack (gw-mock), one face per family: the
# Anthropic and OpenAI APIs and its emulations of Gemini, DeepSeek and GLM. Each mock provider
# is made from the family's own preset, and every model keeps its live capability preset, so
# the gateway filters and translates the app's requests in development exactly as in
# production; only the base URLs (and, for Anthropic and OpenAI, the mock's model names) differ.
MOCK_BASE = "http://gw-mock:9000"
MOCK_PROVIDERS = {
    "mock-anthropic": ("anthropic", ""),
    "mock-openai": ("openai", "/v1"),
    "mock-gemini": ("gemini", "/emulate/gemini/v1beta"),
    "mock-deepseek": ("deepseek", "/emulate/deepseek"),
    "mock-glm": ("glm", "/emulate/glm/api/paas/v4"),
}
MOCK_UPSTREAM = {"anthropic": "claude-mock", "openai": "gpt-mock"}
# No call may be cut by the gateway before the app gives up on it (LLM_TIMEOUT_SECONDS 180,
# LOOP_LLM_TIMEOUT_SECONDS 600): the app is the one that decides, and never retries a timeout.
PROVIDER_TIMEOUT_SECONDS = 600
# The users' daily token budget (the app's AGENT_PER_USER_DAILY_TOKEN_LIMIT), advisory: the app
# reads it with GET /gw/v1/quota and answers 429 itself. Workers send no end user, so it counts
# only people's calls.
USER_DAILY_TOKENS = 100_000
# The workspace's monthly spend, advisory, with alerts at 50 %, 80 % and 100 %.
MONTHLY_SPEND_USD = 500.0


def provider_body(presets: dict, family: str, name: str, base_url: str | None = None) -> dict:
    pre = presets[family]
    body = {
        k: pre[k]
        for k in ("family", "base_url", "quirks", "auth", "managed_api", "platform")
        if k in pre
    }
    body.update(
        name=name,
        display_name=pre.get("display_name", name),
        default_timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
    )
    if base_url:
        body["base_url"] = base_url
    return body


def converge_models(api: Api, org_id: str, provider_ids: dict[str, str], upstream_for) -> None:
    """Every model of MODELS on its family's provider, with its price and live preset, active
    and granted to the organisation. Existing models are changed in place (same id)."""
    by_name = {m["name"]: m for m in api.items("/models")}
    granted = {g.get("model_id") for g in api.items(f"/orgs/{org_id}/grants")}
    for name, family, upstream, preset, inp, out in MODELS:
        want = {
            "name": name,
            "provider_id": provider_ids[family],
            "upstream_id": upstream_for(family, upstream),
            "capability_preset": preset,
        }
        model = by_name.get(name)
        if model is None:
            model = api.call("POST", "/models", want)
        else:
            # A replace keeps the id; the preset is applied again (it is not echoed back).
            model = api.call("PUT", f"/models/{model['id']}", {**want, "version": model["version"]})
        price = {"currency": "USD", "input": inp, "output": out}
        if family in ("anthropic", "openai"):
            price["cache_read"] = round(inp * 0.1, 4)
        if family == "anthropic":
            price["cache_write_5m"] = round(inp * 1.25, 4)
        current = api.try_call("GET", f"/models/{model['id']}/prices")[1]
        latest = (current or [{}])[-1] if isinstance(current, list) else {}
        if any(latest.get(k) != v for k, v in price.items()):
            api.call("PUT", f"/models/{model['id']}/price", price)
        if api.call("GET", f"/models/{model['id']}").get("state") != "active":
            api.call("POST", f"/models/{model['id']}/transition", {"to": "active"})
        if model["id"] not in granted:
            api.call("POST", f"/orgs/{org_id}/grants", {"model_id": model["id"]})
        say(f"models: {name} -> {family} ({want['upstream_id']}, {preset})")


def author_dev(api: Api, path: Path) -> None:
    """Converge the development gateway on the platform's objects, then export them. Safe to run
    again: what exists is changed in place, ids are kept."""
    presets = {p["name"]: p for p in api.items("/provider-presets")}
    providers = {}
    for name, (family, path_) in MOCK_PROVIDERS.items():
        found = api.find("/providers", name)
        if found is None:
            found = api.call(
                "POST", "/providers", provider_body(presets, family, name, MOCK_BASE + path_)
            )
        else:
            api.call(
                "PUT",
                f"/providers/{found['id']}",
                {
                    **provider_body(presets, family, name, MOCK_BASE + path_),
                    "version": found["version"],
                },
            )
        creds = api.items(f"/providers/{found['id']}/credentials")
        if not creds:
            api.call(
                "POST",
                f"/providers/{found['id']}/credentials",
                {"label": "development", "secret": MOCK_SECRET},
            )
        providers[family] = found["id"]
        say(f"author: provider {name} ({family}, {MOCK_BASE}{path_})")
    org = api.find("/orgs", ORG) or api.call(
        "POST", "/orgs", {"name": ORG, "display_name": "PACOD platform"}
    )
    ws_body = {
        "name": WORKSPACE,
        "display_name": "PACOD agents",
        "tag_keys": TAG_KEYS,
        # Anthropic's native web search, which the agents' synthesis may use.
        "settings": {"provider_tools": ["web_search"]},
    }
    ws = api.find(f"/orgs/{org['id']}/workspaces", WORKSPACE) or api.call(
        "POST", f"/orgs/{org['id']}/workspaces", ws_body
    )
    say(f"author: organisation {ORG}, workspace {WORKSPACE} (tag keys {', '.join(TAG_KEYS)})")
    converge_models(
        api, org["id"], providers, lambda family, upstream: MOCK_UPSTREAM.get(family, upstream)
    )
    limits = f"/orgs/{org['id']}/workspaces/{ws['id']}/limits"
    have = {lim["name"] for lim in api.items(limits)}
    for spec in PLATFORM_LIMITS:
        if spec["name"] not in have:
            api.call("POST", limits, spec)
    say("author: limits " + ", ".join(spec["name"] for spec in PLATFORM_LIMITS))
    # Mock providers no model uses any more (an earlier layout) are left out of the export.
    export(api, path)


PLATFORM_LIMITS = [
    {
        "name": "user-daily-tokens",
        "description": "Each person's daily token budget; the app enforces it.",
        "subject": "end_user",
        "metric": "tokens",
        "window": "day",
        "amount": USER_DAILY_TOKENS,
        "enforcement": "advisory",
    },
    {
        "name": "workspace-monthly-spend",
        "description": "The platform's monthly LLM spend.",
        "subject": "workspace",
        "metric": "spend",
        "window": "month",
        "amount": MONTHLY_SPEND_USD,
        "enforcement": "advisory",
        "alert_thresholds": [50, 80, 100],
    },
]


# The live providers, as pacod-gw's presets describe them (GET /provider-presets).
LIVE_PROVIDERS = ["anthropic", "openai", "gemini", "deepseek", "glm"]


def author_live(api: Api, dev_path: Path, live_path: Path) -> None:
    """The live configuration, made on the development gateway and then undone there: the same
    models (same ids) moved onto the real providers, exported, and the development file imported
    again. The real providers get no credential here and are deleted afterwards."""
    import_config(api, dev_path)
    presets = {p["name"]: p for p in api.items("/provider-presets")}
    providers = {}
    for family in LIVE_PROVIDERS:
        found = api.find("/providers", family)
        providers[family] = (
            found or api.call("POST", "/providers", provider_body(presets, family, family))
        )["id"]
    org, _ws = platform_ids(api)
    # Activation probes the provider, which has no credential here: activate without the probe.
    for name, family, upstream, preset, _inp, _out in MODELS:
        model = api.find("/models", name) or fail(
            f"model {name} is missing: import the dev configuration first"
        )
        api.call(
            "PUT",
            f"/models/{model['id']}",
            {
                "name": name,
                "provider_id": providers[family],
                "upstream_id": upstream,
                "capability_preset": preset,
                "version": model["version"],
            },
        )
        if api.call("GET", f"/models/{model['id']}").get("state") != "active":
            api.call(
                "POST", f"/models/{model['id']}/transition", {"to": "active", "skip_probe": True}
            )
        say(f"author-live: {name} -> {family} ({upstream}, {preset})")
    export(api, live_path, keep_providers=set(LIVE_PROVIDERS))
    # Back to the development configuration.
    api.call("POST", "/system/config-import", json.loads(dev_path.read_text(encoding="utf-8")))
    for pid in providers.values():
        api.call("DELETE", f"/providers/{pid}")
    say("author-live: the development gateway is back on the mock providers")


def export(api: Api, path: Path, keep_providers: set[str] | None = None) -> None:
    """Write the platform's objects of the gateway's configuration export to ``path``: the
    organisation and everything under it, the models of MODELS with their prices, and the
    providers those models use. Whatever else the gateway holds (gw-smoke's leftovers, other
    organisations) stays out."""
    doc = api.call("GET", "/system/config-export")
    objs = doc["objects"]
    org_ids = {o["id"] for o in objs if o["kind"] == "organisation" and o.get("name") == ORG}
    model_names = {m[0] for m in MODELS}
    models = {o["id"]: o for o in objs if o["kind"] == "model" and o.get("name") in model_names}
    provider_ids = {o["doc"].get("provider_id") for o in models.values()}
    keep = set(org_ids) | set(models) | provider_ids
    changed = True
    while changed:  # children of kept objects: workspaces, grants, limits, prices
        changed = False
        for o in objs:
            if o["id"] not in keep and o.get("parent") in keep:
                keep.add(o["id"])
                changed = True
    # The workers' spend caps name this installation's key ids: gw-bootstrap makes them.
    out = [
        o
        for o in objs
        if o["id"] in keep
        and not (o["kind"] == "limit" and str(o.get("name", "")).startswith("cap-"))
    ]
    if keep_providers is not None:
        out = [o for o in out if o["kind"] != "provider" or o.get("name") in keep_providers]
    doc["objects"] = out
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    say(
        f"wrote {path.relative_to(ROOT)} ({len(out)} objects, config version {doc['config_version']})"
    )


def import_config(api: Api, path: Path) -> None:
    if not path.exists():
        fail(f"{path.relative_to(ROOT)} is missing")
    doc = json.loads(path.read_text(encoding="utf-8"))
    dry = api.call("POST", "/system/config-import?dry_run=true", doc)
    counts = (
        {k: len(v) for k, v in dry.items() if isinstance(v, list)} if isinstance(dry, dict) else {}
    )
    say(f"config: dry run {counts}")
    refused = dry.get("refused") if isinstance(dry, dict) else None
    if refused:
        fail(f"config: the import would be refused: {short(refused)}")
    if isinstance(dry, dict) and not any(dry.get(k) for k in ("created", "updated")):
        say("config: already in place")
        return
    api.call("POST", "/system/config-import", doc)
    say(f"config: imported {path.relative_to(ROOT)}")


def ensure_credentials(api: Api, rotate: bool) -> None:
    keys = read_env(PROVIDERS_ENV)
    for provider in api.items("/providers"):
        name, pid = provider["name"], provider["id"]
        have = [c for c in api.items(f"/providers/{pid}/credentials") if c.get("current", True)]
        if have and not rotate:
            say(f"credentials: {name} has one")
            continue
        if name in MOCK_PROVIDERS:
            secret = MOCK_SECRET
        else:
            var = PROVIDER_KEYS.get(name) or PROVIDER_KEYS.get(provider.get("family", ""))
            secret = keys.get(var or "")
            if not secret:
                say(
                    f"credentials: {name} has none and {var or '(no variable)'} is not in providers.env; its calls fail until it has one"
                )
                continue
        api.call(
            "POST",
            f"/providers/{pid}/credentials",
            {"label": "pacod-ap", "secret": secret, "make_current": True},
        )
        say(f"credentials: {name} set")


def platform_ids(api: Api) -> tuple[str, str]:
    org = api.find("/orgs", ORG) or fail(
        f"no organisation {ORG!r}: the configuration is not imported"
    )
    ws = api.find(f"/orgs/{org['id']}/workspaces", WORKSPACE) or fail(f"no workspace {WORKSPACE!r}")
    return org["id"], ws["id"]


def ensure_keys(api: Api, data_url: str) -> None:
    org, ws = platform_ids(api)
    base = f"/orgs/{org}/workspaces/{ws}"
    stored = read_env(GATEWAY_ENV)
    updates = {}
    for name, var, cap in SERVICES:
        secret = stored.get(var)
        if secret:
            status = probe("GET", f"{data_url.rstrip('/')}/v1/models", secret)
            if not refused(status):
                say(f"keys: {name} works ({var})")
                key = api.find(f"{base}/keys", name)
                if key and cap is not None:
                    ensure_cap(api, base, name, key["id"], cap)
                continue
            say(f"keys: {name} is refused ({status}); issuing a new one")
        old = api.find(f"{base}/keys", name)
        if old:
            api.call("DELETE", f"{base}/keys/{old['id']}")
        made = api.call("POST", f"{base}/keys", {"name": name})
        updates[var] = made["secret"]
        say(f"keys: {name} issued ({var})")
        if cap is not None:
            ensure_cap(api, base, name, made["id"], cap)
    if updates:
        write_env(GATEWAY_ENV, updates)


def ensure_cap(api: Api, base: str, key_name: str, key_id: str, usd: float) -> None:
    name = f"cap-{key_name}"
    spec = {
        "name": name,
        "description": f"Daily spend cap of the {key_name} key.",
        "subject": "key",
        "metric": "spend",
        "window": "day",
        "amount": usd,
        "enforcement": "request",
        "filter": {"keys": [key_id]},
    }
    old = api.find(f"{base}/limits", name)
    if old and old.get("filter", {}).get("keys") == [key_id] and old.get("amount") == usd:
        return
    if old:
        api.call("DELETE", f"{base}/limits/{old['id']}")
    api.call("POST", f"{base}/limits", spec)
    say(f"keys: {key_name} capped at {usd:g} USD a day")


# admin-api's two management tokens: read-only for its usage reports, and one that may change
# the workspace's limits (per-user quota overrides and resets). Each is an admin token (a
# pacod-id service account) with one role on the platform workspace only.
ADMIN_API_TOKENS = [
    ("GATEWAY_REPORT_TOKEN", "pacod-ap-reports", "viewer"),
    ("GATEWAY_ADMIN_TOKEN", "pacod-ap-admin-api", "workspace_admin"),
]


def ensure_admin_api_tokens(api: Api) -> None:
    org, ws = platform_ids(api)
    stored = read_env(GATEWAY_ENV)
    updates = {}
    if stored.get("GATEWAY_ORG_ID") != org or stored.get("GATEWAY_WORKSPACE_ID") != ws:
        updates.update(GATEWAY_ORG_ID=org, GATEWAY_WORKSPACE_ID=ws)
    for var, name, role in ADMIN_API_TOKENS:
        token = stored.get(var)
        if token:
            status = probe("GET", f"{api.base}/orgs/{org}/workspaces/{ws}", token)
            if not refused(status):
                say(f"admin-api: {var} works")
                continue
            say(f"admin-api: {var} is refused ({status}); making a new one")
        updates[var] = make_admin_token(api, name, role, {"type": "workspace", "id": ws})
        say(f"admin-api: made {var} ({role} of the workspace)")
    if updates:
        write_env(GATEWAY_ENV, updates)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="gw_bootstrap.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--env", choices=("dev", "live"), default="dev")
    p.add_argument(
        "--admin-url",
        help="the management API (dev: http://127.0.0.1:18081, live: http://pacod-gw-admin:8081)",
    )
    p.add_argument(
        "--data-url",
        help="the data plane (dev: http://127.0.0.1:18080, live: http://pacod-gw:8080)",
    )
    p.add_argument(
        "--issuer", help="pacod-id's issuer, for signing in (dev: http://127.0.0.1:8070)"
    )
    p.add_argument(
        "--author",
        action="store_true",
        help="converge the dev gateway on the platform objects and export them",
    )
    p.add_argument(
        "--author-live",
        action="store_true",
        help="make platform-config.live.json on the development gateway (then undo it there)",
    )
    p.add_argument(
        "--export",
        action="store_true",
        help="only write the gateway's current configuration to the file",
    )
    p.add_argument(
        "--rotate-credentials",
        action="store_true",
        help="set provider credentials even where one exists",
    )
    a = p.parse_args(argv)
    d = DEFAULTS[a.env]
    admin_url = a.admin_url or os.environ.get("GW_ADMIN_URL") or d["admin_url"]
    data_url = a.data_url or os.environ.get("GW_DATA_URL") or d["data_url"]
    path = Path(str(CONFIG).format(env=a.env))
    api = admin_api(a.env, admin_url, a.issuer or d["issuer"])
    if a.author:
        if a.env != "dev":
            fail("--author builds the development configuration only")
        author_dev(api, path)
        return
    if a.author_live:
        if a.env != "dev":
            fail("--author-live runs against the development gateway (--env dev)")
        author_live(api, path, Path(str(CONFIG).format(env="live")))
        return
    if a.export:
        export(api, path)
        return
    import_config(api, path)
    ensure_credentials(api, a.rotate_credentials)
    ensure_keys(api, data_url)
    ensure_admin_api_tokens(api)
    say(f"done; the services' credentials are in {GATEWAY_ENV.relative_to(ROOT)} (0600)")


if __name__ == "__main__":
    main()
