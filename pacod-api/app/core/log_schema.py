"""The pacod logging convention (pacod-demo/docs/LOGGING.md) on the standard library alone.

This module imports nothing outside the standard library, so a service that does not carry
the rest of ``app`` can still log the same way: pacod-embeddings copies just this file into
its image (docker/embeddings.Dockerfile), as it copies ``app/core/metrics.py``. The structlog
services go through ``app.core.logging``, which uses everything here.

What it provides:

- ``PacodFormatter``: one event per line, JSON or text, with the five keys of the convention
  (``timestamp``, ``level``, ``service``, ``logger``, ``message``) followed by the event's
  fields, then ``error`` / ``stack_trace`` when an exception is attached.
- ``UvicornAccessFilter``: turns uvicorn's access records into the convention's request line
  (``GET /path 200``, the query string dropped; health checks at debug).
- ``ServiceRotatingFileHandler``: ``<service>.log``, rotated at midnight UTC and at 20 MB to
  ``<service>.<yyyy-mm-dd>.<n>.log.gz``, rotated files kept 30 days and 600 MB in all.
- ``configure``: stdout (and the file when a directory is given) on the root logger, with
  uvicorn's own loggers routed through it.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import IO, Any

# The five keys every event starts with, in this order.
RESERVED = ("timestamp", "level", "service", "logger", "message")

# The LogRecord attribute that carries an event's fields (structlog's keywords, a request
# line's method/path/status). Anything passed with ``extra=`` is a field too.
FIELDS_ATTR = "pacod_fields"

JSON = "json"
TEXT = "text"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# Request paths that are health checks: logged at debug, so the probes Docker runs every few
# seconds stay out of an info log.
HEALTH_PATHS = frozenset({"/health", "/healthz", "/ready", "/readyz", "/live", "/livez"})

# LogRecord's own attributes: never fields. ``color_message`` is uvicorn's coloured copy of
# its message (passed with ``extra=``), ``taskName`` is Python 3.12's.
_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
    FIELDS_ATTR,
}

_COLORS = {
    "trace": "\x1b[90m",
    "debug": "\x1b[90m",
    "info": "\x1b[36m",
    "warn": "\x1b[33m",
    "error": "\x1b[31m",
}
_RESET = "\x1b[0m"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def parse_level(value: str | None) -> int:
    """``debug``, ``info``, ``warn`` (or ``warning``), ``error``, any case; anything else: info."""
    return _LEVELS.get((value or "").strip().lower(), logging.INFO)


def resolve_format(value: str | None, app_env: str | None) -> str:
    """``json`` or ``text``, any case. Unset: text while APP_ENV=development, else json; set to
    anything else: json (the product's default)."""
    fmt = (value or "").strip().lower()
    if fmt in (JSON, TEXT):
        return fmt
    if not fmt and (app_env or "").strip().lower() == "development":
        return TEXT
    return JSON


def service_name(default: str) -> str:
    """The compose service name of this process: ``SERVICE_NAME``, else ``default``."""
    return os.environ.get("SERVICE_NAME", "").strip() or default


# ---------------------------------------------------------------------------
# The event
# ---------------------------------------------------------------------------


def level_name(levelno: int) -> str:
    """Python's level number in the convention's words (warning → warn, critical → error)."""
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    if levelno >= logging.INFO:
        return "info"
    if levelno >= logging.DEBUG:
        return "debug"
    return "trace"


def format_timestamp(created: float) -> str:
    """RFC 3339, UTC, ``Z``, exactly three fraction digits."""
    # The epsilon absorbs float error (x.695 stored as x.69499999…) without rounding up a
    # genuinely later millisecond.
    secs, ms = divmod(int(created * 1000 + 1e-3), 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(secs)) + f".{ms:03d}Z"


def describe_exception(exc_type: type[BaseException] | None, exc: BaseException | None) -> str:
    """One line: ``ValueError: bad input`` (the first line of the message), or the type alone."""
    name = exc_type.__name__ if exc_type is not None else type(exc).__name__
    text = str(exc) if exc is not None else ""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return f"{name}: {first}" if first else name


def build_event(
    *,
    created: float,
    levelno: int,
    service: str,
    logger: str,
    message: str,
    fields: Mapping[str, Any] | None = None,
    exc_info: tuple[Any, Any, Any] | None = None,
    stack_info: str | None = None,
) -> dict[str, Any]:
    """The event as an ordered dict: the five keys, the fields, then error / stack_trace."""
    event: dict[str, Any] = {
        "timestamp": format_timestamp(created),
        "level": level_name(levelno),
        "service": service,
        "logger": logger or "root",
        # Never empty: an event with only fields uses its name (the logger's) as the message.
        "message": message or logger or "event",
    }
    for key, value in (fields or {}).items():
        name = str(key)
        if name in RESERVED:
            name += "_"
        event[name] = value
    if exc_info and exc_info[0] is not None:
        exc_type, exc, tb = exc_info
        if "error" in event:
            # The event's own `error` field would be lost under the exception's.
            event["error_"] = event.pop("error")
        event["error"] = describe_exception(exc_type, exc)
        trace = "".join(traceback.format_exception(exc_type, exc, tb)).rstrip("\n")
        if stack_info:
            trace = f"{trace}\n{stack_info}"
        event["stack_trace"] = trace
    elif stack_info:
        event["stack_trace"] = stack_info
    return event


def render_json(event: Mapping[str, Any]) -> str:
    try:
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        # A value json cannot take even with default=str (a circular structure, a key of an
        # odd type): its repr, rather than losing the event.
        safe = {
            str(k): v if isinstance(v, str | int | float | bool) else repr(v)
            for k, v in event.items()
        }
        return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def _text_value(value: Any) -> str:
    """``key=value``'s value: as is, quoted (JSON string) when it has spaces, quotes or is
    empty; numbers, booleans, lists and objects as JSON."""
    if isinstance(value, str):
        text = value
        needs_quotes = text == "" or '"' in text or any(c.isspace() for c in text)
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            text = repr(value)
        needs_quotes = any(c.isspace() for c in text)
    return json.dumps(text, ensure_ascii=False) if needs_quotes else text


def render_text(event: Mapping[str, Any], *, colors: bool = False) -> str:
    """``<timestamp> <LEVEL> <service> <logger>: <message> key=value ...``, then the stack trace
    on the next lines."""
    level = str(event.get("level", "info"))
    tag = f"{level.upper():<5}"
    if colors:
        tag = f"{_COLORS.get(level, '')}{tag}{_RESET}"
    line = f"{event.get('timestamp')} {tag} {event.get('service')} {event.get('logger')}: {event.get('message')}"
    pairs = [
        f"{k}={_text_value(v)}"
        for k, v in event.items()
        if k not in RESERVED and k != "stack_trace"
    ]
    if pairs:
        line += " " + " ".join(pairs)
    trace = event.get("stack_trace")
    if trace:
        line += "\n" + str(trace)
    return line


def record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The fields of a stdlib record: whatever came with ``extra=``, then FIELDS_ATTR."""
    fields = {
        k: v for k, v in record.__dict__.items() if k not in _RECORD_ATTRS and not k.startswith("_")
    }
    own = getattr(record, FIELDS_ATTR, None)
    if isinstance(own, Mapping):
        fields.update(own)
    return fields


class PacodFormatter(logging.Formatter):
    """A stdlib formatter writing the convention's event, as JSON or as text."""

    def __init__(self, service: str, fmt: str = JSON, *, colors: bool = False) -> None:
        super().__init__()
        self.service = service
        self.fmt = TEXT if fmt == TEXT else JSON
        self.colors = colors and self.fmt == TEXT

    def to_event(self, record: logging.LogRecord) -> dict[str, Any]:
        exc_info = record.exc_info if isinstance(record.exc_info, tuple) else None
        return build_event(
            created=record.created,
            levelno=record.levelno,
            service=self.service,
            logger=record.name,
            message=record.getMessage(),
            fields=record_fields(record),
            exc_info=exc_info,
            stack_info=record.stack_info,
        )

    def format(self, record: logging.LogRecord) -> str:
        event = self.to_event(record)
        if self.fmt == TEXT:
            return render_text(event, colors=self.colors)
        return render_json(event)


# ---------------------------------------------------------------------------
# Request lines
# ---------------------------------------------------------------------------


def is_health_path(path: str) -> bool:
    return path in HEALTH_PATHS or path.startswith("/health/")


def request_line(
    method: str,
    path: str,
    status: int,
    *,
    duration_ms: float | None = None,
    client_ip: str | None = None,
) -> tuple[int, str, dict[str, Any]]:
    """The level, message and fields of a request line. ``path`` loses its query string."""
    path = path.split("?", 1)[0] or "/"
    fields: dict[str, Any] = {"method": method, "path": path, "status": status}
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if client_ip:
        fields["client_ip"] = client_ip
    level = logging.DEBUG if is_health_path(path) else logging.INFO
    return level, f"{method} {path} {status}", fields


def _client_ip(client_addr: object) -> str | None:
    """uvicorn's ``host:port`` (IPv6 too: ``::1:5000``) → the host."""
    text = str(client_addr or "").strip()
    if not text or text == "-":
        return None
    host, sep, port = text.rpartition(":")
    return host if sep and port.isdigit() and host else text


class UvicornAccessFilter(logging.Filter):
    """Rewrites uvicorn's access record (``'%s - "%s %s HTTP/%s" %d'``) into a request line.

    A filter on the ``uvicorn.access`` logger, not the formatter: the level of a health check
    has to drop to debug before the handlers compare it with their own level.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not (isinstance(args, tuple) and len(args) == 5):
            return True
        client_addr, method, full_path, _http_version, status = args
        try:
            status_code = int(str(status))
        except (TypeError, ValueError):
            return True
        level, message, fields = request_line(
            str(method), str(full_path), status_code, client_ip=_client_ip(client_addr)
        )
        record.msg = message
        record.args = ()
        setattr(record, FIELDS_ATTR, fields)
        if level != record.levelno:
            record.levelno = level
            record.levelname = logging.getLevelName(level)
        return True


class HttpxUrlFilter(logging.Filter):
    """httpx logs every request at info as ``HTTP Request: GET <url> "HTTP/1.1 200 OK"`` — the
    URL with its query string, which can carry an API key (Google Custom Search takes ``key=``)
    or a code. A filter on the ``httpx`` logger drops the query string from that URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            isinstance(args, tuple)
            and len(args) >= 2
            and isinstance(record.msg, str)
            and record.msg.startswith("HTTP Request:")
        ):
            url = str(args[1])
            cut = min((i for i in (url.find("?"), url.find("#")) if i != -1), default=-1)
            if cut != -1:
                record.args = (args[0], url[:cut], *args[2:])
        return True


def _install_filter(logger: logging.Logger, cls: type[logging.Filter]) -> None:
    """Exactly one ``cls`` on ``logger``, however often ``configure`` runs."""
    for f in list(logger.filters):
        if isinstance(f, cls):
            logger.removeFilter(f)
    logger.addFilter(cls())


# ---------------------------------------------------------------------------
# The log file
# ---------------------------------------------------------------------------

MAX_BYTES = 20 * 1024 * 1024
RETENTION_DAYS = 30
MAX_TOTAL_BYTES = 600 * 1024 * 1024
# After a rotation that could not move the file away, the size trigger waits this long before
# trying again, instead of trying on every event.
_RETRY_SECONDS = 60.0


def _utc_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, UTC).date()


class ServiceRotatingFileHandler(logging.handlers.BaseRotatingHandler):
    """``<directory>/<service>.log``, rotated to ``<service>.<yyyy-mm-dd>.<n>.log.gz``.

    - At midnight UTC: the file is archived under the day it holds (the day that just ended).
    - When the next line would take it past ``max_bytes``: archived under the same day with
      the next ``n`` (0, 1, 2, ...).
    - Rotated files older than ``retention_days`` (by the date in their name) are deleted,
      then the oldest until all of them together take at most ``max_total_bytes``.

    The next ``n`` is found by looking at the directory, so a rotation never meets an existing
    name and never gives up: the state always moves on. ``clock`` is for tests.

    One process per file: two processes rotating the same file would race (each service has a
    directory of its own).
    """

    def __init__(
        self,
        directory: str | os.PathLike[str],
        service: str,
        *,
        max_bytes: int = MAX_BYTES,
        retention_days: int = RETENTION_DAYS,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = os.fspath(directory)
        self.service = service
        super().__init__(
            os.path.join(self.directory, f"{service}.log"), "a", encoding="utf-8", delay=True
        )
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.max_total_bytes = max_total_bytes
        self._clock = clock
        self._archive_re = re.compile(
            rf"^{re.escape(service)}\.(\d{{4}}-\d{{2}}-\d{{2}})\.(\d+)\.log(?:\.gz)?$"
        )
        self._retry_at = 0.0
        self._period = self._initial_period()

    # -- when ---------------------------------------------------------------

    def _today(self) -> date:
        return _utc_date(self._clock())

    def _initial_period(self) -> date:
        """The day the active file holds: a file left by an earlier run is that run's day."""
        try:
            st = os.stat(self.baseFilename)
        except OSError:
            return self._today()
        return _utc_date(st.st_mtime) if st.st_size > 0 else self._today()

    def _size(self) -> int:
        if self.stream is not None:
            try:
                return self.stream.tell()
            except (OSError, ValueError):
                pass
        try:
            return os.path.getsize(self.baseFilename)
        except OSError:
            return 0

    def _rotation_due(self, nbytes: int) -> bool:
        if self._today() != self._period:
            return True
        size = self._size()
        return size > 0 and size + nbytes > self.max_bytes and self._clock() >= self._retry_at

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        return self._rotation_due(len(self.format(record).encode("utf-8")) + 1)

    def emit(self, record: logging.LogRecord) -> None:
        """Formats once, rotates when the line would take the file past ``max_bytes`` (or the
        day changed), then writes it."""
        try:
            line = self.format(record) + self.terminator
            if self._rotation_due(len(line.encode("utf-8"))):
                self.doRollover()
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(line)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

    # -- how ----------------------------------------------------------------

    def doRollover(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        day = self._period
        self._period = self._today()
        try:
            if os.path.exists(self.baseFilename) and os.path.getsize(self.baseFilename) > 0:
                self._archive(day)
        finally:
            self._prune()

    def _archives(self) -> list[tuple[date, int, str]]:
        """The rotated files, oldest first (by the day in the name, then by ``n``)."""
        found: list[tuple[date, int, str]] = []
        try:
            names: Iterable[str] = os.listdir(self.directory)
        except OSError:
            return found
        for name in names:
            m = self._archive_re.match(name)
            if not m:
                continue
            try:
                day = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            found.append((day, int(m.group(2)), os.path.join(self.directory, name)))
        found.sort()
        return found

    def _next_index(self, day: date) -> int:
        used = [n for d, n, _ in self._archives() if d == day]
        return max(used) + 1 if used else 0

    def _archive(self, day: date) -> None:
        n = self._next_index(day)
        dest = os.path.join(self.directory, f"{self.service}.{day.isoformat()}.{n}.log.gz")
        tmp = dest + ".tmp"
        try:
            with (
                open(self.baseFilename, "rb") as src,
                open(tmp, "wb") as raw,
                # The name inside the archive is the final one, not the temporary file's.
                gzip.GzipFile(filename=os.path.basename(dest)[:-3], mode="wb", fileobj=raw) as out,
            ):
                shutil.copyfileobj(src, out)
            os.replace(tmp, dest)
            os.remove(self.baseFilename)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            # Keep the file moving all the same, uncompressed, so that logging goes on into a
            # fresh file; if even that fails, wait before trying again.
            try:
                os.replace(self.baseFilename, dest.removesuffix(".gz"))
            except OSError:
                self._retry_at = self._clock() + _RETRY_SECONDS
            sys.stderr.write(f"log rotation of {self.baseFilename} failed: {exc}\n")

    def _prune(self) -> None:
        archives = self._archives()
        cutoff = self._today() - timedelta(days=self.retention_days)
        kept: list[tuple[str, int]] = []
        for day, _n, path in archives:
            if day < cutoff:
                self._remove(path)
                continue
            try:
                kept.append((path, os.path.getsize(path)))
            except OSError:
                continue
        total = sum(size for _, size in kept)
        while kept and total > self.max_total_bytes:
            path, size = kept.pop(0)
            self._remove(path)
            total -= size

    @staticmethod
    def _remove(path: str) -> None:
        with contextlib.suppress(OSError):
            os.remove(path)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _isatty(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def route_uvicorn(*, access_log: bool = True) -> None:
    """uvicorn's loggers through the root handlers (uvicorn gives them handlers of its own and
    turns propagation off), its access records as request lines.

    ``access_log=False`` silences uvicorn's access log, for a service whose middleware logs
    its requests (with their duration) instead.
    """
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(logging.NOTSET)
    access = logging.getLogger("uvicorn.access")
    _install_filter(access, UvicornAccessFilter)
    if not access_log:
        access.setLevel(logging.CRITICAL + 1)


def configure(
    *,
    service: str,
    level: int,
    fmt: str,
    log_dir: str = "",
    access_log: bool = True,
    stream: IO[str] | None = None,
) -> None:
    """The root logger: stdout in ``fmt`` (colours only on a terminal), and, when ``log_dir``
    is set, ``<log_dir>/<service>.log`` in JSON."""
    out = stream if stream is not None else sys.stdout
    console = logging.StreamHandler(out)
    console.setFormatter(PacodFormatter(service, fmt, colors=fmt == TEXT and _isatty(out)))
    console.setLevel(level)
    handlers: list[logging.Handler] = [console]

    file_error: tuple[str, OSError] | None = None
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = ServiceRotatingFileHandler(log_dir, service)
            file_handler.setFormatter(PacodFormatter(service, JSON))
            file_handler.setLevel(level)
            handlers.append(file_handler)
        except OSError as exc:
            # A directory that cannot be written does not stop the service.
            file_error = (log_dir, exc)

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        if isinstance(old, ServiceRotatingFileHandler):
            old.close()
    root.setLevel(level)
    for h in handlers:
        root.addHandler(h)
    route_uvicorn(access_log=access_log)
    _install_filter(logging.getLogger("httpx"), HttpxUrlFilter)

    if file_error is not None:
        logging.getLogger(__name__).warning(
            "log_file_unavailable", extra={"path": file_error[0], "error": str(file_error[1])}
        )


def configure_from_env(default_service: str, *, access_log: bool = True) -> None:
    """``configure`` from the environment alone (APP_LOG_LEVEL, APP_LOG_FORMAT, APP_ENV,
    APP_LOG_DIR, SERVICE_NAME), for a service without ``app.core.config``. An unset APP_ENV
    counts as development, as in ``app.core.config``."""
    env = os.environ
    configure(
        service=service_name(default_service),
        level=parse_level(env.get("APP_LOG_LEVEL")),
        fmt=resolve_format(env.get("APP_LOG_FORMAT"), env.get("APP_ENV", "development")),
        log_dir=env.get("APP_LOG_DIR", "").strip(),
        access_log=access_log,
    )
