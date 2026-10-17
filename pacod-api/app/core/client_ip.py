"""The real client IP of a request, as the trusted edge reported it.

Every public path reaches this process through Caddy, which *sets* (never
appends) ``X-Forwarded-For`` / ``X-Real-IP`` to the TCP peer it saw; the
SvelteKit proxy forwards those headers unchanged, and its server-side calls
set ``X-Forwarded-For`` from the same value. In production ``api:8000`` is not
published, so a client cannot bypass the edge to forge them; the local overlay
(docker-compose.local.yml) publishes it on 127.0.0.1:8000 only, for curl and
scripts on the same machine.

Deliberately **no peer-address fallback**: without the headers the peer is a
container (frontend, open-webui), and recording that as a user's IP would be
worse than recording nothing. A caller that wants the peer anyway adds it
itself.
"""

from __future__ import annotations

from collections.abc import Mapping

MAX_IP_LEN = 64


def client_ip_from_headers(headers: Mapping[str, str]) -> str | None:
    """First X-Forwarded-For hop, else X-Real-IP. ``headers`` must be
    case-insensitive or have lower-case keys."""
    xff = headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:MAX_IP_LEN]
    real_ip = (headers.get("x-real-ip") or "").strip()
    return real_ip[:MAX_IP_LEN] or None
