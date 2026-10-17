#!/usr/bin/env bash
# One chat turn of the app through the gateway, against its mock provider, end to end.
#
#   scripts/smoke_gateway_turn.sh        (make gw-turn-smoke; part of make smoke-all)
#
# Needs the local app stack (make up-local) and the gateway's development stack (make gw-up,
# make gw-bootstrap). The mock answers with an echo, which no guard or planner could parse, so
# the first two calls of the turn are scripted on its control port: the topic classifier's
# verdict and a plan that calls get_fact. Then it checks:
#   - the SSE stream reaches `event: done`, with the planned tool call and streamed tokens;
#   - the gateway recorded the turn under trace = the turn id, attributed to the user, the
#     agent and the session, and its cost equals the `done` event's.
# Every run asks a slightly different question, so neither the topic cache nor the response
# cache answers it without the scripted calls. Prints no key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATEWAY_ENV="$ROOT/pacod-api/.env.gateway"
MOCK_CONTROL="${MOCK_CONTROL:-http://127.0.0.1:19000}"
ADMIN="${GW_ADMIN_URL:-http://127.0.0.1:18081}/api/v1"

fail() { echo "gw-turn-smoke: FAIL: $*" >&2; exit 1; }
b64url() { printf '%s' "$1" | base64 | tr '+/' '-_' | tr -d '=\n'; }
envval() { grep "^$1=" "$GATEWAY_ENV" | tail -1 | cut -d= -f2-; }

[ -f "$GATEWAY_ENV" ] || fail "no pacod-api/.env.gateway: run make gw-bootstrap"
docker inspect pacod-api >/dev/null 2>&1 || fail "the app is not running: make up-local"

classifier='{"is_relevant": true, "confidence": 0.95, "detected_language": "en", "reason": "a pricing question", "needs_clarification": false, "follow_ups": []}'
planner='{"is_complex": false, "tool_calls": [{"name": "get_fact", "args": {"fact_key": "price", "variant": "plus"}}]}'
curl -fsS -X POST "$MOCK_CONTROL/__mock/script" -H 'Content-Type: application/json' \
	-d "[\"reply=$(b64url "$classifier")\", \"reply=$(b64url "$planner")\"]" >/dev/null \
	|| fail "gw-mock's control port ($MOCK_CONTROL) does not answer: make gw-up"

stamp="$(date +%s)"
sse="$(docker exec pacod-api curl -N -sS -X POST http://localhost:8000/api/v1/chat \
	-H 'Authorization: Bearer dev-token-gw_smoke_user' -H 'Content-Type: application/json' \
	-d "{\"message\": \"How much does the Plus plan cost per month? (smoke $stamp)\", \"session_id\": \"gw-turn-smoke-$stamp\"}")"

grep -q '^event: done' <<<"$sse" || fail "the stream did not reach 'done': $(head -c 300 <<<"$sse")"
grep -A1 '^event: tool_call' <<<"$sse" | grep -q '"get_fact"' || fail "no get_fact tool call in the stream"
[ "$(grep -c '^event: token' <<<"$sse")" -gt 0 ] || fail "no tokens were streamed"
done_json="$(grep -A1 '^event: done' <<<"$sse" | sed -n 's/^data: //p' | tail -1)"
turn_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["turn_id"])' "$done_json")"
echo "gw-turn-smoke: turn $turn_id streamed to 'done' with a get_fact call"

token="$(envval GATEWAY_REPORT_TOKEN)"
ws="$(envval GATEWAY_WORKSPACE_ID)"
for _ in 1 2 3 4 5 6 7 8 9 10; do  # the gateway's events reach its store within a second or two
	trace="$(curl -fsS -H "Authorization: Bearer $token" "$ADMIN/analytics/traces/$turn_id?workspace_id=$ws")" || fail "the gateway's trace report does not answer"
	python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1])["totals"]["requests"] else 1)' "$trace" && break
	sleep 1
done
python3 - "$trace" "$done_json" <<'EOF' || fail "the gateway's record of the turn does not match"
import json, sys
trace, done = json.loads(sys.argv[1]), json.loads(sys.argv[2])
reqs = trace["requests"]
assert reqs, "no request recorded under the turn's trace id"
tasks = [r["task"] for r in reqs]
for need in ("classifier", "planner", "synthesizer"):
    assert need in tasks, f"no {need} call in the trace: {tasks}"
for r in reqs:
    assert r["end_user"] == "gw_smoke_user", r
    assert r["agent_id"] == "starter" and r["tags"].get("profile") == "starter", r
    assert r["session_id"].startswith("gw-turn-smoke-"), r
    assert r["tags"]["purpose"] == r["task"], r
gw_cost, app_cost = trace["totals"]["cost"], done["usage"]["cost_usd"]
assert abs(gw_cost - app_cost) < 1e-6, f"cost: gateway {gw_cost}, app {app_cost}"
print(f"gw-turn-smoke: the gateway recorded {len(reqs)} calls ({', '.join(tasks)}), cost {gw_cost} = the turn's")
EOF
echo "gw-turn-smoke: ok"
