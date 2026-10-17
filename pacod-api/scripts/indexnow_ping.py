"""Submit each site's live sitemap to IndexNow (Bing, Yandex, Seznam, Naver).

Run on the host after a deploy:  make indexnow-ping [ARGS="--dry-run"]

The site's key is read from its running container (`frontend`), so what is
pinged is exactly what that container serves at /<key>.txt. A site with no
key, or with indexing disabled, is skipped.

Before submitting, the script fetches https://<host>/<key>.txt and checks it
returns the key: IndexNow rejects submissions whose key file does not verify,
and a silent 403 later is harder to diagnose than a clear skip now.

Standard library only, so it runs on the host without a virtualenv.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

ENDPOINT = "https://api.indexnow.org/indexnow"
SITES = (("PACOD", "frontend"),)
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# IndexNow accepts up to 10,000 URLs per request; our sitemaps are tiny.
MAX_URLS = 10_000


class ContainerError(RuntimeError):
    """The container could not be queried — NOT the same as "no key configured"."""


def container_env(service: str, *names: str) -> dict[str, str]:
    """Read env vars from a running compose service (the neutral, per-site names).

    `printenv NAME` exits 1 with NO stderr when the variable is unset; anything
    else non-zero (service not running, wrong compose project, docker down)
    raises, so a broken setup can never masquerade as "skipped: no key".
    `--profile ui` because both frontends are profile-gated (see `make up-ui`).
    """
    out: dict[str, str] = {}
    for name in names:
        res = subprocess.run(
            ["docker", "compose", "--profile", "ui", "exec", "-T", service, "printenv", name],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            out[name] = res.stdout.strip()
        elif res.returncode == 1 and not res.stderr.strip():
            out[name] = ""
        else:
            detail = res.stderr.strip() or f"exit {res.returncode}"
            raise ContainerError(f"cannot read {name} from {service}: {detail}")
    return out


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "pacod-indexnow-ping/1"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — our own https origin
        return resp.read().decode("utf-8")


def sitemap_urls(site_url: str) -> list[str]:
    root = ET.fromstring(fetch(f"{site_url}/sitemap.xml"))
    return [loc.text.strip() for loc in root.findall("sm:url/sm:loc", SITEMAP_NS) if loc.text][
        :MAX_URLS
    ]


def ping(label: str, service: str, dry_run: bool) -> bool:
    try:
        env = container_env(service, "SITE_URL", "INDEXNOW_KEY", "SITE_INDEXING_ENABLED")
    except ContainerError as exc:
        print(f"[{label}] FAILED: {exc}")
        return False
    site_url = env["SITE_URL"].rstrip("/")
    key = env["INDEXNOW_KEY"]
    if not key:
        print(f"[{label}] skipped: no INDEXNOW_KEY on {service} (set {label}_INDEXNOW_KEY in .env)")
        return True
    if env["SITE_INDEXING_ENABLED"].lower() == "false":
        print(f"[{label}] skipped: indexing disabled on {service}")
        return True
    if not site_url.startswith("https://"):
        print(f"[{label}] skipped: SITE_URL {site_url!r} is not a public https origin")
        return True

    key_location = f"{site_url}/{key}.txt"
    try:
        served = fetch(key_location).strip()
    except urllib.error.URLError as exc:
        print(f"[{label}] FAILED: key file {key_location} unreachable: {exc}")
        return False
    if served != key:
        print(f"[{label}] FAILED: {key_location} does not return the configured key")
        return False

    try:
        urls = sitemap_urls(site_url)
    except (urllib.error.URLError, ET.ParseError) as exc:
        print(f"[{label}] FAILED: sitemap {site_url}/sitemap.xml unreadable: {exc}")
        return False
    if not urls:
        print(f"[{label}] skipped: sitemap is empty")
        return True

    payload = {
        "host": urlparse(site_url).hostname,
        "key": key,
        "keyLocation": key_location,
        "urlList": urls,
    }
    if dry_run:
        print(f"[{label}] dry run — would submit {len(urls)} URL(s):")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return True

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — fixed endpoint
            print(f"[{label}] submitted {len(urls)} URL(s): HTTP {resp.status}")
            return True
    except urllib.error.HTTPError as exc:
        # 202 = accepted, key pending verification (returned as success above);
        # 403 = key not valid for host; 422 = URLs don't belong to host.
        print(f"[{label}] FAILED: HTTP {exc.code} {exc.reason}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the payloads, send nothing")
    parser.add_argument(
        "--site", choices=[label for label, _ in SITES], help="only this site (default: both)"
    )
    args = parser.parse_args()
    ok = True
    for label, service in SITES:
        if args.site and args.site != label:
            continue
        ok = ping(label, service, args.dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
