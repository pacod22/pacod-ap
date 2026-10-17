"""Parse local document files (PDF, DOCX, TXT) from the documents/ folder.

Each file becomes one ParsedDoc. Language is inferred from the filename
suffix (_en / -en) and defaults to Bulgarian.
Temporary Word lock files (~$...) are silently skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.logging import get_logger
from app.core.models import Language
from ingestion.pipeline import ParsedDoc

log = get_logger(__name__)

DOCUMENTS_ROOT = Path(__file__).resolve().parent.parent.parent / "documents"


def _filename_to_source_id(path: Path) -> str:
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", path.stem).strip("-").lower()
    return f"doc-{stem}"


def _filename_to_title(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ").title()


def _detect_language(path: Path) -> Language:
    name = path.stem.lower()
    if name.endswith(("_en", "-en")) or "_en_" in name or "-en-" in name:
        return Language.EN
    return Language.BG


def _parse_pdf(path: Path) -> str:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _parse_pdf_layout(path: Path) -> str:
    """Use LayoutParser (marker-pdf) for layout-aware PDF → Markdown.
    Falls back to pypdf if marker is not installed (CPU containers).
    """
    try:
        from ingestion.parser import LayoutParser  # lazy import avoids slow model load

        parser = LayoutParser()
        return parser.parse(path)
    except ImportError:
        log.warning("layout_parser_unavailable_pypdf_fallback", path=str(path))
        return _parse_pdf(path)


def _parse_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paras)


def _parse_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_html(path: Path) -> str:
    from bs4 import BeautifulSoup

    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(
        ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]
    ):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.find("body")
    if root is None:
        return ""
    import re

    text = root.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_md(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _scalar_md(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _json_to_md_str(data: object) -> str:
    """Render parsed JSON as a nested Markdown bullet list (keys bolded)."""
    lines: list[str] = []

    def render(value: object, depth: int) -> None:
        pad = "  " * depth
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, dict | list):
                    lines.append(f"{pad}- **{k}**:")
                    render(v, depth + 1)
                else:
                    lines.append(f"{pad}- **{k}**: {_scalar_md(v)}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict | list):
                    lines.append(f"{pad}-")
                    render(item, depth + 1)
                else:
                    lines.append(f"{pad}- {_scalar_md(item)}")
        else:
            lines.append(f"{pad}- {_scalar_md(value)}")

    render(data, 0)
    return "\n".join(lines)


def _parse_json_raw(path: Path) -> str:
    """Keep JSON as JSON (mode 1, default). Normalises formatting for readable
    chunking but does not convert to Markdown. Falls back to raw text if the
    file is not valid JSON."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, ValueError):
        log.warning("json_parse_failed_keeping_raw", path=str(path))
        return raw


def _parse_json_md(path: Path) -> str:
    """Convert JSON to Markdown (mode 2). Falls back to raw text on parse error."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        return _json_to_md_str(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        log.warning("json_to_md_failed_keeping_raw", path=str(path))
        return raw


_PARSERS = {
    ".pdf": _parse_pdf_layout,
    ".docx": _parse_docx,
    ".txt": _parse_txt,
    ".html": _parse_html,
    ".htm": _parse_html,
    ".md": _parse_md,
    ".json": _parse_json_raw,  # default mode: keep as JSON (override via json_mode)
}

# CPU-safe parser table: uses pypdf for PDFs instead of marker/Surya.
# Used by the CPU ingestion pipeline to avoid loading 2-4 GB of layout models.
_PARSERS_CPU = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".txt": _parse_txt,
    ".html": _parse_html,
    ".htm": _parse_html,
    ".md": _parse_md,
    ".json": _parse_json_raw,
}


def parse_local_doc(
    path: Path, legal_year: int, use_layout_parser: bool = True, json_mode: str = "keep"
) -> ParsedDoc | None:
    """Parse a single local document file into a ParsedDoc.

    use_layout_parser=True  — use marker-pdf for PDFs (layout-aware, GPU pipeline)
    use_layout_parser=False — use pypdf for PDFs (text-only, CPU pipeline)
    json_mode="keep"        — keep JSON files as JSON (default)
    json_mode="md"          — convert JSON files to Markdown
    """
    parsers = _PARSERS if use_layout_parser else _PARSERS_CPU
    suffix = path.suffix.lower()
    if suffix == ".json":
        parser = _parse_json_md if json_mode == "md" else _parse_json_raw
    else:
        parser = parsers.get(suffix)
    if parser is None:
        return None
    log.debug("local_doc_parse_start", path=str(path), suffix=path.suffix.lower())
    try:
        body = parser(path)
    except Exception as e:
        log.error(
            "local_doc_parse_failed",
            path=str(path),
            error=str(e),
            exc_info=True,
        )
        return None

    if not body.strip():
        log.warning("local_doc_empty", path=str(path))
        return None

    log.info("local_doc_ok", path=str(path), chars=len(body))
    return ParsedDoc(
        source_id=_filename_to_source_id(path),
        title=_filename_to_title(path),
        source_type="document",
        legal_year=legal_year,
        language=_detect_language(path),
        url=None,
        body=body.strip(),
        file_path=path.name,
    )


def iter_local_docs(root: Path, legal_year: int, use_layout_parser: bool = True) -> list[ParsedDoc]:
    """Walk root and parse every supported document file.

    use_layout_parser=False switches PDF parsing to pypdf (CPU pipeline).
    """
    if not root.exists():
        return []
    parsers = _PARSERS if use_layout_parser else _PARSERS_CPU
    docs: list[ParsedDoc] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$"):  # Word temp lock files
            continue
        if path.suffix.lower() not in parsers:
            continue
        doc = parse_local_doc(path, legal_year, use_layout_parser=use_layout_parser)
        if doc:
            docs.append(doc)
    return docs
