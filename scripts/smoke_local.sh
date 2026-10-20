#!/usr/bin/env bash
# The local stack answers: the edge's /health, then one real chat turn of the starter agent.
#
#   scripts/smoke_local.sh        (make smoke-local; part of make smoke-all)
#
# The turn runs inside the ap-api container (a dev-token bearer is refused at the edge, which
# expects a browser session) as the development user test_user — dev tokens need both
# APP_ENV=development and AUTH_ALLOW_DEV_TOKENS=true, which .env.local sets.
# When the gateway's mock provider answers on its control port, the turn's first two calls are
# scripted (the topic verdict and a plan that calls get_fact): the mock's echo would otherwise
# be refused by the topic guard. Against real providers nothing is scripted. Every run asks a
# new question, so the response cache never answers it. Fails unless the stream reaches
# `event: done`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOCK_CONTROL="${MOCK_CONTROL:-http://127.0.0.1:19000}"
fail() { echo "smoke-local: FAIL: $*" >&2; exit 1; }
b64url() { printf '%s' "$1" | base64 | tr '+/' '-_' | tr -d '=\n'; }

echo "smoke-local: the edge's /health"
curl -fsS http://localhost:8080/health >/dev/null || fail "http://localhost:8080/health does not answer"

docker inspect ap-api >/dev/null 2>&1 || fail "the app is not running: make up-local"

if curl -fsS -o /dev/null -m 2 -X DELETE "$MOCK_CONTROL/__mock/script" 2>/dev/null; then
	classifier='{"is_relevant": true, "confidence": 0.95, "detected_language": "en", "reason": "a pricing question", "needs_clarification": false, "follow_ups": []}'
	planner='{"is_complex": false, "tool_calls": [{"name": "get_fact", "args": {"fact_key": "plan_price_monthly", "variant": "plus"}}]}'
	curl -fsS -X POST "$MOCK_CONTROL/__mock/script" -H 'Content-Type: application/json' \
		-d "[\"reply=$(b64url "$classifier")\", \"reply=$(b64url "$planner")\"]" >/dev/null
	echo "smoke-local: the gateway's mock answers: its first two replies are scripted"
fi

stamp="$(date +%s)"
echo "smoke-local: one SSE chat turn (starter, as test_user)"
sse="$(docker exec ap-api curl -N -sS --fail-with-body -X POST http://localhost:8000/api/v1/chat \
	-H 'Authorization: Bearer dev-token-test_user' -H 'Content-Type: application/json' \
	-d "{\"message\": \"What does the Plus plan cost each month? (local smoke $stamp-$$)\", \"session_id\": \"local-smoke-$stamp\"}")" \
	|| fail "the chat request failed: $(head -c 300 <<<"${sse:-}")"
grep -q '^event: done' <<<"$sse" || fail "the stream did not reach 'event: done': $(head -c 300 <<<"$sse")"
tokens="$(grep -c '^event: token' <<<"$sse" || true)"
echo "smoke-local: ok — the turn streamed $tokens token event(s) to 'done'"
