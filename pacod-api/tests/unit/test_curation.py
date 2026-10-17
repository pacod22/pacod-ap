"""Unit tests for Phase 2 curation: provenance rendering, stakes, grounding gate,
and candidate building. No DB / network — grounding uses fakes + a monkeypatched
authoritative-collection resolver.
"""

from __future__ import annotations

import pytest

import improvement_loop.grounding as grounding_mod
import improvement_loop.novelty as novelty_mod
from app.services.curated_provenance import (
    candidate_content_hash,
    curated_source_id,
    render_full_markdown,
    render_note_body,
)
from app.services.profiles import ProfileContext, env_profile_context
from improvement_loop.curated_insert import auto_insert_eligible, should_auto_insert
from improvement_loop.grounding import GroundingChecker
from improvement_loop.models import CandidateKnowledge, Finding
from improvement_loop.novelty import NoveltyChecker
from improvement_loop.promote import build_candidate, compute_stakes

# ---- provenance ----------------------------------------------------


def test_content_hash_is_stable_and_order_insensitive_on_citations():
    h1 = candidate_content_hash("ДДС праг", "Прагът е 100000 лв.", ["ЗДДС чл. 96", "ЗДДС чл. 97"])
    h2 = candidate_content_hash("ДДС праг", "Прагът е 100000 лв.", ["ЗДДС чл. 97", "ЗДДС чл. 96"])
    assert h1 == h2
    assert curated_source_id(h1) == f"curated_{h1[:16]}"


def test_note_body_excludes_yaml_but_includes_claim_and_citations():
    body = render_note_body("Тема", "Това е твърдението.", ["ЗДДС чл. 96"], "EUR")
    assert "---" not in body  # no front-matter pollutes the embedded text
    assert "Това е твърдението." in body
    assert "ЗДДС чл. 96" in body


def test_full_markdown_has_provenance_frontmatter_and_body():
    md = render_full_markdown(
        topic="Тема",
        claim="Твърдение",
        citations=["ЗДДС чл. 96"],
        currency_basis="BGN",
        derived_from_turns=["t1"],
        models_used=["claude", "gemini"],
        synthesis_confidence=0.8,
        human_reviewed=True,
        reviewer="paco",
        source_id="curated_abc",
    )
    assert md.startswith("---")
    assert "tier: llm_curated" in md
    assert "source_type: synthesized" in md
    assert "human_reviewed: true" in md
    assert "trust_weight:" in md
    assert "Твърдение" in md  # body present


# ---- stakes --------------------------------------------------------


@pytest.fixture
def stakes_keywords(monkeypatch):
    """The platform ships no keyword floor; an operator sets one per deployment."""
    from app.core.config import get_settings

    monkeypatch.setattr(
        get_settings(), "loop_high_stakes_keywords", "регистрация по ддс,осигуров,penalty"
    )


def test_compute_stakes_without_keywords_follows_the_analyst():
    assert compute_stakes("Регистрация по ДДС", "кога е задължителна") == "low"
    assert compute_stakes("Регистрация по ДДС", "кога е задължителна", "high") == "high"


def test_compute_stakes_flags_high_stakes_topics(stakes_keywords):
    assert compute_stakes("Регистрация по ДДС", "кога е задължителна") == "high"
    assert compute_stakes("Осигуровки за самоосигуряващи се", "ставки") == "high"
    assert compute_stakes("Как се чете фактура", "обяснение") == "low"


# ---- grounding gate ------------------------------------------------


class _Hit:
    def __init__(self, score):
        self.score = score


class _FakeEmbeddings:
    async def embed(self, texts):
        return [[1.0] * 1024 for _ in texts]


class _FakeVS:
    def __init__(self, score):
        self._score = score

    async def search(self, **kw):
        return [_Hit(self._score)]


@pytest.fixture
def patch_authoritative(monkeypatch):
    async def _active(_redis, **kw):
        return "pacod_kb"

    monkeypatch.setattr(grounding_mod, "get_active_collection", _active)


@pytest.mark.asyncio
async def test_grounding_grounded_when_citation_anchors(patch_authoritative):
    gc = GroundingChecker(_FakeEmbeddings(), _FakeVS(score=0.9))
    res = await gc.ground(CandidateKnowledge(claim="x", required_citations=["ЗДДС чл. 96"]), 2026)
    assert res.status == "grounded"
    assert res.anchored_citations == ["ЗДДС чл. 96"]


@pytest.mark.asyncio
async def test_grounding_needs_source_when_no_anchor(patch_authoritative):
    gc = GroundingChecker(_FakeEmbeddings(), _FakeVS(score=0.1))
    res = await gc.ground(CandidateKnowledge(claim="x", required_citations=["ЗДДС чл. 999"]), 2026)
    assert res.status == "needs_source"
    assert res.missing_citations == ["ЗДДС чл. 999"]


@pytest.mark.asyncio
async def test_grounding_ungrounded_without_citations(patch_authoritative):
    gc = GroundingChecker(_FakeEmbeddings(), _FakeVS(score=0.9))
    res = await gc.ground(CandidateKnowledge(claim="x", required_citations=[]), 2026)
    assert res.status == "ungrounded"


# ---- profile scoping of the authoritative tier ----------------------
# Regression: both checkers used to call get_active_collection(None) with no
# profile, so every profile's novelty/grounding verdicts came from the DEFAULT
# profile's KB.


def _profile(pid: str, base: str) -> ProfileContext:
    return ProfileContext(
        id=pid,
        slug=pid,
        name=pid,
        topic_name="t",
        topic_description_bg="d",
        topic_description_en="d",
        legal_year=2026,
        languages=("bg",),
        base_collection=base,
        curated_collection=f"pacod_{pid}_curated",
    )


@pytest.fixture
def capture_collection(monkeypatch):
    """Record the (profile_id, default_collection) each checker resolves with."""
    seen: dict = {}

    async def _active(_redis, *, profile_id=None, default_collection=None):
        seen["profile_id"] = profile_id
        seen["default_collection"] = default_collection
        return default_collection or "pacod_taxes_kb"

    monkeypatch.setattr(grounding_mod, "get_active_collection", _active)
    monkeypatch.setattr(novelty_mod, "get_active_collection", _active)
    return seen


@pytest.mark.asyncio
async def test_grounding_uses_the_runs_own_profile_collection(capture_collection):
    gc = GroundingChecker(
        _FakeEmbeddings(), _FakeVS(score=0.9), profile=_profile("traffic", "pacod_traffic_kb")
    )
    await gc.ground(CandidateKnowledge(claim="x", required_citations=["чл. 1"]), 2026)
    assert capture_collection["profile_id"] == "traffic"
    assert capture_collection["default_collection"] == "pacod_traffic_kb"


@pytest.mark.asyncio
async def test_novelty_uses_the_runs_own_profile_collection(capture_collection):
    nc = NoveltyChecker(
        _FakeEmbeddings(),
        _FakeVS(score=0.95),
        profile=_profile("condominium", "pacod_condominium_kb"),
    )
    verdict, _score = await nc.check(CandidateKnowledge(topic="Тема", claim="Твърдение"), 2026)
    assert verdict == "covered"
    assert capture_collection["profile_id"] == "condominium"
    assert capture_collection["default_collection"] == "pacod_condominium_kb"


@pytest.mark.asyncio
async def test_checkers_fall_back_to_the_env_profile_when_none_given(capture_collection):
    """No profile (tests / standalone callers) still resolves, via env settings."""
    nc = NoveltyChecker(_FakeEmbeddings(), _FakeVS(score=0.1))
    await nc.check(CandidateKnowledge(topic="Тема", claim="Твърдение"), 2026)
    assert capture_collection["profile_id"] == env_profile_context().id


# ---- candidate building --------------------------------------------


def _finding(**kw):
    base = {
        "turn_id": "t1",
        "session_id": "s",
        "user_id": "u",
        "content_hash": "h",
        "assessment": "incorrect",
        "failure_mode": "content_gap",
        "route": "content_gap",
        "novelty": "gap",
        "synthesis_confidence": 0.77,
        "candidate": {
            "topic": "Регистрация по ДДС",
            "claim": "Прагът е 100000 лв.",
            "required_citations": ["ЗДДС чл. 96"],
            "currency_basis": "BGN",
        },
        "evidence": "",
    }
    base.update(kw)
    return Finding(**base)


# ---- Phase 3 auto-insert eligibility -------------------------------


def _elig(**over):
    base = {
        "enabled": True,
        "grounding_status": "grounded",
        "stakes": "low",
        "agreement": "full",
        "confidence": 0.9,
    }
    base.update(over)
    return auto_insert_eligible(**base)


def test_auto_insert_requires_all_conditions():
    assert _elig() is True
    assert _elig(enabled=False) is False  # flag off → never
    assert _elig(grounding_status="needs_source") is False
    assert _elig(stakes="high") is False  # high-stakes never auto-inserts
    assert _elig(agreement="conflict") is False  # models disagreed
    assert _elig(confidence=0.5) is False  # below threshold


def test_auto_insert_respects_prior_decision():
    # New or still-pending → may auto-insert; a prior decision is never resurrected.
    assert should_auto_insert(None) is True
    assert should_auto_insert("pending") is True
    assert should_auto_insert("rejected") is False
    assert should_auto_insert("inserted") is False
    assert should_auto_insert("superseded") is False


def test_build_candidate_row_shape_and_stakes(stakes_keywords):
    res = grounding_mod.GroundingResult("grounded", ["ЗДДС чл. 96"], [], 0.9)
    row = build_candidate(_finding(), res, models_used=["claude", "gemini"], run_id=7)
    assert row["status"] == "pending" and row["needs_human_review"] is True
    assert row["stakes"] == "high"  # "регистрация по ддс" is on the denylist
    assert row["grounding_status"] == "grounded"
    assert row["run_id"] == 7
    assert row["content_hash"] == candidate_content_hash(
        "Регистрация по ДДС", "Прагът е 100000 лв.", ["ЗДДС чл. 96"]
    )
    assert "tier: llm_curated" in row["markdown"]


def test_compute_stakes_respects_the_analysts_judgement(stakes_keywords):
    # The keyword list is a deployment's floor; beyond it the analyst's own
    # `stakes` is what engages the auto-insert brake.
    assert compute_stakes("Кворум на общото събрание", "мнозинство", "high") == "high"
    assert compute_stakes("Кворум на общото събрание", "мнозинство", "low") == "low"
    assert compute_stakes("Кворум на общото събрание", "мнозинство", None) == "low"
    # Keyword hits still count regardless of what the model said.
    assert compute_stakes("Регистрация по ДДС", "кога", "low") == "high"


def test_candidate_knowledge_stakes_coerces():
    from improvement_loop.models import CandidateKnowledge

    assert CandidateKnowledge(stakes="HIGH").stakes == "high"
    assert CandidateKnowledge(stakes="whatever").stakes == "low"
    assert CandidateKnowledge().stakes == "low"
