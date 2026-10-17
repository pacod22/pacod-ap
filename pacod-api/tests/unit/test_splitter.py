"""Unit tests for HierarchicalLegalSplitter."""

from __future__ import annotations

from ingestion.splitter import ChildChunk, HierarchicalLegalSplitter, ParentChunk

SAMPLE_BG_MARKDOWN = """# Закон за корпоративното подоходно облагане

## Глава I. Общи разпоредби

Чл. 1. (1) Настоящият закон урежда облагането с данък върху печалбата.

(2) Данъчно задължени лица по смисъла на този закон са местните юридически лица.

Чл. 2. Данъчната ставка е 10 процента.

(1) Ставката се прилага върху данъчната основа.

(2) Данъчната основа се определя по реда на глава трета.

## Глава II. Данъчна основа

Чл. 3. Данъчната основа е счетоводният финансов резултат, коригиран по реда на тази глава.

(1) Коригиращите позиции включват данъчните постоянни разлики.

(2) И данъчните временни разлики.
"""

SOURCE_ID = "zkkpo-2026"


def _collect(
    splitter: HierarchicalLegalSplitter, md: str, sid: str
) -> list[tuple[ParentChunk, list[ChildChunk]]]:
    return list(splitter.split(md, sid))


class TestHierarchicalLegalSplitter:
    def setup_method(self) -> None:
        self.splitter = HierarchicalLegalSplitter()

    # ------------------------------------------------------------------
    # Basic structure
    # ------------------------------------------------------------------

    def test_yields_parent_child_pairs(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        assert len(pairs) > 0
        for parent, children in pairs:
            assert isinstance(parent, ParentChunk)
            assert isinstance(children, list)
            for child in children:
                assert isinstance(child, ChildChunk)

    def test_at_least_one_child_per_non_empty_parent(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        for parent, children in pairs:
            if parent.text.strip():
                assert len(children) >= 1, f"Parent '{parent.parent_id}' has text but no children"

    # ------------------------------------------------------------------
    # ID stability
    # ------------------------------------------------------------------

    def test_parent_ids_are_stable(self) -> None:
        """Same input and source_id must produce identical parent IDs."""
        pairs1 = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        pairs2 = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        ids1 = [p.parent_id for p, _ in pairs1]
        ids2 = [p.parent_id for p, _ in pairs2]
        assert ids1 == ids2

    def test_child_ids_are_stable(self) -> None:
        """Same input and source_id must produce identical child IDs."""
        pairs1 = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        pairs2 = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        kids1 = [c.child_id for _, cs in pairs1 for c in cs]
        kids2 = [c.child_id for _, cs in pairs2 for c in cs]
        assert kids1 == kids2

    def test_different_source_ids_produce_different_ids(self) -> None:
        pairs_a = _collect(self.splitter, SAMPLE_BG_MARKDOWN, "source-a")
        pairs_b = _collect(self.splitter, SAMPLE_BG_MARKDOWN, "source-b")
        ids_a = {p.parent_id for p, _ in pairs_a}
        ids_b = {p.parent_id for p, _ in pairs_b}
        assert ids_a.isdisjoint(ids_b), "Different source_ids must not share parent_ids"

    # ------------------------------------------------------------------
    # source_id propagation
    # ------------------------------------------------------------------

    def test_source_id_in_all_chunks(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        for parent, children in pairs:
            assert parent.source_id == SOURCE_ID
            for child in children:
                assert child.source_id == SOURCE_ID

    # ------------------------------------------------------------------
    # Parent-child linkage
    # ------------------------------------------------------------------

    def test_children_link_to_parent(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        for parent, children in pairs:
            for child in children:
                assert child.parent_id == parent.parent_id

    # ------------------------------------------------------------------
    # Enriched text / breadcrumb
    # ------------------------------------------------------------------

    def test_enriched_text_contains_breadcrumb_and_text(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        for _, children in pairs:
            for child in children:
                assert child.breadcrumb in child.enriched_text
                assert child.text in child.enriched_text

    def test_child_text_in_parent_text(self) -> None:
        """Every child's raw text should be a substring of its parent's text."""
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        for parent, children in pairs:
            for child in children:
                # Child text may be a combined paragraph; check each paragraph individually.
                for para in child.text.split("\n\n"):
                    if para.strip():
                        assert (
                            para.strip() in parent.text
                        ), f"Paragraph '{para[:60]}' not found in parent text"

    # ------------------------------------------------------------------
    # No-headers fallback
    # ------------------------------------------------------------------

    def test_no_headers_treated_as_one_parent(self) -> None:
        plain = "This is plain text.\n\nNo markdown headers here.\n\nJust paragraphs."
        pairs = _collect(self.splitter, plain, "plain-doc")
        assert len(pairs) == 1, f"Expected 1 parent for headerless doc, got {len(pairs)}"
        parent, children = pairs[0]
        assert parent.text.strip()
        assert len(children) >= 1

    # ------------------------------------------------------------------
    # CHILD_MAX_CHARS enforcement
    # ------------------------------------------------------------------

    def test_child_max_chars_respected(self) -> None:
        # Build a parent whose body has several short-ish paragraphs that,
        # individually, are well under the limit but accumulate across children.
        # Also include one giant paragraph to test the oversized single-para path.
        giant_para = "Г" * (HierarchicalLegalSplitter.CHILD_MAX_CHARS + 500)
        md = "# Заглавие\n\n" "Чл. 1. Кратък текст.\n\n" f"{giant_para}\n\n" "Следващ параграф.\n"
        pairs = _collect(self.splitter, md, "max-chars-test")
        for _, children in pairs:
            for child in children:
                # Each child must not exceed the limit (unless it is a single
                # paragraph that already exceeds it — that is the force-emit path).
                paras = [p for p in child.text.split("\n\n") if p.strip()]
                if len(paras) > 1:
                    assert (
                        len(child.text) <= HierarchicalLegalSplitter.CHILD_MAX_CHARS
                    ), f"Multi-paragraph child exceeds CHILD_MAX_CHARS: {len(child.text)}"

    # ------------------------------------------------------------------
    # Article-level splitting
    # ------------------------------------------------------------------

    def test_articles_start_new_parents(self) -> None:
        """Each Чл. header should create a new parent boundary."""
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        # We have Чл. 1, Чл. 2 (Глава I) and Чл. 3 (Глава II) — at minimum 3 article parents
        # plus possibly preamble/chapter header parents.
        assert len(pairs) >= 3

    def test_hierarchy_path_not_empty_for_articled_content(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        # At least some parents should have a populated hierarchy_path
        paths = [p.hierarchy_path for p, _ in pairs if p.hierarchy_path]
        assert len(paths) > 0, "Expected at least one parent with a non-empty hierarchy_path"

    # ------------------------------------------------------------------
    # ID uniqueness
    # ------------------------------------------------------------------

    def test_all_parent_ids_unique(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        parent_ids = [p.parent_id for p, _ in pairs]
        assert len(parent_ids) == len(set(parent_ids)), "Duplicate parent_ids detected"

    def test_all_child_ids_unique(self) -> None:
        pairs = _collect(self.splitter, SAMPLE_BG_MARKDOWN, SOURCE_ID)
        child_ids = [c.child_id for _, cs in pairs for c in cs]
        assert len(child_ids) == len(set(child_ids)), "Duplicate child_ids detected"

    # ------------------------------------------------------------------
    # Empty / minimal input
    # ------------------------------------------------------------------

    def test_empty_input_yields_nothing(self) -> None:
        pairs = _collect(self.splitter, "", SOURCE_ID)
        assert pairs == []

    def test_whitespace_only_yields_nothing(self) -> None:
        pairs = _collect(self.splitter, "   \n\n   ", SOURCE_ID)
        assert pairs == []
