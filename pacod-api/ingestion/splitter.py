"""Hierarchical text splitter for Bulgarian and English legal Markdown documents.

Produces (ParentChunk, list[ChildChunk]) pairs from Markdown source text.
Parent chunks correspond to Articles/Sections (hierarchy level ≤ 3).
Child chunks are paragraph-level sub-splits inside each parent, enriched with
a breadcrumb that encodes the full structural path.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ParentChunk:
    parent_id: str  # uuid5 — stable across re-runs
    source_id: str
    hierarchy_path: list[str]  # e.g. ["Глава I", "Раздел 2", "Чл. 15"]
    text: str  # complete article/section body (raw, no breadcrumb)


@dataclass
class ChildChunk:
    child_id: str  # uuid5 — stable across re-runs
    parent_id: str
    source_id: str
    breadcrumb: str  # "[Context: Глава I -> Раздел 2 -> Чл. 15]"
    enriched_text: str  # breadcrumb + " [Text: " + text + "]"
    text: str  # raw paragraph text (no breadcrumb)


class HierarchicalLegalSplitter:
    """Stateful hierarchical text splitter.

    Each call to :meth:`split` is independent — all counters are local to
    that invocation.  Multiple documents may be processed by the same
    instance sequentially without interference.
    """

    # Max chars for a parent chunk before forced split (≈ 2 000 tokens).
    PARENT_MAX_CHARS: ClassVar[int] = 8_000
    # Max chars for a child chunk (≈ 300 tokens).
    CHILD_MAX_CHARS: ClassVar[int] = 1_200

    # Ordered from highest level to lowest — first match wins per line.
    # level 0 = document title, level 4 = sub-paragraph (Алинея).
    HIERARCHY_PATTERNS: ClassVar[list[tuple[int, re.Pattern[str]]]] = [
        # Markdown H1/H2 headings (Bulgarian & English)
        (0, re.compile(r"^#{1,2}\s+.+", re.MULTILINE)),
        # Bulgarian law — Chapter
        (1, re.compile(r"^\s*Глава\s+[IVXLCD\d]+", re.MULTILINE | re.IGNORECASE)),
        (1, re.compile(r"^\s*ГЛАВА\s+[IVXLCD\d]+", re.MULTILINE)),
        # Bulgarian law — Section
        (2, re.compile(r"^\s*Раздел\s+[IVXLCD\d]+", re.MULTILINE | re.IGNORECASE)),
        # Bulgarian law — Article
        (3, re.compile(r"^\s*Чл\.\s*\d+", re.MULTILINE)),
        (3, re.compile(r"^\s*Член\s+\d+", re.MULTILINE | re.IGNORECASE)),
        # English legal — Article / Section at H3/H4 level
        (3, re.compile(r"^#{3,4}\s+(?:Article|Section)\s+\d+", re.MULTILINE)),
        # Bulgarian law — Sub-paragraph / Алинея (does NOT flush a parent)
        (4, re.compile(r"^\s*\(\d+\)", re.MULTILINE)),
        (4, re.compile(r"^\s*Ал\.\s*\d+", re.MULTILINE | re.IGNORECASE)),
        # English legal — Title / Part (H1/H2 equivalent)
        (0, re.compile(r"^#{1,2}\s+(?:Title|Part)\b", re.MULTILINE)),
        # English legal — Chapter (H2/H3)
        (1, re.compile(r"^#{2,3}\s+Chapter\b", re.MULTILINE)),
        # English legal — Section (H3/H4)
        (2, re.compile(r"^#{3,4}\s+Section\b", re.MULTILINE)),
    ]

    # Hierarchy level number at or below which a new header flushes the parent.
    # level 0–3 trigger a flush; level 4 (Алинея) does not.
    _PARENT_BOUNDARY_LEVEL: ClassVar[int] = 3

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def split(
        self, markdown: str, source_id: str
    ) -> Iterator[tuple[ParentChunk, list[ChildChunk]]]:
        """Yield ``(parent, children)`` pairs from *markdown*.

        A new parent is started at every heading at hierarchy level ≤ 3
        (document title, chapter, section, article).  Sub-paragraphs
        (Алинея, level 4) are accumulated into the current parent.

        The generator is lazy: each pair is yielded as soon as the parent
        text is complete, so large documents are processed with O(parent)
        peak memory.
        """
        logger.debug("splitter.start", source_id=source_id, chars=len(markdown))

        lines: list[str] = markdown.splitlines()

        # Hierarchy stack: list of (level, header_text).  Drives breadcrumbs.
        stack: list[tuple[int, str]] = []

        # Lines accumulated for the current parent-in-progress.
        parent_lines: list[str] = []

        # Monotonic counters for stable UUID generation (reset each call).
        parent_index: int = 0
        total_children: int = 0

        # Track whether we've seen any recognised header at all.
        found_header: bool = False

        # ---- helpers (closures so they share the locals above) --------

        def _detect_level(line: str) -> tuple[int, str] | None:
            """Return ``(level, stripped_line)`` for the first matching pattern."""
            for level, pattern in self.HIERARCHY_PATTERNS:
                if pattern.match(line):
                    return level, line.strip()
            return None

        def _flush_parent(
            buf: list[str],
            p_stack: list[tuple[int, str]],
            p_idx: int,
        ) -> tuple[ParentChunk, list[ChildChunk]] | None:
            """Finalise *buf* as a parent chunk and produce its children."""
            text = "\n".join(buf).strip()
            if not text:
                return None
            path = [ht for _, ht in p_stack]
            parent_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}::parent::{p_idx}"))
            parent = ParentChunk(
                parent_id=parent_id,
                source_id=source_id,
                hierarchy_path=path,
                text=text,
            )
            children = _make_children(text, parent_id, path, p_idx)
            return parent, children

        def _make_children(
            parent_text: str,
            parent_id: str,
            path: list[str],
            p_idx: int,
        ) -> list[ChildChunk]:
            """Split *parent_text* by paragraphs into child chunks."""
            breadcrumb_path = " -> ".join(path) if path else source_id
            breadcrumb = f"[Context: {breadcrumb_path}]"

            paragraphs = [p.strip() for p in parent_text.split("\n\n") if p.strip()]
            if not paragraphs:
                return []

            children: list[ChildChunk] = []
            c_idx: int = 0
            current_parts: list[str] = []
            current_chars: int = 0

            def _emit(parts: list[str]) -> None:
                nonlocal c_idx
                child_text = "\n\n".join(parts)
                if not child_text:
                    return
                c_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{source_id}::child::{p_idx}::{c_idx}",
                    )
                )
                children.append(
                    ChildChunk(
                        child_id=c_id,
                        parent_id=parent_id,
                        source_id=source_id,
                        breadcrumb=breadcrumb,
                        enriched_text=f"{breadcrumb} [Text: {child_text}]",
                        text=child_text,
                    )
                )
                c_idx += 1

            for para in paragraphs:
                if current_parts and current_chars + len(para) > self.CHILD_MAX_CHARS:
                    _emit(current_parts)
                    current_parts = []
                    current_chars = 0
                # Single paragraph larger than the limit — emit it alone.
                if len(para) > self.CHILD_MAX_CHARS and not current_parts:
                    _emit([para])
                    continue
                current_parts.append(para)
                current_chars += len(para)

            if current_parts:
                _emit(current_parts)

            return children

        def _force_split_if_needed(
            buf: list[str],
            p_stack: list[tuple[int, str]],
            p_idx: int,
        ) -> tuple[list[tuple[ParentChunk, list[ChildChunk]]], list[str], int]:
            """Force-split *buf* when it exceeds PARENT_MAX_CHARS.

            Returns ``(emitted_pairs, remaining_lines, next_parent_index)``.
            """
            emitted: list[tuple[ParentChunk, list[ChildChunk]]] = []
            current_buf = buf
            current_idx = p_idx

            while len("\n".join(current_buf)) > self.PARENT_MAX_CHARS:
                # Find split point at the last double-newline within the limit.
                joined = "\n".join(current_buf)
                cut = joined.rfind("\n\n", 0, self.PARENT_MAX_CHARS)
                if cut == -1:
                    # No paragraph boundary found — hard split at char limit.
                    cut = self.PARENT_MAX_CHARS

                chunk_text_str = joined[:cut]
                remainder = joined[cut:].lstrip("\n")

                result = _flush_parent(chunk_text_str.splitlines(), p_stack, current_idx)
                if result is not None:
                    emitted.append(result)
                    current_idx += 1

                current_buf = remainder.splitlines()

            return emitted, current_buf, current_idx

        # ----------------------------------------------------------------
        # Main line-scan loop
        # ----------------------------------------------------------------
        for line in lines:
            detected = _detect_level(line)

            if detected is not None:
                level, header_text = detected
                found_header = True

                if level <= self._PARENT_BOUNDARY_LEVEL:
                    # This header starts a new parent — flush the current buffer.
                    result = _flush_parent(parent_lines, stack, parent_index)
                    if result is not None:
                        total_children += len(result[1])
                        yield result
                        parent_index += 1

                    # Start fresh parent with just this header line.
                    parent_lines = [line]

                    # Update stack: pop entries at this level or deeper, push new.
                    stack = [(lvl, ht) for lvl, ht in stack if lvl < level]
                    stack.append((level, header_text))
                else:
                    # Sub-paragraph (level 4) — accumulate into current parent.
                    parent_lines.append(line)
            else:
                parent_lines.append(line)

            # After each line, check whether we must force-split.
            if len("\n".join(parent_lines)) > self.PARENT_MAX_CHARS:
                emitted, parent_lines, parent_index = _force_split_if_needed(
                    parent_lines, stack, parent_index
                )
                for pair in emitted:
                    total_children += len(pair[1])
                    yield pair

        # ----------------------------------------------------------------
        # No-header fallback: treat entire document as one parent.
        # ----------------------------------------------------------------
        if not found_header and parent_lines:
            full_text = "\n".join(parent_lines).strip()
            if full_text:
                # Force-split if necessary, then flush the final chunk.
                emitted, parent_lines, parent_index = _force_split_if_needed(
                    parent_lines, stack, parent_index
                )
                for pair in emitted:
                    total_children += len(pair[1])
                    yield pair

        # ----------------------------------------------------------------
        # Final flush for the last (or only) parent.
        # ----------------------------------------------------------------
        result = _flush_parent(parent_lines, stack, parent_index)
        if result is not None:
            total_children += len(result[1])
            yield result
            parent_index += 1

        logger.info(
            "splitter.done",
            source_id=source_id,
            parents=parent_index,
            children=total_children,
        )
