"""Record the gateway's answers, against its mock provider, as test fixtures.

    python3 -I scripts/record_gw_fixtures.py            (from pacod-api/, with `make gw-up`
                                                          and `make gw-bootstrap` done)

Writes tests/fixtures/gw/<case>.json: what pacod-gw's data plane really sends back for each
outcome the app's client classifies (app/services/llm_client.py, _classify_error) and for the
success shapes it reads (cost, cached tokens, reasoning deltas, finish reasons). The unit tests
replay them with respx, so the classifier is tested on the gateway's own envelopes rather than
on hand-written guesses.

Failures are scripted through gw-mock's control port (POST /__mock/script queues the
behaviour of the next upstream requests); the 402 comes from a tiny spend cap on a scratch key
that is deleted again. Standard library only. Prints case names, never a key.

These are the gateway's shapes against its mock. They are NOT the plan's Phase 3d baseline,
which records LiteLLM's behaviour against real providers (tag pre-gateway, needs keys).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "tests" / "fixtures" / "gw"
GATEWAY_ENV = HERE / ".env.gateway"
DATA = "http://127.0.0.1:18080"
ADMIN = "http://127.0.0.1:18081/api/v1"
MOCK_CONTROL = "http://127.0.0.1:19000"
# Response headers worth keeping: the ones the client reads, and the gateway's own.
KEEP_HEADERS = ("content-type", "retry-after", "x-should-retry", "x-request-id")

ANTHROPIC = "anthropic/claude-sonnet-5-5"
OPENAI = "openai/gpt-5.6-luna"


def env() -> dict[str, str]:
    out = {}
    for line in GATEWAY_ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def http(method, url, *, token=None, body=None, headers=None, stream=False):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        r = e
    status = r.status if hasattr(r, "status") else r.code
    kept = {k.lower(): v for k, v in r.headers.items() if k.lower() in KEEP_HEADERS}
    raw = r.read().decode("utf-8", "replace")
    if stream and status == 200:
        events = [line[6:] for line in raw.splitlines() if line.startswith("data: ")]
        parsed = [json.loads(e) if e != "[DONE]" else "[DONE]" for e in events]
        return status, kept, parsed
    try:
        return status, kept, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, kept, raw


def items(url: str, token: str) -> list[dict]:
    """A management list, unwrapped from its page object."""
    data = http("GET", url, token=token)[2]
    if isinstance(data, dict):
        data = next(
            (data[k] for k in ("items", "data", "results") if isinstance(data.get(k), list)), []
        )
    return data


def script(*steps: str) -> None:
    """Queue gw-mock behaviours for the next upstream requests."""
    status, _, _ = http("POST", f"{MOCK_CONTROL}/__mock/script", body=list(steps))
    if status not in (200, 204):
        sys.exit(f"gw-mock's control port answered {status}")


def chat(model=ANTHROPIC, **extra):
    body = {
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
        "gateway": {"trace_id": "fixture", "tags": {"purpose": "synthesizer"}},
    }
    body.update(extra)
    return body


def save(case: str, request: dict, status: int, headers: dict, body, note: str) -> None:
    # Ids and timings change on every run; keep the shape, not the noise.
    text = json.dumps(
        {
            "case": case,
            "note": note,
            "request": request,
            "status": status,
            "headers": headers,
            "body": body,
        },
        indent=1,
        ensure_ascii=False,
    )
    (OUT / f"{case}.json").write_text(text + "\n", encoding="utf-8")
    print(f"recorded {case} ({status})")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    e = env()
    key, admin = e["GATEWAY_KEY_API"], e["GW_ADMIN_TOKEN"]
    one_try = {"x-gw-retries": "1"}

    # --- outcomes the client classifies ---------------------------------------------------
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token="pgw_not_a_key", body=chat())
    save("error_401_invalid_key", chat(), st, h, b, "unknown API key")

    req = {"model": ANTHROPIC, "max_tokens": 64}
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=req)
    save("error_400_invalid_request", req, st, h, b, "a request without messages")

    script("status=429,retry_after=7")
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=chat(), headers=one_try)
    save("error_429_upstream_rate_limited", chat(), st, h, b, "the provider answered 429")

    script("status=503")
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=chat(), headers=one_try)
    save("error_502_upstream_error", chat(), st, h, b, "the provider answered 503")

    script("hang")
    st, h, b = http(
        "POST",
        f"{DATA}/v1/chat/completions",
        token=key,
        body=chat(),
        headers={**one_try, "x-gw-timeout-ms": "1000"},
    )
    save(
        "error_504_upstream_timeout", chat(), st, h, b, "the provider never answered (1 s timeout)"
    )

    script("hang")
    st, h, b = http(
        "POST",
        f"{DATA}/v1/chat/completions",
        token=key,
        body=chat(stream=True),
        headers={**one_try, "x-gw-timeout-ms": "1000"},
        stream=True,
    )
    save("error_504_stream_open_timeout", chat(stream=True), st, h, b, "a stream that never opened")

    script("reset_after=2")
    req = chat(stream=True, stream_options={"include_usage": True}, max_tokens=200)
    st, h, b = http(
        "POST", f"{DATA}/v1/chat/completions", token=key, body=req, headers=one_try, stream=True
    )
    save(
        "stream_broken_mid_stream",
        req,
        st,
        h,
        b,
        "the provider's connection broke after two chunks: an error event ends the stream",
    )

    # 402: a scratch key with a spend cap so small that the second call is refused.
    org = next(o for o in items(f"{ADMIN}/orgs", admin) if o["name"] == "pacod")
    ws = next(
        w for w in items(f"{ADMIN}/orgs/{org['id']}/workspaces", admin) if w["name"] == "pacod"
    )
    base = f"{ADMIN}/orgs/{org['id']}/workspaces/{ws['id']}"
    scratch = http("POST", f"{base}/keys", token=admin, body={"name": "fixture-scratch"})[2]
    limit = http(
        "POST",
        f"{base}/limits",
        token=admin,
        body={
            "name": "fixture-scratch-cap",
            "subject": "key",
            "metric": "spend",
            "window": "day",
            "amount": 0.000001,
            "enforcement": "request",
            "filter": {"keys": [scratch["id"]]},
        },
    )[2]
    try:
        http("POST", f"{DATA}/v1/chat/completions", token=scratch["secret"], body=chat())
        time.sleep(1.5)  # the spend reaches the limit counters
        st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=scratch["secret"], body=chat())
        save("error_402_quota_exceeded", chat(), st, h, b, "a spend cap reached")
    finally:
        http("DELETE", f"{base}/limits/{limit['id']}", token=admin)
        http("DELETE", f"{base}/keys/{scratch['id']}", token=admin)

    # --- success shapes ---------------------------------------------------------------------
    script("cache_read=40,reasoning=12")
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=chat())
    save(
        "ok_chat_cost_cached",
        chat(),
        st,
        h,
        b,
        "usage.cost and prompt_tokens_details.cached_tokens",
    )

    script("thinking,cache_read=40")
    req = chat(
        stream=True,
        stream_options={"include_usage": True},
        max_tokens=2048,
        thinking={"type": "adaptive", "display": "summarized"},
    )
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=req, stream=True)
    save("ok_stream_thinking", req, st, h, b, "reasoning deltas, then text, then usage with cost")

    script("stop=max_tokens")
    req = chat(stream=True, stream_options={"include_usage": True})
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=req, stream=True)
    save("ok_stream_length", req, st, h, b, "an Anthropic max_tokens stop, normalised")

    script("stop=length")
    st, h, b = http("POST", f"{DATA}/v1/chat/completions", token=key, body=chat(model=OPENAI))
    save("ok_chat_length_openai", chat(model=OPENAI), st, h, b, "finish_reason length")

    st, h, b = http("GET", f"{DATA}/v1/models", token=key)
    save("ok_models", {}, st, h, b, "the workspace's model list")

    st, h, b = http("GET", f"{DATA}/gw/v1/quota?end_user=usr_fixture", token=key)
    save(
        "ok_quota",
        {"end_user": "usr_fixture"},
        st,
        h,
        b,
        "the limits that apply to a key and end user",
    )


if __name__ == "__main__":
    main()
