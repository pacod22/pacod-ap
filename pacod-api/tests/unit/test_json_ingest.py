"""JSON ingestion: keep-as-JSON (default) vs convert-to-Markdown."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.models import Language
from ingestion.sources.local_docs import parse_local_doc

_DATA = {
    "tax": "ДДС",
    "rate": 20,
    "active": True,
    "missing": None,
    "notes": ["standard", "reduced"],
    "meta": {"year": 2026},
}


def _write(tmp_path: Path, name: str = "rates.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(_DATA), encoding="utf-8")
    return p


class TestJsonIngest:
    def test_default_mode_keeps_json(self, tmp_path):
        doc = parse_local_doc(_write(tmp_path), 2026)  # default json_mode="keep"
        assert doc is not None
        assert '"tax": "ДДС"' in doc.body  # still JSON, pretty-printed
        assert "**tax**" not in doc.body  # not Markdown
        assert doc.source_type == "document"

    def test_keep_mode_explicit(self, tmp_path):
        doc = parse_local_doc(_write(tmp_path), 2026, json_mode="keep")
        assert doc is not None and '"rate": 20' in doc.body

    def test_md_mode_converts(self, tmp_path):
        doc = parse_local_doc(_write(tmp_path), 2026, json_mode="md")
        assert doc is not None
        assert "**tax**: ДДС" in doc.body
        assert "**rate**: 20" in doc.body
        assert "**active**: true" in doc.body  # bool rendered
        assert "- standard" in doc.body  # list item
        assert '"tax":' not in doc.body  # not raw JSON

    def test_invalid_json_falls_back_to_raw(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        for mode in ("keep", "md"):
            doc = parse_local_doc(p, 2026, json_mode=mode)
            assert doc is not None and doc.body.strip().startswith("{not valid")

    def test_language_from_filename(self, tmp_path):
        doc = parse_local_doc(_write(tmp_path, "rates_en.json"), 2026)
        assert doc is not None and doc.language == Language.EN
