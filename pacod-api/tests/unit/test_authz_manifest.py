"""The app's pacod-id manifest (authz/pacod.yaml) loads and its vectors hold.

Runs authz/vectors/pacod.yaml through the vendored pacod_id rules engine, the same engine
pacod-id and the frontend use, so what a role may do is pinned in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

authz = pytest.importorskip("pacod_id.authz", reason="the vendored pacod_id wheel is not installed")
from pacod_id import vectors  # noqa: E402

AUTHZ = Path(__file__).resolve().parents[2] / "authz"


def _load(name: str):
    return authz.Manifest.from_yaml((AUTHZ / f"{name}.yaml").read_text(encoding="utf-8"))


def test_manifest_loads_as_the_pacod_product():
    manifest = _load("pacod")
    assert manifest.product == "pacod"


def test_vectors_hold():
    checks, failures = vectors.run(
        (AUTHZ / "vectors" / "pacod.yaml").read_text(encoding="utf-8"), _load
    )
    assert checks > 30
    assert failures == []
