"""Layout-aware PDF parser backed by marker-pdf.

Wraps the marker-pdf ``PdfConverter`` to produce clean Markdown from PDF
files.  All heavy imports (marker, torch, surya) are deferred to the first
``parse()`` call so that importing this module is cheap.

Fallback behaviour: when marker fails after all retries, the parser falls back
to the plain-text extraction already used in ``ingestion/sources/local_docs.py``
(``pypdf.PdfReader``).

Note on async usage
-------------------
``parse()`` and ``parse_bytes()`` are synchronous and CPU-bound.  Calling them
directly from an async context (e.g. the admin upload route) would block the
event loop during the marker conversion and the ``time.sleep()`` back-off.
Use the async wrappers ``parse_async()`` / ``parse_bytes_async()`` instead —
they delegate to a thread-pool executor so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import functools
import sys
import tempfile
import time
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class LayoutParser:
    """Layout-aware PDF → Markdown converter backed by marker-pdf."""

    _models: dict | None = None  # class-level lazy-loaded singleton

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse(self, path: Path) -> str:
        """Convert a PDF file to Markdown. Returns a Markdown string."""
        logger.info("parser_start", path=str(path), size_bytes=path.stat().st_size)
        sys.stdout.flush()
        start = time.monotonic()
        markdown = self._convert(path)
        elapsed = time.monotonic() - start
        logger.info(
            "parser_done",
            path=str(path),
            chars=len(markdown),
            elapsed_s=round(elapsed, 2),
        )
        sys.stdout.flush()
        return markdown

    def parse_bytes(self, data: bytes) -> str:
        """Convert PDF bytes to Markdown.

        Writes *data* to a temporary file, calls ``parse()``, then deletes the
        temporary file.  ``delete=False`` is used so that the file can be
        opened by name on all platforms (including Windows).
        """
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            return self.parse(tmp_path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

    async def parse_async(self, path: Path) -> str:
        """Async wrapper — runs ``parse()`` in a thread-pool executor.

        Use this from async callers (e.g. the admin upload route) to avoid
        blocking the event loop during marker conversion and retry back-off.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(self.parse, path))

    async def parse_bytes_async(self, data: bytes) -> str:
        """Async wrapper — runs ``parse_bytes()`` in a thread-pool executor.

        Use this from async callers (e.g. the admin upload route) to avoid
        blocking the event loop during marker conversion and retry back-off.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(self.parse_bytes, data))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_models(self) -> None:
        """Lazy-load marker models (expensive, done once per process).

        marker-pdf downloads Surya layout models (~2–4 GB) from Hugging Face
        on first call if they are not already cached. If the container has no
        internet access or insufficient disk/memory, this is where the process
        will appear to hang. Check HF_HOME / TRANSFORMERS_CACHE and ensure the
        models are pre-downloaded into the image or a mounted volume.
        """
        if LayoutParser._models is not None:
            return

        logger.info(
            "parser_models_loading", hint="this may download ~2-4 GB of models on first run"
        )
        sys.stdout.flush()
        t0 = time.monotonic()
        try:
            # Deferred imports so importing this module does not pull in torch/surya.
            from marker.models import create_model_dict  # noqa: PLC0415

            LayoutParser._models = create_model_dict()
            logger.info("parser_models_loaded", elapsed_s=round(time.monotonic() - t0, 1))
        except Exception:
            logger.error(
                "parser_models_load_failed",
                elapsed_s=round(time.monotonic() - t0, 1),
                exc_info=True,
            )
            sys.stdout.flush()
            raise

    def _convert(self, path: Path) -> str:
        """Inner conversion with retry and pypdf fallback."""
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            logger.debug("parser_attempt_start", path=str(path), attempt=attempt)
            sys.stdout.flush()
            try:
                self._load_models()
                from marker.converters.pdf import PdfConverter  # noqa: PLC0415

                converter = PdfConverter(artifact_dict=LayoutParser._models)
                rendered = converter(str(path))
                logger.debug(
                    "parser_attempt_ok",
                    path=str(path),
                    attempt=attempt,
                    markdown_chars=len(rendered.markdown),
                )
                return rendered.markdown
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "parser_attempt_failed",
                    path=str(path),
                    attempt=attempt,
                    error=str(exc),
                    exc_info=True,
                )
                sys.stdout.flush()
                if attempt < self.max_retries:
                    wait = 2**attempt
                    logger.debug("parser_retry_wait", seconds=wait)
                    time.sleep(wait)

        # All retries exhausted — fall back to pypdf plain-text extraction.
        logger.error(
            "parser_all_retries_failed",
            path=str(path),
            attempts=self.max_retries,
            last_error=str(last_exc),
            fallback="pypdf",
            exc_info=True,
        )
        sys.stdout.flush()
        return self._pypdf_fallback(path)

    @staticmethod
    def _pypdf_fallback(path: Path) -> str:
        """Extract raw text via pypdf (already in requirements.txt)."""
        logger.info("parser_pypdf_fallback_start", path=str(path))
        try:
            import pypdf  # noqa: PLC0415

            reader = pypdf.PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(p.strip() for p in pages if p.strip())
            logger.info("parser_pypdf_fallback_done", path=str(path), chars=len(text))
            return text
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "parser_pypdf_fallback_failed",
                path=str(path),
                error=str(exc),
                exc_info=True,
            )
            return ""
