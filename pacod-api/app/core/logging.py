"""Logging: structlog on top of the standard library, in the pacod logging convention
(pacod-demo/docs/LOGGING.md).

Every event, structlog's and the libraries' (uvicorn, httpx, sqlalchemy, ...) alike, is one line:

  {"timestamp":"2026-10-23T06:41:07.695Z","level":"info","service":"ap-api",
   "logger":"app.main","message":"migrations_done","applied":0,"skipped":3}

  2026-10-23T06:41:07.695Z INFO  ap-api app.main: migrations_done applied=0 skipped=3

Output targets:
  - stdout: always; ``APP_LOG_FORMAT`` = ``json`` | ``text`` (unset: text while
    ``APP_ENV=development``, else json). Colours only when stdout is a terminal.
  - file: when ``APP_LOG_DIR`` is set; always JSON, ``<APP_LOG_DIR>/<service>.log``, rotated at
    midnight UTC and at 20 MB to ``<service>.<yyyy-mm-dd>.<n>.log.gz``, rotated files kept 30 days
    and 600 MB in all (``app.core.log_schema.ServiceRotatingFileHandler``).

``APP_LOG_LEVEL``: ``debug`` | ``info`` | ``warn`` (``warning``) | ``error``, any case; anything
else means info. ``service`` is ``SERVICE_NAME`` (compose sets it to the service name), else the
entry point's own default.

uvicorn's loggers go through the same handlers; its access log becomes the convention's request
line (``GET /api/v1/quota 200``, no query string, health checks at debug).

The format itself lives in ``app.core.log_schema`` (standard library only, so pacod-embeddings
can use it without structlog or the settings).
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core import log_schema
from app.core.config import get_settings

# Re-exported for the services' request middleware.
request_line = log_schema.request_line


def _to_stdlib(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
    """structlog's last processor: hand the event to the stdlib logger, its keywords (and the
    bound contextvars, merged before it) as the record's fields."""
    message = str(event_dict.pop("event", "") or "")
    args = event_dict.pop("positional_args", None)
    if args:
        try:
            message = message % args
        except (TypeError, ValueError):
            message = f"{message} {args!r}"
    kwargs: dict[str, Any] = {"msg": message}
    exc_info = event_dict.pop("exc_info", None)
    if exc_info:
        kwargs["exc_info"] = exc_info
    if event_dict.pop("stack_info", False):
        kwargs["stack_info"] = True
    kwargs["extra"] = {log_schema.FIELDS_ATTR: event_dict}
    return kwargs


def configure_logging(service: str = "ap-api", *, access_log: bool = True) -> None:
    """Configure structlog and the root logger for this process.

    ``service`` is the name when ``SERVICE_NAME`` is unset (each entry point passes its own).
    ``access_log=False`` silences uvicorn's access log, for a service that logs its requests
    in a middleware instead (ap-admin-api).
    """
    settings = get_settings()
    level = log_schema.parse_level(settings.app_log_level)
    fmt = log_schema.resolve_format(settings.app_log_format, settings.app_env)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            # Bound context first, so the event's own keyword wins over a context field.
            structlog.contextvars.merge_contextvars,
            _to_stdlib,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    log_schema.configure(
        service=log_schema.service_name(service),
        level=level,
        fmt=fmt,
        log_dir=(settings.app_log_dir or "").strip(),
        access_log=access_log,
    )


def log_request(
    logger: Any,
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: float | None = None,
    client_ip: str | None = None,
) -> None:
    """One request line: ``<METHOD> <path> <status>`` with method, path (no query string),
    status, duration_ms and client_ip; health checks at debug."""
    level, message, fields = log_schema.request_line(
        method, path, status, duration_ms=duration_ms, client_ip=client_ip
    )
    emit = logger.debug if level == logging.DEBUG else logger.info
    emit(message, **fields)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
