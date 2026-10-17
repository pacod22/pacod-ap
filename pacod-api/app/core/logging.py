"""Structured logging via structlog + stdlib.

Output targets:
  - stdout  — always; ConsoleRenderer (dev) or JSONRenderer (staging/prod)
  - file    — when APP_LOG_DIR is set; always JSON; rotates at 20 MB or midnight,
              whichever comes first; compressed backups saved as .tar.gz

Log level is controlled by APP_LOG_LEVEL (default: DEBUG).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import tarfile
from pathlib import Path

import structlog

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Custom rotating handler
# ---------------------------------------------------------------------------


class _CompressingRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Rotates at midnight UTC **or** when the active file reaches *max_bytes*.

    Rotated files are compressed into ``.tar.gz`` archives next to the active
    log file.  Old archives beyond *backup_count* are deleted automatically.
    """

    def __init__(self, filename: str, max_bytes: int, backup_count: int = 30) -> None:
        super().__init__(
            filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
            utc=True,
        )
        self.maxBytes = max_bytes
        self.rotator = self._tar_gz_rotator
        self.namer = self._tar_gz_namer

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if self.stream and self.stream.tell() >= self.maxBytes:
            return True
        return super().shouldRollover(record)  # type: ignore[return-value]

    @staticmethod
    def _tar_gz_namer(name: str) -> str:
        return name + ".tar.gz"

    @staticmethod
    def _tar_gz_rotator(source: str, dest: str) -> None:
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(source, arcname=os.path.basename(source))
        os.remove(source)

    def getFilesToDelete(self) -> list[str]:
        """Return old .tar.gz archives to delete once backup_count is exceeded."""
        dir_name, base_name = os.path.split(self.baseFilename)
        prefix = base_name + "."
        plen = len(prefix)
        result: list[str] = []
        for fn in os.listdir(dir_name):
            if not fn.startswith(prefix):
                continue
            # Strip the .tar.gz suffix before matching the time-rotation pattern.
            suffix = fn[plen:].removesuffix(".tar.gz")
            if self.extMatch.match(suffix):
                result.append(os.path.join(dir_name, fn))
        result.sort()
        if len(result) <= self.backupCount:
            return []
        return result[: len(result) - self.backupCount]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    settings = get_settings()
    level_name = settings.app_log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    dev_mode = settings.app_env == "development"

    # Processors shared by structlog-native and foreign (third-party) log records.
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    # structlog delegates final rendering to stdlib handlers via wrap_for_formatter.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    def _formatter(*, colors: bool, json_output: bool) -> structlog.stdlib.ProcessorFormatter:
        if json_output:
            renderer = structlog.processors.JSONRenderer()
        else:
            renderer = structlog.dev.ConsoleRenderer(colors=colors)
        return structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
            foreign_pre_chain=shared_processors,
        )

    # --- Console handler (stdout) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_formatter(colors=dev_mode, json_output=not dev_mode))
    console_handler.setLevel(level)

    handlers: list[logging.Handler] = [console_handler]

    # --- File handler (optional, enabled by APP_LOG_DIR) ---
    log_dir: str = getattr(settings, "app_log_dir", "")
    if log_dir:
        log_path = Path(log_dir) / "pacod-agent.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = _CompressingRotatingFileHandler(
                str(log_path),
                max_bytes=20 * 1024 * 1024,  # 20 MB
                backup_count=30,
            )
            file_handler.setFormatter(_formatter(colors=False, json_output=True))
            file_handler.setLevel(level)
            handlers.append(file_handler)
        except OSError as exc:
            # Don't abort startup if the log directory is not writable.
            logging.warning("log_file_unavailable path=%s error=%s", log_path, exc)

    # --- Root logger ---
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
