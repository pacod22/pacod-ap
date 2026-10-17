"""Bulk ingestion is per profile: sources, collections, source rows, reference year.

Also pins the web source's resilience: a page the HTML parser chokes on is
skipped, never the end of the run.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import ingestion.pipeline as pipeline
from app.core.db_models import Base, IngestionSource
from app.services.profiles import env_profile_context, parents_of
from ingestion.cpu_pipeline import ingest_all_cpu
from ingestion.sources import web


def _profile(profile_id: str = "acme", year: int = 2030):
    return replace(
        env_profile_context(),
        id=profile_id,
        slug=profile_id,
        legal_year=year,
        base_collection=f"pacod_{profile_id}_kb",
        curated_collection=f"pacod_{profile_id}_curated",
    )


class FakeVectorStore:
    def __init__(self) -> None:
        self.ensured: list[str | None] = []
        self.hash_lookups: list[str | None] = []
        self.upserts: list[tuple[str | None, list[dict]]] = []

    async def ensure_collection(self, collection_name=None):
        self.ensured.append(collection_name)

    async def scroll_source_hashes(self, source_id, collection_name=None):
        self.hash_lookups.append(collection_name)
        return {}

    async def upsert_chunks(self, chunk_ids, vectors, payloads, collection_name=None):
        self.upserts.append((collection_name, payloads))


class FakeEmbedder:
    async def embed(self, texts):
        return [[0.0, 0.0, 0.0, 0.0] for _ in texts]


def _write_profile_sources(root: Path, profile_id: str) -> None:
    corpus = root / profile_id / "corpus" / "en"
    corpus.mkdir(parents=True)
    (corpus / "plans.md").write_text(
        "---\nsource_id: acme-plans\ntitle: Plans\nsource_type: guidance\n"
        "legal_year: 2030\nlanguage: en\n---\n\nThe Pro plan includes ten seats.\n",
        encoding="utf-8",
    )
    docs = root / profile_id / "documents"
    docs.mkdir(parents=True)
    (docs / "faq_en.md").write_text("Support answers within one business day.\n", "utf-8")


class TestProfileSources:
    def test_paths_follow_the_profile_id(self, tmp_path):
        paths = pipeline.profile_sources("acme", data_root=tmp_path)
        assert paths.corpus_root == tmp_path / "acme" / "corpus"
        assert paths.links_file == tmp_path / "acme" / "links.md"
        assert paths.documents_root == tmp_path / "acme" / "documents"

    def test_shipped_tax_example_uses_the_layout(self):
        paths = pipeline.profile_sources("taxes")
        assert any(paths.corpus_root.rglob("*.md"))
        assert paths.links_file.exists()
        assert paths.documents_root.is_dir()


class TestIngestAllCpu:
    async def test_writes_to_the_profiles_collection(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pipeline, "PROFILES_DATA_ROOT", tmp_path)
        _write_profile_sources(tmp_path, "acme")
        vs = FakeVectorStore()

        summary = await ingest_all_cpu(FakeEmbedder(), vs, profile=_profile("acme"))

        assert summary["corpus"] == 1 and summary["local"] == 1 and summary["web"] == 0
        assert vs.ensured == ["pacod_acme_kb"]
        assert set(vs.hash_lookups) == {"pacod_acme_kb"}
        assert vs.upserts and {c for c, _ in vs.upserts} == {"pacod_acme_kb"}
        years = {p["legal_year"] for _, payloads in vs.upserts for p in payloads}
        assert years == {2030}  # front-matter year and the profile's year agree here

    async def test_local_documents_take_the_profiles_year(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pipeline, "PROFILES_DATA_ROOT", tmp_path)
        _write_profile_sources(tmp_path, "acme")
        vs = FakeVectorStore()

        await ingest_all_cpu(FakeEmbedder(), vs, profile=_profile("acme", year=2031))

        local = [p for _, ps in vs.upserts for p in ps if p["source_type"] == "document"]
        assert local and {p["legal_year"] for p in local} == {2031}

    async def test_explicit_paths_override_the_profile_layout(self, tmp_path):
        _write_profile_sources(tmp_path, "other")
        vs = FakeVectorStore()
        summary = await ingest_all_cpu(
            FakeEmbedder(),
            vs,
            root=tmp_path / "other" / "corpus",
            links_file=tmp_path / "missing.md",
            documents_root=tmp_path / "nowhere",
            profile=_profile("acme"),
        )
        assert summary["corpus"] == 1 and summary["local"] == 0
        assert vs.ensured == ["pacod_acme_kb"]

    async def test_packaged_documents_carry_no_upload_file_path(self, tmp_path):
        _write_profile_sources(tmp_path, "acme")
        from ingestion.sources.local_docs import iter_local_docs

        docs = iter_local_docs(tmp_path / "acme" / "documents", 2030)
        assert docs and docs[0].file_path == "faq_en.md"
        pipeline._without_file_path(docs)
        assert docs[0].file_path is None


class TestStatusUpdateIsProfileScoped:
    async def test_only_the_named_profiles_row_changes(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(c, tables=[IngestionSource.__table__])
            )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(UTC)
        async with maker() as s:
            for i, pid in enumerate(("acme", "other"), start=1):
                s.add(
                    IngestionSource(
                        id=i,
                        profile_id=pid,
                        source_id="shared-doc",
                        title="t",
                        source_type="document",
                        legal_year=2030,
                        language="en",
                        status="NEW",
                        ingested_at=now,
                    )
                )
            await s.commit()
        async with maker() as s:
            await pipeline._update_source_status(
                s, "shared-doc", pipeline.IngestionStatus.DONE, chunk_count=3, profile_id="acme"
            )
            await s.commit()
        async with maker() as s:
            rows = {
                r.profile_id: (r.status, r.chunk_count)
                for r in (await s.execute(select(IngestionSource))).scalars()
            }
        await engine.dispose()
        assert rows == {"acme": ("DONE", 3), "other": ("NEW", 0)}


class TestIngestCorpusScript:
    async def test_unknown_profile_is_refused(self, monkeypatch):
        import scripts.ingest_corpus as script

        async def none(_pid, redis=None):
            return None

        monkeypatch.setattr(script, "get_profile", none)
        with pytest.raises(SystemExit) as e:
            await script.resolve_profile("nope")
        assert "nope" in str(e.value)

    async def test_known_profile_is_returned(self, monkeypatch):
        import scripts.ingest_corpus as script

        profile = _profile("acme")

        async def found(_pid, redis=None):
            return profile

        monkeypatch.setattr(script, "get_profile", found)
        assert await script.resolve_profile("acme") is profile

    def test_profile_flag(self):
        import scripts.ingest_corpus as script

        assert script._parse_args(["--profile", "taxes"]).profile == "taxes"
        assert script._parse_args([]).profile == pipeline.DEFAULT_PROFILE_ID


class TestEvalGoldenSet:
    def test_reads_the_profiles_golden_set(self):
        from scripts import run_eval

        assert run_eval.load_cases("taxes")  # the shipped tax example has cases
        assert run_eval.load_cases("no-such-profile") == []
        assert run_eval.golden_path("acme").parts[-2:] == ("acme", "golden_qa.yaml")


class TestWebParsing:
    def test_namespaced_and_brace_attributes_parse(self):
        html = (
            '<html><body><svg xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<use xlink:href="#a"/></svg><div {x="1">Kept text</div></body></html>'
        )
        assert "Kept text" in web._extract_html_text(html)

    async def test_a_page_that_fails_to_parse_is_skipped(self, monkeypatch):
        url = "https://example.org/page"

        class Client:
            async def get(self, *_a, **_k):
                return httpx.Response(
                    200,
                    content=b"<html><body>hi</body></html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                    request=httpx.Request("GET", url),
                )

        def boom(_html):
            raise ValueError("not enough values to unpack")

        monkeypatch.setattr(web, "_extract_html_text", boom)
        assert await web._fetch_one(Client(), asyncio.Semaphore(1), "t", url, 2030) is None

    async def test_fetch_all_keeps_the_good_pages(self, tmp_path, monkeypatch):
        links = tmp_path / "links.md"
        links.write_text("# Good\nhttps://example.org/a\n# Bad\nhttps://example.org/b\n", "utf-8")

        async def fake_fetch_one(client, sem, title, url, legal_year):
            if url.endswith("/b"):
                return None  # what _fetch_one returns for a page it could not parse
            return pipeline.ParsedDoc(
                source_id="web-a",
                title=title,
                source_type="web",
                legal_year=legal_year,
                language=pipeline.Language.EN,
                url=url,
                body="ok",
            )

        monkeypatch.setattr(web, "_fetch_one", fake_fetch_one)
        docs = await web.fetch_all(links, 2030)
        assert [d.source_id for d in docs] == ["web-a"]


def test_parents_companion_name():
    assert parents_of("pacod_acme_kb") == "pacod_acme_kb_parents"
