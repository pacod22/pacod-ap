"""Tests for ingestion chunking and metadata extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.models import Language
from ingestion.pipeline import _parse_frontmatter, chunk_text, parse_document


class TestFrontmatterParsing:
    def test_parses_valid_frontmatter(self):
        text = "---\nsource_id: a\ntitle: B\n---\n\nbody text"
        meta, body = _parse_frontmatter(text)
        assert meta["source_id"] == "a"
        assert meta["title"] == "B"
        assert body.startswith("body text")

    def test_no_frontmatter_returns_empty_meta(self):
        text = "no front matter here"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_strips_quotes_around_values(self):
        text = "---\ntitle: 'Quoted Title'\n---\n\nbody"
        meta, _ = _parse_frontmatter(text)
        assert meta["title"] == "Quoted Title"


class TestChunking:
    def test_short_text_one_chunk(self):
        chunks = chunk_text("This is a short paragraph.", target=1000, overlap=100)
        assert len(chunks) == 1

    def test_long_text_multiple_chunks(self):
        para = "This is a sentence. " * 30
        text = "\n\n".join([para] * 5)
        chunks = chunk_text(text, target=500, overlap=100)
        assert len(chunks) >= 2

    def test_chunks_have_overlap(self):
        # Mix of small paragraphs so the chunker accumulates several into
        # each chunk and the overlap tail mechanism kicks in.
        paras = [
            "Alpha intro paragraph about the first topic.",
            "Bravo continuation that elaborates on alpha.",
            "Charlie further detail relating to bravo.",
            "Delta a separate but related point.",
            "Echo a final wrap-up paragraph.",
            "Foxtrot starts a new section.",
            "Golf continues the new section.",
            "Hotel concludes the new section.",
        ]
        text = "\n\n".join(paras)
        chunks = chunk_text(text, target=200, overlap=120)
        assert len(chunks) >= 2
        # Adjacent chunks should share at least one full paragraph.
        for first, second in zip(chunks, chunks[1:], strict=False):
            first_paras = set(first.split("\n\n"))
            second_paras = set(second.split("\n\n"))
            assert (
                first_paras & second_paras
            ), f"no shared paragraph between adjacent chunks:\nA={first!r}\nB={second!r}"

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\n   ") == []


class TestParseDocument:
    def test_parses_seed_corpus_document(self):
        path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "profiles"
            / "taxes"
            / "corpus"
            / "bg"
            / "zddfl_overview_2026.md"
        )
        doc = parse_document(path)
        assert doc.source_id == "zddfl-overview-2026"
        assert doc.legal_year == 2026
        assert doc.language == Language.BG
        assert doc.url and "lex.bg" in doc.url
        assert doc.body  # non-empty

    def test_missing_required_field_raises(self, tmp_path):
        bad = tmp_path / "bad.md"
        bad.write_text(
            "---\ntitle: x\n---\n\nbody",  # missing source_id, source_type, etc.
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing required keys"):
            parse_document(bad)
