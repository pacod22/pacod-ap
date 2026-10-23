"""The pacod logging convention (pacod-demo/docs/LOGGING.md): app/core/log_schema.py and
app/core/logging.py — the event, the format and level switches, uvicorn's access lines, the
admin request line and the rotating log file (on a fake clock, never a sleep)."""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import re
from datetime import UTC, datetime

import pytest
import structlog

from app.core import log_schema
from app.core.log_schema import (
    PacodFormatter,
    ServiceRotatingFileHandler,
    UvicornAccessFilter,
    parse_level,
    resolve_format,
)

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
ANSI_RE = re.compile(r"\x1b\[")


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging rewires the root and uvicorn loggers and structlog: put them back."""
    root = logging.getLogger()
    saved_root = (list(root.handlers), root.level)
    saved = {}
    for name in (*log_schema.UVICORN_LOGGERS, "httpx"):
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), lg.propagate, lg.level, list(lg.filters), lg.disabled)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        if h not in saved_root[0]:
            h.close()
    for h in saved_root[0]:
        root.addHandler(h)
    root.setLevel(saved_root[1])
    for name, (handlers, propagate, level, filters, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.propagate = propagate
        lg.setLevel(level)
        lg.filters[:] = filters
        lg.disabled = disabled
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


def _record(
    msg="hello",
    *,
    name="app.x",
    level=logging.INFO,
    args=(),
    exc_info=None,
    created=None,
    **extra,
):
    rec = logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)
    if created is not None:
        rec.created = created
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def _json(fmt: PacodFormatter, rec: logging.LogRecord) -> dict:
    return json.loads(fmt.format(rec))


# ---------------------------------------------------------------------------
# The event
# ---------------------------------------------------------------------------


class TestEvent:
    def test_five_keys_first_in_order_then_fields(self):
        rec = _record("pacod is up", **{log_schema.FIELDS_ATTR: {"public": "0.0.0.0:8070", "n": 3}})
        line = PacodFormatter("ap-api").format(rec)
        event = json.loads(line)
        assert list(event)[:5] == ["timestamp", "level", "service", "logger", "message"]
        assert event["service"] == "ap-api"
        assert event["logger"] == "app.x"
        assert event["message"] == "pacod is up"
        assert event["public"] == "0.0.0.0:8070"
        assert event["n"] == 3  # numbers stay numbers
        assert "\n" not in line and ": " not in line  # one compact line

    def test_timestamp_is_rfc3339_utc_millis(self):
        created = datetime(2026, 10, 23, 6, 41, 7, 695000, tzinfo=UTC).timestamp()
        event = _json(PacodFormatter("s"), _record(created=created))
        assert event["timestamp"] == "2026-10-23T06:41:07.695Z"
        assert TS_RE.match(_json(PacodFormatter("s"), _record())["timestamp"])
        # Exactly three digits, even on a whole second.
        whole = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp()
        assert _json(PacodFormatter("s"), _record(created=whole))["timestamp"] == (
            "2026-01-02T03:04:05.000Z"
        )

    @pytest.mark.parametrize(
        ("levelno", "name"),
        [
            (5, "trace"),
            (logging.DEBUG, "debug"),
            (logging.INFO, "info"),
            (logging.WARNING, "warn"),
            (logging.ERROR, "error"),
            (logging.CRITICAL, "error"),
        ],
    )
    def test_levels(self, levelno, name):
        assert _json(PacodFormatter("s"), _record(level=levelno))["level"] == name

    def test_reserved_names_get_a_trailing_underscore(self):
        fields = {"message": "m", "level": "l", "service": "s", "logger": "g", "timestamp": "t"}
        event = _json(PacodFormatter("ap-api"), _record("real", **{log_schema.FIELDS_ATTR: fields}))
        assert event["message"] == "real" and event["message_"] == "m"
        assert event["level"] == "info" and event["level_"] == "l"
        assert event["service"] == "ap-api" and event["service_"] == "s"
        assert event["logger_"] == "g" and event["timestamp_"] == "t"

    def test_extra_fields_of_a_library_record_are_fields(self):
        event = _json(PacodFormatter("s"), _record("x", k=1, color_message="ansi"))
        assert event["k"] == 1
        assert "color_message" not in event  # uvicorn's coloured copy of the message

    def test_message_is_never_empty(self):
        assert _json(PacodFormatter("s"), _record(""))["message"] == "app.x"

    def test_exception_gives_error_and_stack_trace(self):
        try:
            raise ValueError("bad input\nmore detail")
        except ValueError:
            import sys

            rec = _record("boom", level=logging.ERROR, exc_info=sys.exc_info())
        event = _json(PacodFormatter("s"), rec)
        assert event["error"] == "ValueError: bad input"
        assert event["stack_trace"].startswith("Traceback (most recent call last):")
        assert "ValueError: bad input" in event["stack_trace"]
        assert list(event)[-2:] == ["error", "stack_trace"]

    def test_exception_keeps_an_own_error_field(self):
        try:
            raise KeyError("k")
        except KeyError:
            import sys

            rec = _record(
                "boom", exc_info=sys.exc_info(), **{log_schema.FIELDS_ATTR: {"error": "mine"}}
            )
        event = _json(PacodFormatter("s"), rec)
        assert event["error"] == "KeyError: 'k'"
        assert event["error_"] == "mine"

    def test_text_line(self):
        created = datetime(2026, 10, 23, 6, 41, 7, 695000, tzinfo=UTC).timestamp()
        rec = _record(
            "pacod-id is up",
            name="pacod_id::app",
            created=created,
            **{log_schema.FIELDS_ATTR: {"public": "0.0.0.0:8070", "note": "a b", "n": 2}},
        )
        line = PacodFormatter("pacod-id", "text").format(rec)
        assert line == (
            "2026-10-23T06:41:07.695Z INFO  pacod-id pacod_id::app: pacod-id is up "
            'public=0.0.0.0:8070 note="a b" n=2'
        )

    def test_text_stack_trace_on_the_next_lines(self):
        try:
            raise RuntimeError("nope")
        except RuntimeError:
            import sys

            rec = _record("boom", level=logging.ERROR, exc_info=sys.exc_info())
        first, *rest = PacodFormatter("s", "text").format(rec).split("\n")
        assert first.endswith('boom error="RuntimeError: nope"')
        assert rest[0] == "Traceback (most recent call last):"

    def test_no_colours_unless_asked(self):
        assert not ANSI_RE.search(PacodFormatter("s", "text").format(_record()))
        assert ANSI_RE.search(PacodFormatter("s", "text", colors=True).format(_record()))
        # Never in JSON, whatever is asked.
        assert not ANSI_RE.search(PacodFormatter("s", "json", colors=True).format(_record()))


# ---------------------------------------------------------------------------
# The switches
# ---------------------------------------------------------------------------


class TestSwitches:
    @pytest.mark.parametrize(
        ("value", "level"),
        [
            ("debug", logging.DEBUG),
            ("DEBUG", logging.DEBUG),
            ("info", logging.INFO),
            ("warn", logging.WARNING),
            ("Warning", logging.WARNING),
            ("error", logging.ERROR),
            ("verbose", logging.INFO),
            ("", logging.INFO),
            (None, logging.INFO),
        ],
    )
    def test_level(self, value, level):
        assert parse_level(value) == level

    @pytest.mark.parametrize(
        ("value", "env", "fmt"),
        [
            ("json", "development", "json"),
            ("TEXT", "production", "text"),
            ("", "development", "text"),
            (None, "staging", "json"),
            ("", "production", "json"),
            ("yaml", "development", "json"),  # set but unknown: the default, json
            ("yaml", "production", "json"),
        ],
    )
    def test_format(self, value, env, fmt):
        assert resolve_format(value, env) == fmt

    def _configure(self, monkeypatch, **env):
        monkeypatch.setenv("APP_LOG_DIR", "")
        monkeypatch.delenv("SERVICE_NAME", raising=False)
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        from app.core.logging import configure_logging, get_logger

        configure_logging("ap-test")
        return get_logger("tests.switch")

    def test_configure_logging_json_in_production(self, monkeypatch, capsys):
        log = self._configure(
            monkeypatch, APP_ENV="production", APP_LOG_FORMAT="", APP_LOG_LEVEL="info"
        )
        log.debug("hidden")
        log.info("shown", k="v")
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["service"] == "ap-test"
        assert event["logger"] == "tests.switch"
        assert event["message"] == "shown" and event["k"] == "v"

    def test_configure_logging_text_when_asked(self, monkeypatch, capsys):
        log = self._configure(monkeypatch, APP_ENV="production", APP_LOG_FORMAT="Text")
        log.warning("careful", n=1)
        out = capsys.readouterr().out
        assert re.match(r"^\S+Z WARN  ap-test tests\.switch: careful n=1$", out.strip())
        assert not ANSI_RE.search(out)  # captured stdout is not a terminal

    def test_configure_logging_text_by_default_in_development(self, monkeypatch, capsys):
        log = self._configure(monkeypatch, APP_ENV="development", APP_LOG_FORMAT=None)
        log.info("dev")
        assert " INFO  ap-test tests.switch: dev" in capsys.readouterr().out

    def test_service_name_from_env(self, monkeypatch, capsys):
        log = self._configure(monkeypatch, APP_ENV="production", SERVICE_NAME="ap-ingestion-cpu")
        log.info("x")
        assert json.loads(capsys.readouterr().out)["service"] == "ap-ingestion-cpu"

    def test_level_warning_means_warn(self, monkeypatch, capsys):
        log = self._configure(monkeypatch, APP_ENV="production", APP_LOG_LEVEL="WARNING")
        log.info("no")
        log.warning("yes")
        lines = capsys.readouterr().out.strip().splitlines()
        assert [json.loads(line)["message"] for line in lines] == ["yes"]

    def test_structlog_context_exception_and_args(self, monkeypatch, capsys):
        log = self._configure(monkeypatch, APP_ENV="production", APP_LOG_LEVEL="debug")
        structlog.contextvars.bind_contextvars(request_id="r-1", user_id="ctx")
        try:
            raise ValueError("bad input")
        except ValueError:
            log.exception("failed", user_id="evt")
        log.debug("count %d", 3)
        a, b = (json.loads(line) for line in capsys.readouterr().out.strip().splitlines())
        assert a["level"] == "error"
        assert a["request_id"] == "r-1"
        assert a["user_id"] == "evt"  # the event's own field wins over the context
        assert a["error"] == "ValueError: bad input"
        assert "Traceback" in a["stack_trace"]
        assert b["message"] == "count 3" and b["level"] == "debug"

    def test_stdlib_records_share_the_format(self, monkeypatch, capsys):
        self._configure(monkeypatch, APP_ENV="production")
        logging.getLogger("httpx").warning("slow %s", "call", extra={"ms": 12})
        event = json.loads(capsys.readouterr().out)
        assert event["logger"] == "httpx" and event["level"] == "warn"
        assert event["message"] == "slow call" and event["ms"] == 12


# ---------------------------------------------------------------------------
# uvicorn and request lines
# ---------------------------------------------------------------------------


def _access(logger: logging.Logger, client: str, method: str, path: str, status: int) -> None:
    logger.info('%s - "%s %s HTTP/%s" %d', client, method, path, "1.1", status)


class TestRequestLines:
    def _setup(self, level=logging.INFO, access_log=True):
        out = io.StringIO()
        # What uvicorn's dictConfig leaves behind: its own handler, no propagation.
        for name in log_schema.UVICORN_LOGGERS:
            lg = logging.getLogger(name)
            lg.handlers[:] = [logging.StreamHandler(io.StringIO())]
            lg.propagate = name == "uvicorn.error"
            lg.setLevel(logging.INFO)
        log_schema.configure(
            service="ap-api", level=level, fmt="json", access_log=access_log, stream=out
        )
        return out

    def _events(self, out: io.StringIO) -> list[dict]:
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_access_record_becomes_a_request_line(self):
        out = self._setup()
        _access(
            logging.getLogger("uvicorn.access"),
            "172.18.0.5:51234",
            "GET",
            "/api/v1/quota?token=s3cret&x=1",
            200,
        )
        (event,) = self._events(out)
        assert event["logger"] == "uvicorn.access"
        assert event["message"] == "GET /api/v1/quota 200"
        assert event["method"] == "GET"
        assert event["path"] == "/api/v1/quota"
        assert event["status"] == 200
        assert event["client_ip"] == "172.18.0.5"
        assert "s3cret" not in out.getvalue()

    def test_ipv6_client_and_no_client(self):
        out = self._setup()
        acc = logging.getLogger("uvicorn.access")
        _access(acc, "::1:5000", "POST", "/x", 201)
        _access(acc, "", "GET", "/y", 404)
        a, b = self._events(out)
        assert a["client_ip"] == "::1"
        assert "client_ip" not in b and b["status"] == 404

    def test_health_checks_are_debug(self):
        out = self._setup(level=logging.INFO)
        acc = logging.getLogger("uvicorn.access")
        _access(acc, "127.0.0.1:1", "GET", "/health", 200)
        _access(acc, "127.0.0.1:1", "GET", "/healthz", 200)
        assert out.getvalue() == ""
        out = self._setup(level=logging.DEBUG)
        _access(logging.getLogger("uvicorn.access"), "127.0.0.1:1", "GET", "/health", 200)
        (event,) = self._events(out)
        assert event["level"] == "debug" and event["message"] == "GET /health 200"

    def test_uvicorn_loggers_go_through_the_root_handlers(self):
        out = self._setup()
        logging.getLogger("uvicorn.error").info(
            "Started server process [%d]", 7, extra={"color_message": "c"}
        )
        (event,) = self._events(out)
        assert (
            event["logger"] == "uvicorn.error" and event["message"] == "Started server process [7]"
        )
        for name in log_schema.UVICORN_LOGGERS:
            lg = logging.getLogger(name)
            assert lg.handlers == [] and lg.propagate

    def test_access_log_off(self):
        out = self._setup(access_log=False)
        _access(logging.getLogger("uvicorn.access"), "1.2.3.4:1", "GET", "/api/v1/users", 200)
        assert out.getvalue() == ""

    def test_filter_added_once(self):
        self._setup()
        self._setup()
        filters = logging.getLogger("uvicorn.access").filters
        assert sum(isinstance(f, UvicornAccessFilter) for f in filters) == 1

    def test_other_access_shapes_pass_untouched(self):
        rec = _record("custom %s", args=("x",), name="uvicorn.access")
        assert UvicornAccessFilter().filter(rec)
        assert rec.getMessage() == "custom x"

    def test_httpx_request_urls_lose_their_query_string(self):
        out = self._setup()
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"',
            "GET",
            "https://www.googleapis.com/customsearch/v1?key=AIzaSECRET&cx=1&q=x",
            "HTTP/1.1",
            200,
            "OK",
        )
        (event,) = self._events(out)
        assert event["message"] == (
            'HTTP Request: GET https://www.googleapis.com/customsearch/v1 "HTTP/1.1 200 OK"'
        )
        assert "SECRET" not in out.getvalue()
        assert (
            sum(
                isinstance(f, log_schema.HttpxUrlFilter) for f in logging.getLogger("httpx").filters
            )
            == 1
        )

    def test_log_request_helper(self, monkeypatch, capsys):
        monkeypatch.setenv("APP_LOG_DIR", "")
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("APP_LOG_LEVEL", "info")
        from app.core.logging import configure_logging, get_logger, log_request

        configure_logging("ap-admin-api", access_log=False)
        log = get_logger("admin_api.main")
        log_request(log, method="GET", path="/health", status=200, duration_ms=1)
        log_request(
            log,
            method="PATCH",
            path="/api/v1/users/u1?code=x",
            status=204,
            duration_ms=12,
            client_ip="203.0.113.9",
        )
        (event,) = (json.loads(line) for line in capsys.readouterr().out.strip().splitlines())
        assert event["message"] == "PATCH /api/v1/users/u1 204"
        assert event["path"] == "/api/v1/users/u1"
        assert event["status"] == 204 and event["duration_ms"] == 12
        assert event["client_ip"] == "203.0.113.9"
        assert event["service"] == "ap-admin-api"


# ---------------------------------------------------------------------------
# The log file
# ---------------------------------------------------------------------------


def _ts(y, m, d, hh=12, mm=0, ss=0) -> float:
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC).timestamp()


class FakeClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _handler(tmp_path, clock, **kw) -> tuple[ServiceRotatingFileHandler, logging.Logger]:
    h = ServiceRotatingFileHandler(tmp_path, "svc", clock=clock, **kw)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger(f"tests.rotation.{id(h)}")
    lg.handlers[:] = [h]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    return h, lg


def _gz(path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.read()


def _names(tmp_path) -> list[str]:
    return sorted(os.listdir(tmp_path))


class TestRotation:
    def test_size_rollover_twice_in_one_day(self, tmp_path):
        clock = FakeClock(_ts(2026, 10, 23, 9))
        h, lg = _handler(tmp_path, clock, max_bytes=130)
        lines = [f"line {i:02d} " + "x" * 50 for i in range(6)]  # 59 bytes each with \n: 2 a file
        for line in lines:
            lg.info(line)
            clock.t += 1
        h.close()
        assert _names(tmp_path) == ["svc.2026-10-23.0.log.gz", "svc.2026-10-23.1.log.gz", "svc.log"]
        assert _gz(tmp_path / "svc.2026-10-23.0.log.gz") == "".join(f"{x}\n" for x in lines[0:2])
        assert _gz(tmp_path / "svc.2026-10-23.1.log.gz") == "".join(f"{x}\n" for x in lines[2:4])
        assert (tmp_path / "svc.log").read_text() == "".join(f"{x}\n" for x in lines[4:6])

    def test_rotation_never_gets_stuck(self, tmp_path):
        """The old handler named every rotation of a day the same and gave up on the second."""
        clock = FakeClock(_ts(2026, 10, 23, 9))
        h, lg = _handler(tmp_path, clock, max_bytes=100)
        for i in range(40):
            lg.info("%02d %s", i, "y" * 45)  # 49 bytes: 2 a file
        h.close()
        assert os.path.getsize(tmp_path / "svc.log") <= 100
        archives = [n for n in _names(tmp_path) if n.endswith(".gz")]
        assert len(archives) == 19
        assert all(len(_gz(tmp_path / n)) <= 100 for n in archives)
        # n is numeric: 10 follows 9.
        assert "svc.2026-10-23.10.log.gz" in archives and "svc.2026-10-23.18.log.gz" in archives

    def test_midnight_rollover_names_the_day_that_ended(self, tmp_path):
        clock = FakeClock(_ts(2026, 10, 23, 23, 59, 58))
        h, lg = _handler(tmp_path, clock, max_bytes=10_000)
        lg.info("before midnight")
        clock.t = _ts(2026, 10, 24, 0, 0, 1)
        lg.info("after midnight")
        h.close()
        assert _names(tmp_path) == ["svc.2026-10-23.0.log.gz", "svc.log"]
        assert _gz(tmp_path / "svc.2026-10-23.0.log.gz") == "before midnight\n"
        assert (tmp_path / "svc.log").read_text() == "after midnight\n"

    def test_midnight_after_size_rollovers_takes_the_next_n(self, tmp_path):
        clock = FakeClock(_ts(2026, 10, 23, 22))
        h, lg = _handler(tmp_path, clock, max_bytes=120)
        for i in range(6):
            lg.info("%d %s", i, "z" * 55)  # 58 bytes: 2 a file
        clock.t = _ts(2026, 10, 24, 0, 0, 5)
        lg.info("new day")
        for i in range(2):
            lg.info("%d %s", i, "w" * 55)
        h.close()
        assert _names(tmp_path) == [
            "svc.2026-10-23.0.log.gz",
            "svc.2026-10-23.1.log.gz",
            "svc.2026-10-23.2.log.gz",
            "svc.2026-10-24.0.log.gz",
            "svc.log",
        ]
        assert _gz(tmp_path / "svc.2026-10-23.2.log.gz").startswith("4 ")
        assert _gz(tmp_path / "svc.2026-10-24.0.log.gz").startswith("new day\n")

    def test_empty_file_is_not_archived_at_midnight(self, tmp_path):
        clock = FakeClock(_ts(2026, 10, 23, 23))
        h, lg = _handler(tmp_path, clock)
        clock.t = _ts(2026, 10, 25, 1)
        lg.info("first ever")
        h.close()
        assert _names(tmp_path) == ["svc.log"]

    def test_a_file_left_by_an_earlier_run_is_archived_under_its_day(self, tmp_path):
        old = tmp_path / "svc.log"
        old.write_text("from the last run\n")
        os.utime(old, (_ts(2026, 10, 20, 18), _ts(2026, 10, 20, 18)))
        clock = FakeClock(_ts(2026, 10, 23, 8))
        h, lg = _handler(tmp_path, clock)
        lg.info("restarted")
        h.close()
        assert _gz(tmp_path / "svc.2026-10-20.0.log.gz") == "from the last run\n"
        assert old.read_text() == "restarted\n"

    def test_retention_by_age(self, tmp_path):
        for name in [
            "svc.2026-09-12.0.log.gz",  # 41 days before 10-23
            "svc.2026-09-22.4.log.gz",  # 31 days
            "svc.2026-09-23.0.log.gz",  # 30 days: kept
            "svc.2026-10-18.0.log.gz",
            # Not this handler's: left alone.
            "pacod-agent.log.2026-01-01.tar.gz",
            "other.2026-01-01.0.log.gz",
            "svc.2026-01-01.0.log.gz.tmp",
        ]:
            (tmp_path / name).write_bytes(b"x")
        clock = FakeClock(_ts(2026, 10, 23, 23, 59))
        h, lg = _handler(tmp_path, clock)
        lg.info("a")
        clock.t = _ts(2026, 10, 24, 0, 1)  # cutoff is now 2026-09-24
        lg.info("b")
        h.close()
        assert _names(tmp_path) == [
            "other.2026-01-01.0.log.gz",
            "pacod-agent.log.2026-01-01.tar.gz",
            "svc.2026-01-01.0.log.gz.tmp",
            "svc.2026-10-18.0.log.gz",
            "svc.2026-10-23.0.log.gz",
            "svc.log",
        ]

    def test_retention_by_age_keeps_thirty_days(self, tmp_path):
        (tmp_path / "svc.2026-09-23.0.log.gz").write_bytes(b"x")
        (tmp_path / "svc.2026-09-22.0.log.gz").write_bytes(b"x")
        clock = FakeClock(_ts(2026, 10, 23, 10))
        h, lg = _handler(tmp_path, clock, max_bytes=5)
        lg.info("123456")
        lg.info("rotate")
        h.close()
        names = _names(tmp_path)
        assert "svc.2026-09-23.0.log.gz" in names
        assert "svc.2026-09-22.0.log.gz" not in names

    def test_retention_by_total_size_oldest_first(self, tmp_path):
        # 100 bytes each, oldest first by (day, numeric n): 10-20.9 is older than 10-20.10.
        for name in [
            "svc.2026-10-19.0.log.gz",
            "svc.2026-10-20.9.log.gz",
            "svc.2026-10-20.10.log.gz",
            "svc.2026-10-21.0.log.gz",
        ]:
            (tmp_path / name).write_bytes(os.urandom(100))
        clock = FakeClock(_ts(2026, 10, 23, 10))
        h, lg = _handler(tmp_path, clock, max_bytes=5, max_total_bytes=300)
        lg.info("123456")
        lg.info("rotate")  # archives svc.2026-10-23.0.log.gz (a few dozen bytes)
        h.close()
        assert _names(tmp_path) == [
            "svc.2026-10-20.10.log.gz",
            "svc.2026-10-21.0.log.gz",
            "svc.2026-10-23.0.log.gz",
            "svc.log",
        ]

    def test_configure_writes_json_to_service_log(self, tmp_path):
        out = io.StringIO()
        log_schema.configure(
            service="ap-api",
            level=logging.INFO,
            fmt="text",
            log_dir=str(tmp_path / "logs"),
            stream=out,
        )
        logging.getLogger("app.x").info("to both", extra={"k": 1})
        for h in logging.getLogger().handlers:
            h.flush()
        assert out.getvalue().split(" ", 2)[1] == "INFO"  # text on stdout
        event = json.loads((tmp_path / "logs" / "ap-api.log").read_text())  # JSON in the file
        assert event["message"] == "to both" and event["k"] == 1 and event["service"] == "ap-api"
