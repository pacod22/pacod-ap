"""pacod-id's configuration files (docker/pacod-id/): the local one is the production one plus
the development client gw-smoke, and both parse once the compose variables are filled in."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[2] / "docker" / "pacod-id"

# Checks of the repository's deployment files, which the api image does not carry.
pytestmark = pytest.mark.skipif(
    not DIR.is_dir(), reason="docker/pacod-id is not in this tree (api image)"
)

# What docker-compose.yml / docker-compose.local.yml pass to the pacod-id service.
ENV = {
    "PACOD_ID_ISSUER": "http://127.0.0.1:8070",
    "PACOD_ID_SELF_SIGNUP": "true",
    "PACOD_PUBLIC_URL": "http://localhost:8080",
    "PGW_CONSOLE_URL": "http://127.0.0.1:13000",
}


def _expand(text: str) -> str:
    """pacod-id's own rule (config.rs expand_env): ${NAME} on non-comment lines, unset = error."""
    out = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("#"):
            out.append(line)
            continue
        out.append(re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: ENV[m.group(1)], line))
    return "".join(out)


def _load(name: str) -> dict:
    return tomllib.loads(_expand((DIR / name).read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def prod():
    return _load("pacod-id.toml")


@pytest.fixture(scope="module")
def local():
    return _load("pacod-id.local.toml")


def test_one_realm_shared_by_every_client(prod):
    (realm,) = prod["realm"]
    assert realm["name"] == "pacod" and realm["issuer"] == ENV["PACOD_ID_ISSUER"]
    assert realm["self_signup"] is True
    assert {c["realm"] for c in prod["client"]} == {"pacod"}


def test_the_realm_has_a_brand_pacod_id_knows(prod):
    # pacod-id refuses to start on any other (config.rs BRANDS at vendor/PACOD_ID_VERSION; it
    # dropped "pacod" with c4eb04b).
    (realm,) = prod["realm"]
    assert realm["brand"] in {"gateway", "shop", "bazaar"}


def test_local_is_prod_plus_gw_smoke(prod, local):
    assert local["realm"] == prod["realm"]
    by_id = {c["id"]: c for c in local["client"]}
    assert by_id.pop("gw-smoke")["kind"] == "public"
    assert by_id == {c["id"]: c for c in prod["client"]}


def test_production_has_no_development_client(prod):
    assert all(c["kind"] != "public" for c in prod["client"])


def test_clients_the_gateway_expects(prod):
    by_id = {c["id"]: c for c in prod["client"]}
    # The ids and audiences pacod-gw's core, console and tester are built with.
    assert by_id["pacod-gw-core"]["product"] == "gw"
    assert {
        by_id[i]["audience"] for i in ("pacod-gw-console", "pacod-gw-core", "pacod-gw-tester")
    } == {"pacod-gw"}
    assert by_id["pacod-web"]["audience"] == "pacod-api"
    assert by_id["pacod-api"]["product"] == "pacod"


def test_compose_pins_the_vendored_commit():
    """docker-compose.yml's default pacod-id image is tagged with the commit the vendored
    libraries came from (vendor/PACOD_ID_VERSION), as `make id-image` tags it."""
    root = DIR.parents[1]
    commit = (root / "vendor" / "PACOD_ID_VERSION").read_text().split()[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"${{PACOD_ID_IMAGE:-pacod-id:{commit[:7]}}}" in compose
